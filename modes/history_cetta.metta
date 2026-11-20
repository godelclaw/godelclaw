; The agent's one conversation history, shared by the framework loop and every
; supervised mode: its path, plain durable appends, a mode's catch-up from its
; own cursor, and timestamp windows that do not read a large file whole.
!(import! &self ./runtime_host_cetta.metta)
!(import! &self ../src/unicode_casefold_cetta.metta)

; Supervised modes use the agent's history; a terminal launch has none.
(= (mode-history:path)
   (if (mode-host:standalone?) "" (system:getenv-or-default "METTACLAW_HISTORY_PATH" "")))
(= (mode-history:shared?) (!= (mode-history:path) ""))
(= (mode-history:size $path)
   (case (mode-host:file-info $path) (((ModeFileV1 $size $_ $_) $size) ($missing 0))))
(= (mode-history:stamp)
   (system:format-time "%Y-%m-%d %H:%M:%S" (math:floor (system:wall-time)) "local"))

(= (mode-history:report $failure)
   (let $_ (system:write stderr (str:concat "[mode] agent history append error: " (str:concat (diagnostic:text $failure 400) "\n")))
     False))

; A mode's cursor is the history size up to which it has accounted for the
; file: its own records, and the other loops' records it has caught up on. It
; is a place in one file: a file with another identity starts it again.
(= (mode-history:cursor-path) (path:join (mode-host:root) "agent-history-cursor.json"))
; A small state file of the mode's, read three ways: none when it does not
; exist; its object when it reads as one; unknown when it cannot be read or is
; not an object. Only a file known to be missing is no record.
(= (mode-history:object-at $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("missing" $_) none)
      (("ok" $text) (case (catch (json:parse $text)) (((JsonObject $pairs) (JsonObject $pairs)) ($bad unknown))))
      ($unavailable unknown))))
; The cursor's offset in the current file; -1 without one; unknown when the
; cursor cannot be read.
(= (mode-history:cursor)
   (case (mode-history:object-at (mode-history:cursor-path))
     ((none -1) (unknown unknown)
      ($record
        (let $identity (mode-history:identity)
          (if (and (== (sf:get $record "path" "") (mode-history:path))
                   (or (or (== (sf:get $record "device" "") "") (== $identity unknown))
                       (== ((sf:get $record "device" "") (sf:get $record "inode" "")) $identity)))
            (sf:get $record "size" -1) -1))))))
(= (mode-history:mark-at $size)
   (let $identity (case (mode-history:identity) ((none ("" "")) (unknown ("" "")) ($pair $pair)))
     (sf:replace (mode-history:cursor-path)
       (turn:json (JsonObject ((JsonPair "path" (mode-history:path)) (JsonPair "size" $size)
                               (JsonPair "device" (index-atom $identity 0)) (JsonPair "inode" (index-atom $identity 1)))))
       384 True)))

; A mode's own record is a durable intent before it is written: its text, the
; offset and identity of the file, and the frontier the record brings the mode
; to. A pending intent is completed exactly once, by offset and prefix, with
; mode-host:resume-append; a restart completes it before anything new is
; written, and no action of the mode runs again. A file replaced, cut, or
; grown with other bytes at the offset abandons the intent: its messages stay
; unrecorded and are written anew.
(= (mode-history:intent-path) (path:join (mode-host:root) "agent-history-intent.json"))
; The mode's last own record: none when there is none; the record when it
; reads as one with a known status; unknown otherwise, which decides nothing.
(= (mode-history:intent)
   (case (mode-history:object-at (mode-history:intent-path))
     ((none none) (unknown unknown)
      ($record (if (erv:contains ("pending" "settled" "abandoned") (sf:get $record "status" "")) $record unknown)))))
(= (mode-history:write-intent $record)
   (sf:replace (mode-history:intent-path) (turn:json $record) 384 True))
; The history file's identity, as an intent records it; none when it does not
; exist; unknown when it could not be observed.
(= (mode-history:identity)
   (let $path (mode-history:path)
     (case (fs:metadata $path follow-observed)
       (((fs:failed fs:missing $_ $_ $_ $_ $_) none)
        ((fs:failed $_ $_ $_ $_ $_ $_) unknown)
        ($found (case (mode-host:file-info $path) (((ModeFileV1 $_ $device $inode) ($device $inode)) ($other unknown))))))))
