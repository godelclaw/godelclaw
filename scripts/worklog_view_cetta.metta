; The standalone working view uses the same observation projection as the
; attach client. --json retains typed records; --full reads complete sources.
!(import! &self ../src/console/main_cetta.metta)

(= (cv:take-events)
   (let* (($owner (cc:get "events" ConsoleNoBufferV1))
          ($text (buffer:value $owner))
          ($_ (buffer:drop $owner (str:byte-length $text))))
     $text))

(= (cv:flush)
   (let $text (cv:take-events)
     (if (== $text "") True
       (progn (cc:set "viewer-emitted" True)
              (if (cc:get "viewer-buffer-once" False)
                (let $acc (cc:get "viewer-once-buf" ConsoleNoBufferV1)
                  (progn (buffer:append $acc $text) True))
                (progn (system:write stdout $text) True))))))

; Strip ANSI SGR sequences (if color is enabled) before checking block lead.
(= (cv:strip-sgr $line)
   (case (str:find $line "\u001b[")
     (((TextNotFound $_) $line)
      ($start
        (let* (($head (str:byte-slice $line 0 $start))
               ($rest (str:byte-slice $line (+ $start 2) (str:byte-length $line))))
          (case (str:find $rest "m")
            (((TextNotFound $_) $head)
             ($end (str:concat $head (cv:strip-sgr (str:byte-slice $rest (+ $end 1) (str:byte-length $rest))))))))))))

(= (cv:has-prefix-any? $text $prefixes)
   (if (== $prefixes ()) False
     (if (str:starts-with $text (car-atom $prefixes)) True
       (cv:has-prefix-any? $text (cdr-atom $prefixes)))))

(= (cv:block-header? $line)
   (let $plain (cv:strip-sgr $line)
     (or (== $plain "Plan")
         (cv:has-prefix-any? $plain ("◆ " "◇ " "• " "✓ " "✗ " "? " "◌ " "· " "! ")))))

(= (cv:session-header? $block)
   (let $first (car-atom (str:split "\n" $block))
     (str:starts-with (cv:strip-sgr $first) "◆ ")))

; Group rendered lines into whole presentation blocks without splitting inside
; heredocs or multi-paragraph commentary: a new block starts only at a glyph
; header line at start-of-output or immediately following a blank line.
(= (cv:split-blocks-go $lines $prev-blank $cur $acc)
   (if (== $lines ())
     (let $final (if (== $cur ()) $acc (cons-atom (str:join "\n" (list:reverse $cur)) $acc))
       (list:reverse $final))
     (let* ((($line $rest) (decons-atom $lines))
            ($blank (== $line ""))
            ($starts (and $prev-blank (cv:block-header? $line))))
       (if (and $starts (not (== $cur ())))
         (cv:split-blocks-go $rest $blank ($line)
           (cons-atom (str:join "\n" (list:reverse $cur)) $acc))
         (if (and $blank (== $cur ()))
           (cv:split-blocks-go $rest True () $acc)
           (cv:split-blocks-go $rest $blank (cons-atom $line $cur) $acc))))))

(= (cv:split-blocks $text)
   (let $blocks (cv:split-blocks-go (str:split "\n" $text) True () ())
     (filter-atom $blocks $b (!= (str:trim $b) ""))))

(= (cv:last-session-header $blocks $found)
   (if (== $blocks ()) $found
     (let* ((($head $tail) (decons-atom $blocks))
            ($next (if (cv:session-header? $head) (car-atom (str:split "\n" $head)) $found)))
       (cv:last-session-header $tail $next))))

(= (cv:trim-trailing-blanks $block)
   (if (str:ends-with $block "\n")
     (cv:trim-trailing-blanks (str:byte-slice $block 0 (- (str:byte-length $block) 1)))
     $block))

