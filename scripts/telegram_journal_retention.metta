; Archive old received Telegram updates out of a channel journal, then remove
; them, through the engine's own durable store.
;
;   cetta --lang petta --import-mode relative scripts/telegram_journal_retention_main.metta \
;     --journal JOURNAL --archive DIRECTORY [--keep 150] [--cetta BIN] [--unit UNIT ...]
;
; The store has one writer, so the channel service must be stopped during a
; run: the units named that are active are stopped for the run and started
; again afterwards, whatever the outcome.
;
; 1. Read host.received and host.inbox through `cetta --durable-admin` (BIN,
;    else this program's own engine). Records come in the order of their last
;    mutation.
; 2. Select the received records except the newest --keep, and never one whose
;    update, or a later one, still has a pending host.inbox entry.
; 3. Write them to a content-addressed .jsonl.gz archive, one line a record
;    with its key and its stored record as the store prints it; gzip it, read
;    it back and check its digest and record count.
; 4. Append one line to the archive manifest, an empty pass included.
; 5. Remove exactly the archived records in one administrative commit, then
;    checkpoint the journal so the file gives back their space.
;
; Received records are needed only while their update is being processed, and
; the poll cursor keeps Telegram from delivering old updates again, so removing
; old processed records changes no delivery.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ../src/state_file_cetta.metta)

(= (jr:log $text) (system:write stderr (str:concat $text "\n")))

;; ------------------------------------------------------------ the options

; (option NAME VALUE) and (unit NAME) from the arguments after the program.
(= (jr:options $args)
   (if (== $args ()) ()
     (let* (($flag (car-atom $args)) ($rest (cdr-atom $args)))
       (if (== $rest ()) (JrBadOption $flag)
         (let* (($value (car-atom $rest)) ($more (jr:options (cdr-atom $rest))))
           (case $more
             (((JrBadOption $bad) (JrBadOption $bad))
              ($parsed (cons-atom (jr:option $flag $value) $parsed)))))))))
(= (jr:option $flag $value) (if (== $flag "--unit") (JrUnit $value) (JrOption $flag $value)))
(= (jr:get $options $flag $default)
   (if (== $options ()) $default
     (case (car-atom $options)
       (((JrOption $flag $value) $value)
        ($other (jr:get (cdr-atom $options) $flag $default))))))
(= (jr:units $options)
   (if (== $options ()) ()
     (let $rest (jr:units (cdr-atom $options))
       (case (car-atom $options) (((JrUnit $unit) (cons-atom $unit $rest)) ($other $rest))))))

;; ------------------------------------------------------------ the store

; A literal administrative script beside the journal, run with --durable-admin.
(= (jr:admin $cetta $journal $directives)
   (let* (($dir (path:dirname $journal))
          ($script (path:join $dir (str:concat ".retention-" (str:concat (system:uuid4-hex) ".metta"))))
          ($text (str:join "\n" (cons-atom "!(import! &self durable)"
                                  (cons-atom (format-args "!(durable:open {})" ((repr (sf:basename $journal)))) $directives))))
          ($_ (fs:write $script (str:concat $text "\n!(durable:close)\n")))
          ($run (proc:run ($cetta "--durable-admin" $script) $dir inherit 1800000 0))
          ($_ (fs:remove $script)))
     (case $run
       (((proc:exited 0 $out $err) $out)
        ($failed (Error jr:admin (repr $failed)))))))

