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
                (progn (cv:clear-status)
                       (system:write stdout $text)
                       (cc:set "viewer-line-start" (str:ends-with $text "\n"))
                       True))))))

; Follow mode on a terminal keeps one live line at the bottom while a tool
; runs: its session, its command and how long it has run. The line is erased
; before every block is written, so the finished block lands once and no
; running row is left behind; it is painted only at the start of a line.
(= (cv:running-status $width)
   (let $open (collapse (match &cc-items (ConsoleToolItemV1 $e $s $a $i $k $nm $cmd $st $disp $upd $stat $hc $ec $txt $art False)
                (if (or (== $k "tool.forming") (== $cmd "")) (empty) ((if (> $disp 0) $disp $st) $s $cmd))))
     (if (== $open ()) ""
       (let* ((($since $sid $cmd) (car-atom (list:sort-by cv:newer-running $open)))
              ($now (cc:live-now))
              ($elapsed (if (and (> $since 0) (>= $now $since)) (str:concat " · " (cc:span (- $now $since))) ""))
              ($more (if (> (size-atom $open) 1) (str:join "" (" (+" (repr (- (size-atom $open) 1)) " more)")) ""))
              ($lead (str:join "" ((cc:style (cc:glyph-style "running") (cc:glyph "running")) " " (cc:short-sid (cc:one-line $sid)) " running "))))
         (cc:clip (str:join "" ($lead (cc:one-line (erv:command-text $cmd)) $elapsed $more)) $width)))))
(= (cv:newer-running ($a $_ $_) ($b $_ $_)) (>= $a $b))
; The terminal's width now, from the viewer's own handle on stdout.
(= (cv:columns)
   (case (cc:get "viewer-terminal" ConsoleNoTerminalV1)
     ((ConsoleNoTerminalV1 (cc:display-width))
      ($terminal (case (catch (terminal:size $terminal))
                   (((HostSize $cols $_) (if (> $cols 0) $cols (cc:display-width))) ($other (cc:display-width))))))))
(= (cv:rows-of $cells $cols) (if (<= $cells $cols) 1 (+ 1 (math:floor (/ (- $cells 1) $cols)))))
(= (cv:erase-up $n) (if (<= $n 0) "" (str:concat "\u001b[1A\u001b[2K" (cv:erase-up (- $n 1)))))
; The status was painted on one row of the width it had then; a terminal that
; has since narrowed and rewrapped it holds it on more rows, all erased.
(= (cv:clear-status)
   (let $shown (cc:get "viewer-status-shown" "")
     (if (== $shown "") True
       (let* (($painted (cc:get "viewer-status-cols" 0)) ($now (cv:columns))
              ($rows (if (< $now $painted) (cv:rows-of (terminal:cells $shown) $now) 1)))
         (progn (system:write stdout (str:concat "\r\u001b[2K" (cv:erase-up (- $rows 1))))
                (cc:set "viewer-status-shown" "") True)))))
; Painted one column short of the edge, so the line never wraps by itself.
(= (cv:paint-status)
   (if (not (and (cc:get "viewer-status" False) (cc:get "viewer-line-start" True))) True
     (let* (($cols (cv:columns))
            ($text (cv:running-status (max 10 (- $cols 1))))
            ($shown (cc:get "viewer-status-shown" "")))
       (if (and (== $text $shown) (== $cols (cc:get "viewer-status-cols" 0))) True
         (progn (cv:clear-status)
                (system:write stdout $text)
                (cc:set "viewer-status-shown" $text)
                (cc:set "viewer-status-cols" $cols) True)))))

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

; Group emissions into whole presentation blocks. A block starts only where
; an emission starts with a glyph header line; text inside an emission (a
; heredoc, a multi-paragraph reply) never starts one, whatever its lines say.
; An emission without a header (a separator, a continued stream) joins the
; block before it.
(= (cv:starts-block? $emission)
   (cv:block-header? (car-atom (str:split "\n" $emission))))
(= (cv:group-emissions $emissions $cur $acc)
   (if (== $emissions ())
     (list:reverse (if (== $cur "") $acc (cons-atom $cur $acc)))
     (let* ((($emission $rest) (decons-atom $emissions)))
       (if (and (cv:starts-block? $emission) (!= $cur ""))
         (cv:group-emissions $rest $emission (cons-atom $cur $acc))
         (cv:group-emissions $rest (str:concat $cur $emission) $acc)))))
(= (cv:strip-leading-blanks $block)
   (if (str:starts-with $block "\n")
     (cv:strip-leading-blanks (str:byte-slice $block 1 (str:byte-length $block)))
     $block))
(= (cv:split-blocks $text)
   (let $blocks (map-atom (cv:group-emissions (str:split "\u001e" $text) "" ()) $b (cv:strip-leading-blanks $b))
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
              (cv:clear-status)
              (if (and $once (cc:get "viewer-buffer-once" False))
                (cv:emit-once-result (cc:get "viewer-limit" 18))
                (if (and $once (and (not (cc:get "viewer-json" False)) (not (cc:get "viewer-emitted" False))))
                  (system:write stdout "Waiting for agent or coding-session output…\n")
                  True))
              True)
       (progn (cv:paint-status)
              (if (== $work ())
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
          ($_ (cc:set "viewer-status" (and (not $once) (and (not $as-json) (cv:stdout-tty?)))))
          ($_ (if (cc:get "viewer-status" False)
                (case (terminal:open "/dev/stdout")
                  (((HostFailure $_ $_ $_) (cc:set "viewer-status" False))
                   ($handle (cc:set "viewer-terminal" $handle))))
                True))
          ($_ (if $buffer-once (cc:set "viewer-once-buf" (buffer:new 33554432 bytes)) True))
          ($_ (cc:set "viewer-mark-emissions" $buffer-once))
          ($_ (fs:make-directories $directory 448))
          ($watch (host:watch $directory))
          ($_ (cc:set "stream-watch" $watch))
          ($_ (cc:stream-control))
          ($signals (host:signals))
          ($selected (cv:select-names (cc:stream-names) $session (or $full $as-json)))
          ($_ (cv:seed-ordered $selected (or $full $as-json) $tail-bytes $signals))
          ; Live from here on: elapsed times on the status line count in wall time.
          ($_ (if (cc:get "viewer-status" False) (cc:set "clock-live" True) True))
          ($interval (max 50 (math:floor (* 1000 (cli:number (sf:get $options "--interval" "0.15") 0.15)))))
          ($status (cv:loop $signals $once $interval))
          ($_ (host:close $signals))
          ($_ (host:close $watch)))
     $status))
!(system:exit (cv:main))
