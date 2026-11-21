; Native longitudinal observatory report reader with shared-lock cross-file
; snapshot isolation. Holds a shared lock on .collector.lock and refuses torn
; unrecovered publications unless --best-effort is explicitly requested.
!(import! &self ./observatory_publication_cetta.metta)
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library math))
!(import! &self (library list))
!(bind! &orep-samples (new-state (RepSamples 0 0 "")))
!(bind! &orep-day-cache (new-state (DayCache -1 "")))
!(bind! &orep-re-t (new-state (re:compile "^\\{\\s*\"t\"\\s*:\\s*([+-]?[0-9]+(?:\\.[0-9]+)?)")))

(= (orep:columns)
   ("turns_cetta" "turns_swipl" "calls_anthropic" "llm_transient"
    "tokens_in" "tokens_cached" "tokens_out" "effects"
    "eff_exception" "eff_withheld" "sends" "rests" "starts"
    "engine_failures"))

(= (orep:spaces $n)
   (if (<= $n 0) ""
     (if (== $n 1) " "
       (let* (($half (math:floor (/ $n 2)))
              ($s (orep:spaces $half)))
         (if (== (% $n 2) 0) (str:concat $s $s) (str:join "" ($s $s " ")))))))

(= (orep:rjust $text $width)
   (let $len (str:char-length $text)
     (if (>= $len $width) $text
       (str:concat (orep:spaces (- $width $len)) $text))))

(= (orep:int-str $n)
   (if (== (get-type $n) Number) (str:format-fixed $n 0) "0"))

(= (orep:val-str $v)
   (case $v
     ((JsonNull "None")
      (True "True")
      (False "False")
      ($other (if (== (get-type $other) String) $other
                (if (== (get-type $other) Number) (orep:int-str $other) (repr $other)))))))

; 15-minute bucket cache for local calendar day (exact across all timezones/DST).
(= (orep:day $t)
   (let* (($bucket (math:floor (/ $t 900)))
          ((DayCache $prev-b $prev-d) (get-state &orep-day-cache)))
     (if (== $bucket $prev-b) $prev-d
       (let* (($d (system:format-time "%Y-%m-%d" (math:floor $t) "local"))
              ($s (change-state! &orep-day-cache (DayCache $bucket $d))))
         $d))))

(= (orep:mdhm $t) (system:format-time "%m-%d %H:%M" (math:floor $t) "local"))

(= (orep:inc $space $day $col $delta)
   (case (collapse (match $space (RepCell $day $col $v) $v))
     ((($v)
       (if (== $delta 0) True
         (let* (($next (+ $v $delta))
                ($r (remove-atom $space (RepCell $day $col $v)))
                ($a (add-atom $space (RepCell $day $col $next))))
           True)))
      ($absent
       (let* (($d (case (collapse (match $space (RepDay $day) True))
                    (((True) True) ($none (add-atom $space (RepDay $day))))))
              ($a (add-atom $space (RepCell $day $col $delta))))
         True)))))

(= (orep:get-cell $space $day $col)
   (case (collapse (match $space (RepCell $day $col $v) $v)) ((($v) $v) ($none 0))))

(= (orep:contains? $hay $needle)
   (if (!= (get-type $hay) String) False
     (case (str:find $hay $needle) (((TextNotFound $_) False) ($at True)))))

(= (orep:truthy? $v)
   (case $v ((JsonNull False) (False False) (0 False) ("" False) ($other True))))

(= (orep:num $v) (if (== (get-type $v) Number) $v 0))

(= (orep:fast-t $line)
   (let $m (re:captures (get-state &orep-re-t) $line)
     (case $m
       (((re:match $_1 $_2 $_3)
         (let $txt (re:group-text $m $line 1)
           (if (== (get-type $txt) String) (FastT (str:string->number $txt)) NoFastT)))
        ($none NoFastT)))))