; The results the reads printed after the open: each starts a line with
; (durable:snapshot "; a printed string escapes its quotes, so that text
; cannot begin inside one.
(= (jr:results $out) (cdr-atom (str:split "\n(durable:snapshot \"" (str:concat "\n" $out))))
; EPOCH, REVISION and the record texts of one read of SPACE.
(= (jr:snapshot $result $space)
   (let* (($epoch (car-atom (str:split "\"" $result)))
          ($after (str:byte-slice $result (+ (str:byte-length $epoch) 2) (str:byte-length $result)))
          ($revision (str:string->number (car-atom (str:split " " $after))))
          ($pieces (cdr-atom (str:split (str:concat "(durable:record " (str:concat (repr $space) " \"")) $result))))
     (JrSnapshot $epoch $revision (jr:records $pieces $space))))
; (KEY TEXT) of each record: the key up to its closing quote, the text the
; whole record as printed.
(= (jr:records $pieces $space)
   (if (== $pieces ()) ()
     (let* (($piece (car-atom $pieces)) ($rest (cdr-atom $pieces))
            ($key (car-atom (str:split "\"" $piece)))
            ($body (if (== $rest ()) (jr:last-record $piece) (str:trim $piece)))
            ($text (str:concat "(durable:record " (str:concat (repr $space) (str:concat " \"" $body))))
            ($tail (jr:records $rest $space)))
       (cons-atom ($key $text) $tail))))
; The last record of a read ends the record list and the snapshot as well.
(= (jr:last-record $piece)
   (let $trimmed (str:trim (car-atom (str:split "\n(durable:closed)" $piece)))
     (str:byte-slice $trimmed 0 (- (str:byte-length $trimmed) 2))))

(= (jr:read $cetta $journal)
   (case (jr:admin $cetta $journal ("!(durable:read \"host.received\")" "!(durable:read \"host.inbox\")"))
     (((Error $where $why) (Error $where $why))
      ($out
       (let $results (jr:results $out)
         (if (not (== (size-atom $results) 3)) (Error jr:read (format-args "expected three snapshots, found {}" ((size-atom $results))))
           (let* (((JrSnapshot $epoch $revision $received) (jr:snapshot (car-atom (cdr-atom $results)) "host.received"))
                  ((JrSnapshot $e2 $r2 $inbox) (jr:snapshot (car-atom (cdr-atom (cdr-atom $results))) "host.inbox")))
             (JrRead $epoch $revision $received (map-atom $inbox $entry (car-atom $entry))))))))))

;; ------------------------------------------------------------ what goes

; The update number a key ends with, or none.
(= (jr:update $key)
   (case (catch (str:string->number (jr:last (str:split "/" $key))))
     (((Error $where $why) none) ($n (if (== (get-type $n) Number) $n none)))))
(= (jr:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (jr:last (cdr-atom $items))))
(= (jr:take $items $n) (if (or (== $items ()) (<= $n 0)) () (cons-atom (car-atom $items) (jr:take (cdr-atom $items) (- $n 1)))))
(= (jr:min-update $keys $best)
   (if (== $keys ()) $best
     (let $n (jr:update (car-atom $keys))
       (jr:min-update (cdr-atom $keys) (if (== $n none) $best (if (== $best none) $n (if (< $n $best) $n $best)))))))

; The records to archive: all but the newest KEEP; while an update waits in
; the inbox, only those before the oldest waiting one.
(= (jr:candidates $received $pending $keep)
   (let* (($old (jr:take $received (- (size-atom $received) $keep)))
          ($first (jr:min-update $pending none)))
     (if (== $first none) $old
       (filter-atom $old $record
         (let ($key $text) $record (let $n (jr:update $key) (if (== $n none) False (< $n $first))))))))

;; ------------------------------------------------------------ the archive

(= (jr:line ($key $text))
   (json:stringify-python-with (JsonObject ((JsonPair "key" $key) (JsonPair "record" $text) (JsonPair "space" "host.received")))
                               ((sort-keys True))))
(= (jr:fsync $path)
   (let $reader (fs:open-read $path)
     (if (sf:failed? $reader) $reader (let* (($synced (fs:sync $reader)) ($_ (fs:close $reader))) $synced))))

; (JrArchived FILE DIGEST BYTES), or an Error. The body is written whole, gzip
; packs it without a name or time, the packed file is synced and renamed into
; place, and its unpacked bytes are checked.
(= (jr:write-archive $directory $records)
   (let* (($body (str:concat (str:join "\n" (map-atom $records $record (jr:line $record))) "\n"))
          ($digest (str:sha256 $body))
          ($_ (fs:make-directories $directory 448))
          ($target (path:join $directory (str:join "" ("received-" (str:byte-slice $digest 0 16) ".jsonl.gz"))))
          ($plain (path:join $directory (str:concat ".received-" (system:uuid4-hex))))
          ($wrote (sf:replace $plain $body 384 True))
          ($packed (proc:run ("gzip" "-n" "-9" $plain) $directory inherit 600000 65536))
          ($_ (jr:fsync (str:concat $plain ".gz")))
          ($moved (fs:rename (str:concat $plain ".gz") $target))
          ($_ (sf:sync-directory $directory))
          ($back (proc:run ("gzip" "-dc" $target) $directory inherit 600000 0)))
     (case $back
       (((proc:exited 0 $text $err)
         (if (and (== (str:sha256 $text) $digest) (== (size-atom (cdr-atom (str:split "\n" $text))) (size-atom $records)))
           (JrArchived (sf:basename $target) $digest (str:byte-length $body))
           (Error jr:write-archive "archive read-back does not match")))
        ($failed (Error jr:write-archive (repr ($wrote $packed $moved $failed))))))))

(= (jr:now)
   (let $stamp (system:format-time "%Y-%m-%dT%H:%M:%S%z" (system:wall-time) local)
     (let $n (str:byte-length $stamp)
       (str:join "" ((str:byte-slice $stamp 0 (- $n 2)) ":" (str:byte-slice $stamp (- $n 2) $n))))))
(= (jr:manifest $directory $pairs)
   (let* (($_ (fs:make-directories $directory 448))
          ($path (path:join $directory "MANIFEST.jsonl"))
          ($writer (fs:open-write $path ("create" "append") 420)))
     (if (sf:failed? $writer) $writer
       (let* (($wrote (fs:write-all $writer (str:concat (json:stringify-python-with (JsonObject $pairs) ((sort-keys True))) "\n")))
              ($synced (fs:sync $writer)) ($_ (fs:close $writer)))
         (if (sf:failed? $wrote) $wrote $synced)))))

;; ------------------------------------------------------------ the run

(= (jr:remove $cetta $journal $epoch $revision $records)
   (let* (($ops (str:join " " (map-atom $records $record
                   (let ($key $text) $record (format-args "(durable:remove \"host.received\" {})" ((repr $key)))))))
          ($out (jr:admin $cetta $journal ((format-args "!(durable:commit {} {} ({}))" ((repr $epoch) $revision $ops))
                                           "!(durable:checkpoint)"))))
     (case $out
       (((Error $where $why) (Error $where $why))
        ($text (case (str:find $text "(durable:committed ")
                 (((TextNotFound $needle) (Error jr:remove "removal was not committed"))
                  ($at done))))))))

(= (jr:run $options)
   (let* (($journal (jr:get $options "--journal" ""))
          ($archive (jr:get $options "--archive" ""))
          ($keep (str:string->number (jr:get $options "--keep" "150")))
          ($cetta (jr:get $options "--cetta" (system:executable))))
     (if (or (== $journal "") (== $archive "")) (Error jr:run "--journal and --archive are required")
       (case (jr:read $cetta $journal)
         (((Error $where $why) (Error $where $why))
          ((JrRead $epoch $revision $received $pending)
           (let* (($old (jr:candidates $received $pending $keep))
                  ($name (sf:basename $journal)))
             (if (== $old ())
               (let $_ (jr:manifest $archive ((JsonPair "empty" True) (JsonPair "journal" $name) (JsonPair "records" 0) (JsonPair "time" (jr:now))))
                 "nothing to archive")
               (case (jr:write-archive $archive $old)
                 (((JrArchived $file $digest $bytes)
                   (let* (($_ (jr:manifest $archive
                                ((JsonPair "bytes" $bytes) (JsonPair "file" $file) (JsonPair "first" (car-atom (car-atom $old)))
                                 (JsonPair "journal" $name) (JsonPair "last" (car-atom (jr:last $old)))
                                 (JsonPair "records" (size-atom $old)) (JsonPair "revision" $revision)
                                 (JsonPair "sha256" $digest) (JsonPair "time" (jr:now)))))
                          ($removed (jr:remove $cetta $journal $epoch $revision $old)))
                     (case $removed
                       (((Error $w2 $y2) (Error $w2 $y2))
                        ($ok (format-args "archived and removed {} received records to {}; {} remain; journal now {} bytes"
                               ((size-atom $old) $file (- (size-atom $received) (size-atom $old)) (jr:size $journal))))))))
                  ($failed $failed)))))))))))
(= (jr:size $path) (case (fs:metadata $path follow) (((fs:info regular $size $identity) $size) ($other unknown))))

; The units that are active are stopped for the run and started again after it.
(= (jr:active? $unit)
   (case (proc:run ("systemctl" "--user" "is-active" "--quiet" $unit) (system:cwd) inherit 30000 65536)
     (((proc:exited 0 $out $err) True) ($other False))))
(= (jr:systemctl $verb $units)
   (if (== $units ()) True
     (case (proc:run (cons-atom "systemctl" (cons-atom "--user" (cons-atom $verb $units))) (system:cwd) inherit 120000 65536)
       (((proc:exited 0 $out $err) True) ($failed (Error jr:systemctl (repr ($verb $failed))))))))
; The arguments arrive as symbols (or numbers); the options are their text.
(= (jr:text $x) (if (== (get-type $x) String) $x (repr $x)))
(= (jr:main $args)
   (case (jr:options (map-atom $args $a (jr:text $a)))
     (((JrBadOption $flag) (Error jr:main (str:concat "an option without its value: " $flag)))
      ($options
       (let* (($active (filter-atom (jr:units $options) $unit (jr:active? $unit)))
              ($stopped (jr:systemctl "stop" $active))
              ($result (if (== $stopped True) (catch (jr:run $options)) $stopped))
              ($started (jr:systemctl "start" $active)))
         (if (== $started True) $result (JrNotRestarted $started $result)))))))