; Places <text> as the mode's own record, at the file's end: (ModePlacedV1
; <intent>) once the intent is durable, to be completed; else
; (ModeIntentFailedV1 <why>), and nothing is placed. The record holds the
; occurrences numbered after <from> up to the frontier, in the numbering
; <lineage>; the frontier holds only for this file.
(= (mode-history:place-own $text $from $frontier $lineage)
   (let* (($path (mode-history:path)) ($created (mode-host:ensure-file $path)))
     (if (!= $created True) (ModeIntentFailedV1 $created)
       (case (mode-host:file-info $path)
         (((ModeFileV1 $offset $device $inode)
           (let* (($payload (if (str:ends-with $text "\n") $text (str:concat $text "\n")))
                  ($record (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "pending") (JsonPair "path" $path)
                              (JsonPair "payload" $payload) (JsonPair "sha256" (str:sha256 $payload))
                              (JsonPair "offset" $offset) (JsonPair "device" $device) (JsonPair "inode" $inode)
                              (JsonPair "from" $from) (JsonPair "frontier" $frontier) (JsonPair "lineage" $lineage))))
                  ($written (mode-history:write-intent $record)))
             (if (!= $written True) (ModeIntentFailedV1 (Error mode-history (Incomplete intent-not-durable)))
               (ModePlacedV1 $record))))
          ($bad (ModeIntentFailedV1 $bad)))))))
; A pending intent's text written or found written, then settled: True; a
; failure that may pass leaves it pending: (ModeIntentFailedV1 <why>); a file
; that no longer holds its place abandons it: ModeIntentAbandonedV1.
; Only what is established abandons an intent: a missing file, another one,
; or other bytes at its place. A read or a write that failed keeps it pending.
(= (mode-history:complete $record)
   (case (fs:metadata (sf:get $record "path" "") follow-observed)
     (((fs:failed fs:missing $_ $_ $_ $_ $_) (mode-history:abandon-intent $record file-missing))
      ((fs:failed $kind $_ $_ $_ $_ $_)
        (let $_ (mode-history:report (Error mode-history (Incomplete (file-unknown $kind)))) (ModeIntentFailedV1 $kind)))
      ($known
        (case (mode-host:resume-append $record)
          ((True (mode-history:settle-intent $record))
           ((Error mode-checkpoint (Forbidden $why)) (mode-history:abandon-intent $record $why))
           ($failed (let $_ (mode-history:report $failed) (ModeIntentFailedV1 $failed)))))))))
(= (mode-history:settle-intent $record)
   (let* (($offset (sf:get $record "offset" 0)) ($end (+ $offset (str:byte-length (sf:get $record "payload" ""))))
          ($settled (mode-history:write-intent (sf:put-all $record ((JsonPair "status" "settled"))))))
     (if (!= $settled True) (ModeIntentFailedV1 (Error mode-history (Incomplete settlement-not-durable)))
       ; The cursor follows the mode's own record when nothing came between.
       (let $_ (if (== (mode-history:cursor) $offset) (mode-history:mark-at $end) True) True))))
(= (mode-history:abandon-intent $record $why)
   (let* (($_ (mode-history:report (Error mode-history (abandoned-intent $why))))
          ($_ (mode-history:write-intent (sf:put-all $record ((JsonPair "status" "abandoned"))))))
     ModeIntentAbandonedV1))
; Records other loops appended since this mode's cursor, oldest first; "" when
; there are none, ModeCatchUpUnavailableV1 when they could not be read. A larger
; gap shows its newest $limit bytes and says where the rest is.
(= (mode-history:catch-up $limit)
   (if (not (mode-history:shared?)) ""
     (let* (($path (mode-history:path)) ($size (mode-history:size $path))
            ($cursor (mode-history:cursor)))
      (if (== $cursor unknown) ModeCatchUpUnavailableV1
       (let $from (if (< $cursor 0) 0 $cursor)
       (if (<= $size $from) ""
         (if (<= (- $size $from) $limit)
           (let $text (mode-host:read-at $path $from (- $size $from))
             (if (mode-host:read-failed? $text) ModeCatchUpUnavailableV1 $text))
           (case (fs:tail-text $path $limit)
             (((fs:tail $_ $_ $text)
               (str:join "" ("[" (repr (- (- $size $from) (str:byte-length $text)))
                             " earlier bytes of the agent history since this mode last ran are not shown here;"
                             " use episodes or query to read them]\n" $text)))
              ($bad ModeCatchUpUnavailableV1))))))))))

; Timestamps as the history writes them, as civil seconds.
(= (mode-history:leap? $year)
   (and (== (% $year 4) 0) (or (!= (% $year 100) 0) (== (% $year 400) 0))))
(= (mode-history:month-days $year)
   (let $february (if (mode-history:leap? $year) 29 28)
     (31 $february 31 30 31 30 31 31 30 31 30 31)))