(= (orep:on-row $space $samples-state $kind $since $row)
   (let $t (orep:num (sf:get $row "t" 0))
     (if (< $t $since) True
       (case $kind
         ((turns
           (let* (($day (orep:day $t))
                  ($eng (orep:val-str (sf:get $row "engine" JsonNull))))
             (orep:inc $space $day (str:concat "turns_" $eng) 1)))
          (llm
           (let $day (orep:day $t)
             (if (orep:truthy? (sf:get $row "transient" False))
               (orep:inc $space $day "llm_transient" 1)
               (let* (($prov (orep:val-str (sf:get $row "provider" "")))
                      ($i1 (orep:inc $space $day (str:concat "calls_" $prov) 1))
                      ($i2 (orep:inc $space $day "tokens_in" (orep:num (sf:get $row "in" 0))))
                      ($i3 (orep:inc $space $day "tokens_cached" (orep:num (sf:get $row "cache_read" 0))))
                      ($i4 (orep:inc $space $day "tokens_out" (orep:num (sf:get $row "out" 0)))))
                 True))))
          (effects
           (let* (($day (orep:day $t))
                  ($disp (orep:val-str (sf:get $row "disposition" JsonNull)))
                  ($ekind (orep:val-str (sf:get $row "kind" "")))
                  ($i1 (orep:inc $space $day "effects" 1))
                  ($i2 (if (!= $disp "returned") (orep:inc $space $day (str:concat "eff_" $disp) 1) True))
                  ($i3 (if (or (== $ekind "send") (== $ekind "send-telegram-chat")) (orep:inc $space $day "sends" 1) True))
                  ($i4 (if (== $ekind "rest") (orep:inc $space $day "rests" 1) True)))
             True))
          (lifecycle
           (let $ev (sf:get $row "event" "")
             (if (orep:contains? $ev "Started")
               (orep:inc $space (orep:day $t) "starts" 1)
               (if (== $ev "engine_failed")
                 (orep:inc $space (orep:day $t) "engine_failures" 1) True))))
          (samples
           (let (RepSamples $cnt $first-t $last-raw) (get-state $samples-state)
             (change-state! $samples-state
               (RepSamples (+ $cnt 1) (if (== $cnt 0) $t $first-t) $row)))))))))

(= (orep:parse-line $space $samples-state $kind $since $line)
   (let $trimmed (str:trim $line)
     (if (== $trimmed "") True
       (if (== $kind samples)
         (case (orep:fast-t $trimmed)
           (((FastT $t)
             (if (< $t $since) True
               (let (RepSamples $cnt $first-t $last-raw) (get-state $samples-state)
                 (change-state! $samples-state
                   (RepSamples (+ $cnt 1) (if (== $cnt 0) $t $first-t) $trimmed)))))
            (NoFastT
             (case (json:parse-python $trimmed)
               (((JsonObject $pairs) (orep:on-row $space $samples-state $kind $since (JsonObject $pairs)))
                ($bad True))))))
         (if (and (== $kind lifecycle)
                  (and (not (orep:contains? $trimmed "Started"))
                       (not (orep:contains? $trimmed "engine_failed"))))
           True
           (case (json:parse-python $trimmed)
             (((JsonObject $pairs) (orep:on-row $space $samples-state $kind $since (JsonObject $pairs)))
              ($bad True))))))))

(= (orep:feed-parts $space $samples-state $kind $since $parts)
   (let ($head $rest) ((car-atom $parts) (cdr-atom $parts))
     (if (== $rest ()) $head
       (let $d (orep:parse-line $space $samples-state $kind $since $head)
         (orep:feed-parts $space $samples-state $kind $since $rest)))))

(= (orep:split-carry $carry $chunk $space $samples-state $kind $since)
   (let* (($combined (str:concat $carry $chunk))
          ($parts (str:split "\n" $combined)))
     (if (== (cdr-atom $parts) ()) $combined
       (orep:feed-parts $space $samples-state $kind $since $parts))))

(= (orep:stream-file $fh $at $size $carry $space $samples-state $kind $since)
   (if (>= $at $size)
     (if (== $carry "") True (orep:parse-line $space $samples-state $kind $since $carry))
     (case (fs:read-chunk $fh (min 65536 (- $size $at)) $at)
       (((fs:chunk $bytes $_)
         (let $blen (str:byte-length $bytes)
           (if (== $blen 0)
             (if (== $carry "") True (orep:parse-line $space $samples-state $kind $since $carry))
             (let $next-carry (orep:split-carry $carry $bytes $space $samples-state $kind $since)
               (orep:stream-file $fh (+ $at $blen) $size $next-carry $space $samples-state $kind $since)))))
        ($err True)))))

