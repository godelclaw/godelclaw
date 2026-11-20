; Appends into the agent's history that a mode has not settled. A mode writes
; each history record through a durable intent: the record names the file's
; identity, the offset and the payload, and its owner completes it by offset
; and prefix when it next starts. Until its whole payload stands at its offset
; the bytes from there may be torn:
; - a reader of other loops' records stops before them, and what follows joins
;   once the payload is whole;
; - a writer first completes a torn payload from the record, so its own record
;   never lands behind a partial one and the owner's recovery still finds its
;   bytes; writing the rest of a recorded payload runs no tool again. A
;   completion counts once every byte is written and synced. A payload that
;   stands whole but unsettled is synced before a writer goes past it: its
;   bytes alone do not show they are durable;
; - an append that can never complete (other bytes at its place, a place past
;   the file's end, no payload to check, or a payload that does not match its
;   recorded digest) is written by no one: readers stop before it until its
;   owner abandons it when it next runs, and a writer reports it and goes on,
;   since waiting could recover nothing;
; - a record that cannot be read decides nothing: the append it names may
;   still be completed in place once it reads again, so readers stop at their
;   cursor and a writer refuses, which leaves that prefix recoverable;
; - a new record starts on a line of its own: after an append that was
;   abandoned with part of it written, the file ends inside a line, and the
;   next record begins with a line break, so it reads as a record of its own
;   while the fragment stays as it was.
!(import! &self (library str))
!(import! &self (library list))
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library json))
!(import! &self (library system))

(= (history-pending:records) ("native-checkpoint-v1.json" "agent-history-intent.json"))
(= (history-pending:member $pairs $key)
   (let $found (filter-atom $pairs $pair (case $pair (((JsonPair $name $_) (== $name $key)) ($other False))))
     (if (== $found ()) absent (case (car-atom $found) (((JsonPair $_ $value) $value))))))
(= (history-pending:text $value)
   (case $value (((JsonString $text) $text) ($other (if (== (get-type $other) String) $other "")))))
; A file identity as text: the main loop's records quote it, the modes' do not.
(= (history-pending:bare $text)
   (let $length (str:byte-length $text)
     (if (and (>= $length 2) (and (str:starts-with $text "\"") (str:ends-with $text "\"")))
       (str:byte-slice $text 1 (- $length 1)) $text)))
(= (history-pending:number $value)
   (case $value
     (((JsonNumber $text) (str:string->number $text))
      ($other (if (== (get-type $other) Number) $other -1)))))

; The bytes [<offset>, <offset>+<count>) of <path>, or an Error.
(= (history-pending:read-at $path $offset $count)
   (if (== $count 0) ""
     (case (fs:open-read $path)
       (((NativeHandle $kind $id)
         (let* (($owner (NativeHandle $kind $id))
                ($read (history-pending:read-owned $owner $offset $count ()))
                ($_ (fs:close $owner)))
           $read))
        ($bad (Error history-pending (Incomplete unreadable)))))))
(= (history-pending:read-owned $owner $offset $left $reverse)
   (if (== $left 0) (str:join "" (list:reverse $reverse))
     (case (fs:read-chunk $owner (min 65536 $left) $offset)
       (((fs:chunk $bytes $_)
         (let $count (str:byte-length $bytes)
           (if (or (== $count 0) (> $count $left)) (Error history-pending (Incomplete short-read))
             (history-pending:read-owned $owner (+ $offset $count) (- $left $count) (cons-atom $bytes $reverse)))))
        ($bad (Error history-pending (Incomplete unreadable)))))))

; A recorded append, as its bytes stand in a file of <size> bytes:
; AppendCompleteV1 when its whole payload is at its offset; (AppendTornV1
; <offset> <rest>) when the bytes from its offset to the end are a proper
; prefix of its payload, <rest> the bytes still to write; (AppendForeignV1
; <offset>) when its place no longer holds its start: other bytes are there,
; or it names no place in the file; (AppendUncheckedV1 <offset>) when it has
; no payload to compare; unknown when the bytes cannot be read.
(= (history-pending:bytes-state $history $offset $payload $size)
   (let* (($length (str:byte-length $payload)) ($progress (- $size $offset)))
     (if (== $length 0) (AppendUncheckedV1 $offset)
     (if (or (< $offset 0) (< $progress 0)) (AppendForeignV1 $offset)
       (if (>= $progress $length)
         (case (history-pending:read-at $history $offset $length)
           (((Error $_ $_) unknown)
            ($text (if (== $text $payload) AppendCompleteV1 (AppendForeignV1 $offset)))))
         (case (history-pending:read-at $history $offset $progress)
           (((Error $_ $_) unknown)
            ($text (if (== $text (str:byte-slice $payload 0 $progress))
                     (AppendTornV1 $offset (str:byte-slice $payload $progress $length))
                     (AppendForeignV1 $offset))))))))))