(= (cv:render-bounded $text $limit)
   (let* (($blocks (cv:split-blocks $text))
          ($total (size-atom $blocks)))
     (if (== $total 0) ""
       (if (<= $total $limit)
         (str:concat (str:join "\n\n" (map-atom $blocks $b (cv:trim-trailing-blanks $b))) "\n")
         (let* (($drop (- $total $limit))
                ($dropped (list:take $drop $blocks))
                ($kept (list:drop $drop $blocks))
                ($hdr (cv:last-session-header $dropped ""))
                ($note (cc:style "2" (str:join "" ("… " (repr $drop) " earlier blocks omitted; pass --full for complete history"))))
                ($prefix (if (and (!= $hdr "") (not (cv:session-header? (car-atom $kept))))
                           ($hdr $note) ($note)))
                ($rendered (list:append $prefix (map-atom $kept $b (cv:trim-trailing-blanks $b)))))
           (str:concat (str:join "\n\n" $rendered) "\n"))))))

(= (cv:emit-once-result $limit)
   (let* (($acc (cc:get "viewer-once-buf" ConsoleNoBufferV1))
          ($raw (buffer:value $acc))
          ($_ (host:close $acc))
          ($out (cv:render-bounded $raw $limit)))
     (if (== $out "")
       (system:write stdout "Waiting for agent or coding-session output…\n")
       (system:write stdout $out))))

(= (cv:tick $signals $once $interval)
   (once
    (let* (($_ (cc:streams-step)) ($_ (cv:flush))
          ($work (cc:get "stream-work" ())))
     (if (or (!= (host:signal $signals) 0) (and $once (== $work ())))
       (progn (if (cc:get "viewer-json" False) True
                (progn (cc:flush-visible-items) (if $once (cc:flush-running-tools) True)))
              (cv:flush)
              (if (and $once (cc:get "viewer-buffer-once" False))
                (cv:emit-once-result (cc:get "viewer-limit" 18))
                (if (and $once (and (not (cc:get "viewer-json" False)) (not (cc:get "viewer-emitted" False))))
                  (system:write stdout "Waiting for agent or coding-session output…\n")
                  True))
              True)
       (progn (if (== $work ())
                 (let $watch (cc:get "stream-watch" ConsoleNoWatchV1) (host:wait (($watch read)) $interval)) True)
              False)))))

(= (cv:loop $signals $once $interval)
   (if (cv:tick $signals $once $interval) 0 (cv:loop $signals $once $interval)))

(= (cv:ranked-entries $names)
   (list:sort-by cc:newer-ranked (map-atom $names $name
     (let $modified (cc:modified (fs:metadata-v2 (path:join (cc:stream-directory) $name) no-follow))
       ($modified $name)))))

(= (cv:dedup-entries $entries $seen)
   (if (== $entries ()) ()
     (let* ((($entry $rest) (decons-atom $entries))
            (($_ $name) $entry))
       (if (cli:member? $name $seen)
         (cv:dedup-entries $rest $seen)
         (cons-atom $entry (cv:dedup-entries $rest (cons-atom $name $seen)))))))

; In default bounded mode, select the newest 3 streams overall (4 when viewing
; all sessions) plus up to 2 newest streams matching "<session>.", deduplicated
; and strictly ordered oldest-to-newest by mtime so multi-file chronology stays
; coherent without scanning hundreds of historical logs.
(= (cv:select-names $names $session $full)
   (let $ranked (cv:ranked-entries $names)
     (if $full
       (map-atom (list:reverse $ranked) $entry (let ($_ $name) $entry $name))
       (let* (($recent (list:take (if (== $session "") 4 3) $ranked))
              ($prefix (if (== $session "") "" (str:concat $session ".")))
              ($session-recent (if (== $prefix "") ()
                                 (list:take 2 (filter-atom $ranked $e
                                   (let ($_ $n) $e (str:starts-with $n $prefix))))))
              ($ordered (list:sort-by cc:newer-ranked (cv:dedup-entries (list:append $recent $session-recent) ()))))
         (map-atom (list:reverse $ordered) $entry (let ($_ $name) $entry $name))))))

