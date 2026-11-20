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

; As the framework appends its own turns: one durable write; a failure is
; reported and the loop goes on.
(= (mode-history:append $text)
   (let* (($path (mode-history:path))
          ($payload (if (str:ends-with $text "\n") $text (str:concat $text "\n")))
          ($_ (fs:make-directories (path:dirname $path) 504))
          ($owner (fs:open-write $path ("create" "append") 432)))
     (case $owner
       (((NativeHandle $_ $_)
         (let* (($written (fs:write-all $owner $payload))
                ($synced (if (sf:failed? $written) $written (fs:sync $owner)))
                ($_ (fs:close $owner)))
           (if (sf:failed? $synced) (mode-history:report $synced) True)))
        ($bad (mode-history:report $bad))))))
(= (mode-history:report $failure)
   (let $_ (system:write stderr (str:concat "[mode] agent history append error: " (str:concat (diagnostic:text $failure 400) "\n")))
     False))

; A mode's cursor is the history size after its own last record, and how many
; of its newest saved messages the history still owes after a failed write.
(= (mode-history:cursor-path) (path:join (mode-host:root) "agent-history-cursor.json"))
(= (mode-history:cursor)
   (let $record (sf:native-object (mode-history:cursor-path))
     (if (== (sf:get $record "path" "") (mode-history:path)) (sf:get $record "size" -1) -1)))
(= (mode-history:owed)
   (let $record (sf:native-object (mode-history:cursor-path))
     (if (== (sf:get $record "path" "") (mode-history:path)) (sf:get $record "owed" 0) 0)))
(= (mode-history:mark $owed)
   (sf:replace (mode-history:cursor-path)
     (turn:json (JsonObject ((JsonPair "path" (mode-history:path))
                             (JsonPair "size" (mode-history:size (mode-history:path)))
                             (JsonPair "owed" $owed))))
     384 True))

; Records other loops appended since this mode's cursor, oldest first. A larger
; gap shows its newest $limit bytes and says where the rest is.
(= (mode-history:catch-up $limit)
   (if (not (mode-history:shared?)) ""
     (let* (($path (mode-history:path)) ($size (mode-history:size $path))
            ($cursor (mode-history:cursor)) ($from (if (< $cursor 0) 0 $cursor)))
       (if (<= $size $from) ""
         (if (<= (- $size $from) $limit)
           (let $text (mode-host:read-at $path $from (- $size $from))
             (if (sf:failed? $text) "" $text))
           (case (fs:tail-text $path $limit)
             (((fs:tail $_ $_ $text)
               (str:join "" ("[" (repr (- (- $size $from) (str:byte-length $text)))
                             " earlier bytes of the agent history since this mode last ran are not shown here;"
                             " use episodes or query to read them]\n" $text)))
              ($bad ""))))))))

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