; A mode's record about the history <history> (identity <device> <inode>, now
; <size> bytes): none when it is absent, settled, abandoned or about another
; file; (PendingV1 <offset> <state>) while it is pending, its state
; AppendCompleteV1 once its payload stands whole; unknown when it or the bytes
; it names cannot be read.
(= (history-pending:record $path $history $device $inode $size)
   (case (fs:read-text-result $path 0 "raw")
     ((("missing" $_) none)
      ; An entry of the modes directory that is a file names no record.
      (("error" "NotADirectoryError" $_) none)
      (("ok" $text)
       (case (catch (json:parse $text))
         (((JsonObject $pairs)
           (let* (($status (history-pending:text (history-pending:member $pairs "status")))
                  ($operation (history-pending:text (history-pending:member $pairs "operation")))
                  ($same (and (== (history-pending:bare (history-pending:text (history-pending:member $pairs "device")))
                                  (history-pending:bare $device))
                              (== (history-pending:bare (history-pending:text (history-pending:member $pairs "inode")))
                                  (history-pending:bare $inode)))))
             (if (not (and (and (== $status "pending") $same) (or (== $operation "") (== $operation "append")))) none
               (let* (($offset (history-pending:number (history-pending:member $pairs "offset")))
                      ($payload (history-pending:text (history-pending:member $pairs "payload")))
                      ($digest (history-pending:text (history-pending:member $pairs "sha256"))))
                 ; A payload that does not match its recorded digest is not
                 ; verified: nothing is completed from it and readers stop.
                 (if (or (== $digest "") (!= (str:sha256 $payload) $digest)) (PendingV1 $offset (AppendUncheckedV1 $offset))
                   (case (history-pending:bytes-state $history $offset $payload $size)
                     ((unknown unknown)
                      ($state (PendingV1 $offset $state)))))))))
          ($bad unknown))))
      ($unavailable unknown))))
; The modes' state directories: none without a root or when the directory is
; known to be missing; unknown when it cannot be observed or listed, which
; authorizes no reading and no writing.
(= (history-pending:modes $root)
   (if (== $root "") ()
     (case (fs:metadata $root follow-observed)
       (((fs:failed fs:missing $_ $_ $_ $_ $_) ())
        ((fs:failed $_ $_ $_ $_ $_ $_) unknown)
        ($found
          (case (catch (fs:entries $root))
            (((Error $_ $_) unknown)
             ($names (if (== (get-metatype $names) Expression) $names unknown)))))))))
; Every mode's append into the history that is not whole: a list of
; (PendingV1 <offset> <state>), or unknown when something could not be read.
(= (history-pending:all $root $history $device $inode $size)
   (let $modes (history-pending:modes $root)
     (if (== $modes unknown) unknown
       (foldl-atom $modes () $found $mode
         (foldl-atom (history-pending:records) $found $acc $name
           (if (== $acc unknown) unknown
             (case (history-pending:record (path:join (path:join $root $mode) $name) $history $device $inode $size)
               ((none $acc)
                (unknown unknown)
                ($pending (cons-atom $pending $acc))))))))))
; The end up to which the history of <size> bytes is settled for readers:
; <size>, or the least offset of an append that is not whole (a whole one reads
; as settled); unknown when something could not be read.
(= (history-pending:end $root $history $device $inode $size)
   (let $all (history-pending:all $root $history $device $inode $size)
     (if (== $all unknown) unknown
       (foldl-atom $all $size $least $pending
         (case $pending
           (((PendingV1 $_ AppendCompleteV1) $least)
            ((PendingV1 $offset $state) (if (and (>= $offset 0) (< $offset $least)) $offset $least))))))))

; Before a writer appends to the history: True once every mode's recorded
; append into it stands whole, a torn one completed from its record; else
; (Error history-pending <why>), and the writer writes nothing.
(= (history-pending:complete $root $history $device $inode)
   (case (fs:metadata $history follow-observed)
     (((fs:failed fs:missing $_ $_ $_ $_ $_) True)
      ((fs:info-v2 $_ $size $_ $_ $_ $_ $_)
       (let $all (history-pending:all $root $history $device $inode $size)
         (if (== $all unknown) (Error history-pending (Incomplete mode-record-unread))
           (history-pending:complete-each $root $history $device $inode $all))))
      ($bad (Error history-pending (Incomplete history-unobserved))))))