(= (orep:ingest-file $root $name $kind $since $space $samples-state)
   (let $path (path:join $root $name)
     (if (not (fs:is-file $path)) True
       (let* (($size (fs:size $path))
              ($fh (fs:open-read $path)))
         (if (sf:failed? $fh) True
           (let* (($d1 (orep:stream-file $fh 0 $size "" $space $samples-state $kind $since))
                  ($d2 (fs:close $fh)))
             True))))))

(= (orep:format-header)
   (let $cols (map-atom (orep:columns) $c
                (orep:rjust (str:char-slice $c 0 (min 13 (str:char-length $c))) 13))
     (str:concat "day         " (str:join " " $cols))))

(= (orep:format-day $space $day)
   (let $vals (map-atom (orep:columns) $c (orep:rjust (orep:int-str (orep:get-cell $space $day $c)) 13))
     (str:join "" ($day "  " (str:join " " $vals)))))

(= (orep:resolve-last-sample $raw)
   (if (== (get-type $raw) String)
     (case (json:parse-python $raw) (((JsonObject $pairs) (JsonObject $pairs)) ($bad (JsonObject ()))))
     $raw))

(= (orep:format-samples (RepSamples $cnt $first-t $last-raw))
   (if (== $cnt 0) ""
     (let* (($last (orep:resolve-last-sample $last-raw))
            ($last-t (orep:num (sf:get $last "t" 0)))
            ($eng (orep:val-str (sf:get $last "engine" JsonNull)))
            ($mod (orep:val-str (sf:get $last "model" JsonNull)))
            ($rss (orep:val-str (sf:get $last "rss_mb" JsonNull)))
            ($swap (orep:val-str (sf:get $last "swap_mb" JsonNull)))
            ($up-raw (sf:get $last "uptime_s" 0))
            ($up-num (if (== (get-type $up-raw) Number) $up-raw 0))
            ($up-h (str:format-fixed (/ $up-num 3600.0) 1)))
       (str:join "" ("\nsamples: " (orep:int-str $cnt) " from " (orep:mdhm $first-t)
                     " to " (orep:mdhm $last-t) "; now engine=" $eng " model=" $mod
                     " rss=" $rss "MB swap=" $swap "MB uptime=" $up-h "h\n")))))

(= (orep:render $root $days $now $best-effort)
   (let* (($since (- $now (* $days 86400.0)))
          ($space (new-space))
          ($rst1 (change-state! &orep-samples (RepSamples 0 0 "")))
          ($rst2 (change-state! &orep-day-cache (DayCache -1 "")))
          ($r1 (orep:ingest-file $root "turns.jsonl" turns $since $space &orep-samples))
          ($r2 (orep:ingest-file $root "llm.jsonl" llm $since $space &orep-samples))
          ($r3 (orep:ingest-file $root "effects.jsonl" effects $since $space &orep-samples))
          ($r4 (orep:ingest-file $root "lifecycle.jsonl" lifecycle $since $space &orep-samples))
          ($r5 (orep:ingest-file $root "samples.jsonl" samples $since $space &orep-samples))
          ($day-list (list:sort (collapse (match $space (RepDay $d) $d))))
          ($header (orep:format-header))
          ($rows (map-atom $day-list $d (orep:format-day $space $d)))
          ($table (str:concat (str:join "\n" (cons-atom $header $rows)) "\n"))
          ($tail (orep:format-samples (get-state &orep-samples))))
     (str:concat $table $tail)))

(= (orep:run $root $days $now $best-effort)
   (if (not (fs:exists $root))
     (progn (system:write stderr "observation data directory does not exist\n") 1)
     (let* (($lock (fs:open-write (path:join $root ".collector.lock") ("create" "append") 384))
            ($held (if (sf:failed? $lock) $lock (fs:lock $lock shared))))
       (case $held
         ((fs:locked
           (if (and (not $best-effort) (not (op:quiescent? $root)))
             (let* (($u (fs:unlock $lock)) ($c (fs:close $lock)))
               (progn (system:write stderr "observation publication incomplete; run collect to recover before authoritative report\n") 1))
             (let* (($text (orep:render $root $days $now $best-effort))
                    ($u (fs:unlock $lock)) ($c (fs:close $lock))
                    ($w (system:write stdout $text)))
               0)))
          ($failed
           (let $c (if (sf:failed? $lock) True (fs:close $lock))
             (progn (system:write stderr "observation report lock unavailable\n") 1))))))))