(= (mode-history:civil-seconds $text)
   (case (re:captures (re:pattern "^([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})$") $text)
     ((re:no-match none)
      ($match
        (let* (($year (str:string->number (re:group-text $match $text 1)))
               ($month (str:string->number (re:group-text $match $text 2)))
               ($day (str:string->number (re:group-text $match $text 3)))
               ($hour (str:string->number (re:group-text $match $text 4)))
               ($minute (str:string->number (re:group-text $match $text 5)))
               ($second (str:string->number (re:group-text $match $text 6))))
          (if (or (< $year 1) (or (< $month 1) (> $month 12))) none
            (let $days (mode-history:month-days $year)
              (if (or (< $day 1) (or (> $day (index-atom $days (- $month 1)))
                    (or (> $hour 23) (or (> $minute 59) (> $second 59))))) none
                (let* (($prior (- $year 1))
                       ($before-year (+ (- (+ (* 365 $prior) (math:floor (/ $prior 4))) (math:floor (/ $prior 100))) (math:floor (/ $prior 400))))
                       ($before-month (foldl-atom (list:take (- $month 1) $days) 0 $total $day-count (+ $total $day-count))))
                  (+ (* (+ $before-year (+ $before-month (- $day 1))) 86400) (+ (* $hour 3600) (+ (* $minute 60) $second))))))))))))
(= (mode-history:line-time $line)
   (case (re:captures (re:pattern "^\\(\"([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\"") $line)
     ((re:no-match none)
      ($found (mode-history:civil-seconds (re:group-text $found $line 1))))))
(= (mode-history:nearest $lines $target $index $best $distance)
   (if (== $lines ()) $best
     (let $stamp (mode-history:line-time (car-atom $lines))
       (if (== $stamp none)
         (mode-history:nearest (cdr-atom $lines) $target (+ $index 1) $best $distance)
         (let $difference (math:abs (- $stamp $target))
           (if (or (< $best 0) (< $difference $distance))
             (mode-history:nearest (cdr-atom $lines) $target (+ $index 1) $index $difference)
             (mode-history:nearest (cdr-atom $lines) $target (+ $index 1) $best $distance)))))))
(= (mode-history:first-time $lines)
   (if (== $lines ()) none
     (let $stamp (mode-history:line-time (car-atom $lines))
       (if (== $stamp none) (mode-history:first-time (cdr-atom $lines)) $stamp))))
(= (mode-history:window $lines $start)
   (let (ModeHistoryLinesV1 $_ $reverse)
       (foldl-atom $lines (ModeHistoryLinesV1 $start ()) $state $line
         (let (ModeHistoryLinesV1 $index $pieces) $state
           (let* (($piece (str:join "" ((repr $index) ":" $line "\n"))) ($next (+ $index 1)))
             (ModeHistoryLinesV1 $next (cons-atom $piece $pieces)))))
     (str:join "" (list:reverse $reverse))))
(= (mode-history:lines $text)
   (let $all (str:split "\n" (erio:universal $text))
     (if (str:ends-with $text "\n") (list:take (- (size-atom $all) 1) $all) $all)))
(= (mode-history:around $lines $target $count $first)
   (let $best (mode-history:nearest $lines $target 0 -1 0)
     (if (< $best 0) ""
       (let* (($start (max 0 (- $best $count))) ($end (min (size-atom $lines) (+ $best (+ $count 1)))))
         (mode-history:window (list:take (- $end $start) (list:drop $start $lines)) (+ $start $first))))))
; Upstream episodes over the whole file; a large history is searched in its
; newest 4 MiB first, which covers recent times without reading it all.
(= (mode-history:tail-bytes) 4194304)
(= (mode-history:episodes $path $offered $count)
   (let* (($clean (str:trim (str:join "" (str:split "\"" (str:join "" (str:split "\\\"" (turn:text $offered)))))))
          ($target (mode-history:civil-seconds $clean)))
     (if (== $target none) (Error episodes (Unsolvable invalid-timestamp))
       (if (<= (mode-history:size $path) (mode-history:tail-bytes))
         (mode-history:episodes-whole $path $target $count)
         (case (fs:tail-text $path (mode-history:tail-bytes))
           (((fs:tail $_ $_ $text)
             (let* (($lines (cdr-atom (mode-history:lines $text))) ($first (mode-history:first-time $lines)))
               (if (or (== $first none) (< $target $first)) (mode-history:episodes-whole $path $target $count)
                 (let $found (mode-history:around $lines $target $count 1)
                   (if (== $found "") ""
                     (str:concat "[lines numbered within the newest 4 MiB of the agent history]\n" $found))))))
            ($bad (Error episodes (Fault $bad)))))))))
(= (mode-history:episodes-whole $path $target $count)
   (case (fs:read-text-result $path 0 "replace")
     ((("ok" $text) (mode-history:around (mode-history:lines $text) $target $count 1))
      ($bad (Error episodes (Fault $bad))))))