(= (history-pending:complete-each $root $history $device $inode $all)
   (if (== $all ()) True
     (case (car-atom $all)
       (((PendingV1 $_ (AppendTornV1 $_ $rest))
         (let $written (history-pending:append $history $rest)
           (if (!= $written True) $written
             ; Completed: every record is read again against the grown file.
             (history-pending:complete $root $history $device $inode))))
        ; Whole but not settled: synced before anything is written after it.
        ((PendingV1 $_ AppendCompleteV1)
         (let $synced (history-pending:sync $history)
           (if (!= $synced True) $synced
             (history-pending:complete-each $root $history $device $inode (cdr-atom $all)))))
        ((PendingV1 $_ (AppendForeignV1 $at))
         (let $_ (system:write stderr (str:concat "[history] a recorded append at "
                   (str:concat (repr $at) " was lost before: its place holds other bytes or lies past the end; its owner abandons it\n")))
           (history-pending:complete-each $root $history $device $inode (cdr-atom $all))))
        ((PendingV1 $_ (AppendUncheckedV1 $at))
         (let $_ (system:write stderr (str:concat "[history] a recorded append at "
                   (str:concat (repr $at) " cannot be verified: no payload, or one that fails its digest; nothing is written from it, and its owner abandons it\n")))
           (history-pending:complete-each $root $history $device $inode (cdr-atom $all))))
        ($other (history-pending:complete-each $root $history $device $inode (cdr-atom $all)))))))
;; <bytes> appended to <path>: True once every byte is written and synced; else
;; an Error, after one attempt.
(= (history-pending:append $path $bytes)
   (case (fs:open-write $path ("append") 384)
     (((NativeHandle $kind $id)
       (let* (($owner (NativeHandle $kind $id))
              ($written (fs:write-all $owner $bytes))
              ($whole (case $written (((fs:written $count) (== $count (str:byte-length $bytes))) ($failed False))))
              ($synced (if $whole (fs:sync $owner) unsynced))
              ($_ (fs:close $owner)))
         (if (== $synced fs:synced) True
           (Error history-pending (Incomplete (completion-unconfirmed (if $whole sync write)))))))
      ($bad (Error history-pending (Incomplete completion-not-opened))))))

; <payload> as a new record of <history>, on a line of its own: a file that
; ends inside a line (an abandoned append's fragment) gets a line break first,
; and the fragment's place is reported. A missing or empty file, or one that
; ends with a line break, takes the payload as it is.
(= (history-pending:on-own-line $history $payload)
   (case (fs:metadata $history follow-observed)
     (((fs:info-v2 $_ $size $_ $_ $_ $_ $_)
       (if (== $size 0) $payload
         (if (== (history-pending:read-at $history (- $size 1) 1) "\n") $payload
           (let $_ (system:write stderr (str:concat "[history] the history ends inside a line at byte "
                     (str:concat (repr $size) ": an abandoned append's fragment stays as it is; the next record starts on a new line\n")))
             (str:concat "\n" $payload)))))
      ($absent $payload))))

; <path>'s bytes synced: True, or an Error when fsync did not confirm them.
(= (history-pending:sync $path)
   (case (fs:open-read $path)
     (((NativeHandle $kind $id)
       (let* (($owner (NativeHandle $kind $id)) ($synced (fs:sync $owner)) ($_ (fs:close $owner)))
         (if (== $synced fs:synced) True (Error history-pending (Incomplete sync-unconfirmed)))))
      ($bad (Error history-pending (Incomplete sync-not-opened))))))

; Bytes read from inside a character, as a tail read decodes them: each byte
; of the cut character becomes one U+FFFD, the rest is unchanged.
(= (history-pending:as-tail-text $bytes)
   (history-pending:as-tail-text-from $bytes 0))
(= (history-pending:as-tail-text-from $bytes $skip)
   (let $rest (str:byte-slice $bytes $skip (str:byte-length $bytes))
     (if (or (>= $skip 3) (== (str:utf8-error $rest) StrUtf8WellFormedV1))
       (str:concat (str:join "" (history-pending:replacements $skip)) $rest)
       (history-pending:as-tail-text-from $bytes (+ $skip 1)))))
(= (history-pending:replacements $count)
   (if (<= $count 0) () (cons-atom (str:from-codepoints (65533)) (history-pending:replacements (- $count 1)))))