; Drain currently queued stream work completely (used per-file during initial
; seed so an older multi-chunk file never interleaves after a newer file).
(= (cv:drain-work $signals)
   (if (or (!= (host:signal $signals) 0) (== (cc:get "stream-work" ()) ())) True
     (let* (($work (cc:get "stream-work" ()))
            ($name (car-atom $work))
            ($_ (cc:set "stream-work" (cdr-atom $work)))
            ($_ (if (cc:get "stream-enabled" True) (cc:read-stream $name False 1) True))
            ($_ (cv:flush)))
       (cv:drain-work $signals))))

(= (cv:seed-ordered $names $full $tail-bytes $signals)
   (if (or (== $names ()) (!= (host:signal $signals) 0)) True
     (let* (($name (car-atom $names))
            ($_ (if $full True
              (case (fs:metadata-v2 (path:join (cc:stream-directory) $name) no-follow)
                (((fs:info-v2 regular $size $identity $_ $_ $_ $_)
                  (cc:file-set $name (ConsoleFileCursorV1 $identity (max 0 (- $size $tail-bytes)) (buffer:new 33554432 bytes) 0)))
                 ($bad True)))))
            ($_ (cc:queue-stream $name))
            ($_ (cv:drain-work $signals)))
       (cv:seed-ordered (cdr-atom $names) $full $tail-bytes $signals))))

(= (cv:stdout-tty?)
   (case (terminal:open "/dev/stdout")
     (((HostFailure $_ $_ $_) False)
      ($handle (progn (host:close $handle) True)))))

(= (cv:main)
   (let* (($options (cli:pair-args (cli:strings (system:args)) ("--once" "--json" "--full") (JsonObject ())))
          ($directory (sf:get $options "--dir" (system:getenv-or-default "METTACLAW_STREAM_DIR"
                        (system:expanduser "~/.local/state/godelclaw/display"))))
          ($session (sf:get $options "--session" ""))
          ($once (sf:get $options "--once" False))
          ($as-json (sf:get $options "--json" False))
          ($full (sf:get $options "--full" False))
          ($limit (max 1 (math:floor (cli:number (sf:get $options "--limit" "18") 18))))
          ($tail-bytes (min 1048576 (max 131072 (* $limit 8192))))
          ($buffer-once (and $once (and (not $as-json) (not $full))))
          ($settings (JsonObject ((JsonPair "display" $directory) (JsonPair "agent" $session) (JsonPair "view_all" (== $session "")))))
          ($_ (cc:initialize $settings))
          ($_ (if (and (cc:get "color" False)
                       (and (== (system:getenv-or-default "FORCE_COLOR" "") "")
                            (not (cv:stdout-tty?))))
                (cc:set "color" False) True))
          ($_ (cc:set "viewer-json" $as-json))
          ($_ (cc:set "viewer-full" $full))
          ($_ (cc:set "viewer-limit" $limit))
          ($_ (cc:set "viewer-emitted" False))
          ($_ (cc:set "viewer-buffer-once" $buffer-once))
          ($_ (if $buffer-once (cc:set "viewer-once-buf" (buffer:new 33554432 bytes)) True))
          ($_ (fs:make-directories $directory 448))
          ($watch (host:watch $directory))
          ($_ (cc:set "stream-watch" $watch))
          ($_ (cc:stream-control))
          ($signals (host:signals))
          ($selected (cv:select-names (cc:stream-names) $session (or $full $as-json)))
          ($_ (cv:seed-ordered $selected (or $full $as-json) $tail-bytes $signals))
          ($interval (max 50 (math:floor (* 1000 (cli:number (sf:get $options "--interval" "0.15") 0.15)))))
          ($status (cv:loop $signals $once $interval))
          ($_ (host:close $signals))
          ($_ (host:close $watch)))
     $status))
!(system:exit (cv:main))
