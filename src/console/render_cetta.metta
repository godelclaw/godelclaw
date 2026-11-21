; Frames own their bytes until physical writes acknowledge the complete
; prefix. Edits may continue while a frame is pending; the next frame uses
; the newest editor state. Rendering has no agent-effect interface.
!(import! &self ./streams_cetta.metta)
!(import! &self (library math))
(= (cc:repeat $text $count)
   (if (<= $count 0) ""
     (let $half (cc:repeat $text (math:floor (/ $count 2)))
       (str:join "" ($half $half (if (== (% $count 2) 0) "" $text))))))
(= (cc:cup $row $column) (str:join "" ("\u001b[" (repr $row) ";" (repr $column) "H")))
; Erase from 1-based $row through $height in place without triggering tmux's
; ED0-at-(1,1) scrollback push (tmux pushes the whole screen to history on
; ESC[1;1H ESC[J, whereas EL2 on row 1 + ED0 from row 2 clears in place).
(= (cc:erase-from $row $height)
   (if (> $row 1)
     (str:join "" ((cc:cup $row 1) "\u001b[J"))
     (if (> $height 1)
       "\u001b[1;1H\u001b[2K\u001b[2;1H\u001b[J\u001b[1;1H"
       "\u001b[1;1H\u001b[2K")))
(= (cc:cursor-up $count) (if (> $count 0) (str:join "" ("\u001b[" (repr $count) "A")) ""))
(= (cc:cha $column) (str:join "" ("\u001b[" (repr (max 1 $column)) "G")))
(= (cc:label)
   (cc:display-name (cc:str-field (cc:settings) "agent" "agent")))
(= (cc:status-text)
   (cc:status-text 120))
(= (cc:search-bar $width)
   (let* (($dir (cc:get "search-dir" "reverse"))
          ($query (cc:one-line (terminal:clean (cc:get "search-query" ""))))
          ($match (cc:get "search-match" -1))
          ($pos (cc:get "search-match-pos" 0))
          ($total (cc:get "search-match-total" 0))
          ($label (if (== $dir "forward")
                    (if (>= $match 0) "(i-search)" "(failed i-search)")
                    (if (>= $match 0) "(reverse-i-search)" "(failed reverse-i-search)")))
          ($hint (if (>= $match 0)
                   (str:join "" ("[" (repr $pos) "/" (repr $total) " · ^R older · ^S newer · Enter accept · ^G cancel]"))
                   "[no match · Backspace edit · ^G cancel]"))
          ($line (str:join "" ($label "`" $query "': " $hint))))
     (cc:style "33" (terminal:clip $line $width))))
(= (cc:editor-rows $rows $first)
   (if (== $rows ()) ()
     (let* (($prefix (if $first (cc:style "36" "› ") "  "))
            ($line (str:concat $prefix (car-atom $rows))) ($rest (cc:editor-rows (cdr-atom $rows) False)))
       (cons-atom $line $rest))))

; -- CPR-synchronized inline-to-bottom painter state ---------------------------
; Fresh panes open inline directly below the shell using terminal:cpr-request
; (zero blank lines in scrollback). Once the live region reaches the bottom row
; of the terminal, region-anchored switches permanently to True and stays on the
; bottom-anchored painter. Every resize aborts unwritten stale-width frames,
; drains any partial write, and resynchronizes from a fresh CPR reply.
(= (cc:render-reset)
   (let* (($_ (cc:set "region-anchored" False))
          ($_ (cc:set "region-top" 0))
          ($_ (cc:set "paint-top" 0))
          ($_ (cc:set "region-rows" 0))
          ($_ (cc:set "cpr-phase" "synced"))
          ($_ (cc:set "cpr-awaiting" 0))
          ($_ (cc:set "cpr-deadline" 0))
          ($_ (cc:set "cpr-supported" True))
          ($_ (cc:set "cpr-need-newline" False))
          ($_ (cc:set "last-cols" 0))
          ($_ (cc:set "last-height" 0))
          ($_ (cc:set "frame-cols" 0))
          ($_ (cc:set "frame-height" 0))
          ($_ (cc:set "frame-events" ""))
          ($_ (cc:set "frame-is-paint" False))
          ($_ (cc:set "painted-lines" ()))
          ($_ (cc:set "painted-cols" 0))
          ($_ (cc:set "painted-height" 0))
          ($_ (cc:set "painted-cursor-line" 0))
          ($_ (cc:set "painted-cursor-column" 0))
          ($_ (cc:set "resize-erase-top" 0))
          ($_ (cc:set "resize-raced-inflight" False))
          ($_ (cc:set "resize-need-cpr" False))
          ($_ (cc:set "post-events-reserved" 0))
          ($_ (cc:set "post-events-height" 0))
          ($_ (cc:set "post-events-parked" False)))
     True))

(= (cc:parse-cpr $payload)
   (terminal:cpr-parse (str:join "" ("\u001b[" $payload "R"))))

(= (cc:wrap-rows-go $gs $cols $wide-extra $col $rows)
   (if (== $gs ()) $rows
     (let* ((((HostGrapheme $_ $_ $_ $w) $rest) (decons-atom $gs))
            ($step (if (>= $w 2) (+ $w $wide-extra) $w)))
       (if (== $step 0)
         (cc:wrap-rows-go $rest $cols $wide-extra $col $rows)
         (if (and (> $col 0) (> (+ $col $step) $cols))
           (cc:wrap-rows-go $rest $cols $wide-extra $step (+ $rows 1))
           (cc:wrap-rows-go $rest $cols $wide-extra (+ $col $step) $rows))))))

(= (cc:wrap-line-rows $text $cols $wide-extra)
   (cc:wrap-rows-go (terminal:graphemes $text) (max 1 $cols) $wide-extra 0 1))

; Estimate extra rows tmux grid_reflow would add above the cursor if the pane
; narrowed to half width (with tmux 3.6's 3-slot wide-grapheme reflow). When
; this exceeds the available cushion rows above region-top, temporarily park
; the live region at the bottom of the pane while keeping region-top at the end
; of committed content so tmux never spills live-region lines into scrollback.
(= (cc:reflow-expansion $lines $narrow-cols)
   (foldl-atom $lines 0 $sum $line
     (+ $sum (- (cc:wrap-line-rows (terminal:clean $line) $narrow-cols 1) 1))))

(= (cc:needs-park? $lines $cols $rtop $height $reserved)
   (if (or (== (system:getenv-or-default "TMUX" "") "")
           (>= (+ $rtop (- $reserved 1)) $height))
     False
     (let* (($cushion (max 0 (- $rtop 1)))
            ($narrow (max 32 (math:floor (/ $cols 2))))
            ($expansion (cc:reflow-expansion $lines $narrow)))
       (> $expansion $cushion))))

(= (cc:event-rows $events $cols)
   (if (== $events "") 0
     (let* (($trimmed (if (str:ends-with $events "\n")
                        (str:byte-slice $events 0 (- (str:byte-length $events) 1))
                        $events))
            ($lines (str:split "\n" $trimmed)))
       (foldl-atom $lines 0 $sum $line
         (+ $sum (cc:wrap-line-rows (terminal:clean $line) (max 1 $cols) 0))))))

; Simulate tmux wrap of the prefix of $text up to $budget display cells, returning
; (WrapCursorV1 $extra-rows $end-col-1based $wide-seen).
(= (cc:wrap-prefix-go $gs $cols $wide-extra $budget $col $extra $wide-seen)
   (if (or (== $gs ()) (<= $budget 0))
     (WrapCursorV1 $extra (+ $col 1) $wide-seen)
     (let* ((((HostGrapheme $_ $_ $_ $w) $rest) (decons-atom $gs))
            ($step (if (>= $w 2) (+ $w $wide-extra) $w))
            ($next-wide (if (>= $w 2) (+ $wide-seen 1) $wide-seen)))
       (if (== $step 0)
         (cc:wrap-prefix-go $rest $cols $wide-extra $budget $col $extra $next-wide)
         (if (and (> $col 0) (> (+ $col $step) $cols))
           (cc:wrap-prefix-go $rest $cols $wide-extra (- $budget $w) $step (+ $extra 1) $next-wide)
           (cc:wrap-prefix-go $rest $cols $wide-extra (- $budget $w) (+ $col $step) $extra $next-wide))))))

(= (cc:wrap-prefix $text $cols $wide-extra $budget)
   (cc:wrap-prefix-go (terminal:graphemes $text) (max 1 $cols) $wide-extra $budget 0 0 0))

(= (cc:lines-have-wide? $lines)
   (if (== $lines ()) False
     (let (WrapCursorV1 $_ $_ $w) (cc:wrap-prefix (car-atom $lines) 1000000 0 1000000)
       (if (> $w 0) True (cc:lines-have-wide? (cdr-atom $lines))))))

; Detect whether tmux reflowed wide graphemes using 3-slot steps (tmux 3.6
; grid_reflow_split) versus standard 2-cell steps, grounded in the CPR column.
(= (cc:wide-extra-for-cpr $cur-text $cols $cursor-col $cpr-col $above-lines)
   (let* (((WrapCursorV1 $_ $std-col $cur-wide) (cc:wrap-prefix $cur-text $cols 0 $cursor-col))
          ((WrapCursorV1 $_ $t36-col $_) (cc:wrap-prefix $cur-text $cols 1 $cursor-col)))
     (if (> $cur-wide 0)
       (if (and (!= $std-col $t36-col) (== $cpr-col $std-col)) 0 1)
       (if (cc:lines-have-wide? $above-lines) 1 0))))

(= (cc:rows-above-after-resize $new-cols $cpr-col)
   (let* (($lines (cc:get "painted-lines" ()))
          ($cur-line (cc:get "painted-cursor-line" 0))
          ($cur-col (cc:get "painted-cursor-column" 0))
          ($old-cols (cc:get "painted-cols" 0))
          ($raced (cc:get "resize-raced-inflight" False)))
     (if (== $lines ()) 0
       (if (or $raced (or (== (system:getenv-or-default "TMUX" "") "")
                          (or (<= $old-cols 0) (>= $new-cols $old-cols))))
         $cur-line
         (let* (($above (list:take $cur-line $lines))
                ($cur-text (let $tail (list:drop $cur-line $lines) (if (== $tail ()) "" (car-atom $tail))))
                ($wide-extra (cc:wide-extra-for-cpr $cur-text $new-cols $cur-col $cpr-col $above))
                ($above-rows (foldl-atom $above 0 $sum $line
                               (+ $sum (cc:wrap-line-rows $line $new-cols $wide-extra))))
                ((WrapCursorV1 $own-extra $_ $_) (cc:wrap-prefix $cur-text $new-cols $wide-extra $cur-col)))
           (+ $above-rows $own-extra))))))

; Begin initial attach CPR handshake (before first paint).
(= (cc:cpr-begin-attach $terminal $now)
   (let* (((HostSize $cols $height) (terminal:size $terminal))
          ($_ (cc:set "last-cols" $cols))
          ($_ (cc:set "last-height" $height))
          ($_ (cc:set "cpr-phase" "attach"))
          ($_ (cc:set "cpr-awaiting" 1))
          ($_ (cc:set "cpr-deadline" (+ $now 150000000)))
          ($_ (cc:set "cpr-supported" True))
          ($_ (cc:set "dirty" True)))
     True))

; Re-prepend unsent events from an aborted unwritten frame so no scrollback is lost.
(= (cc:restore-frame-events)
   (let $saved (cc:get "frame-events" "")
     (if (== $saved "") True
       (let* (($ev (cc:get "events" ConsoleNoBufferV1))
              ($current (buffer:value $ev))
              ($_ (if (> (str:byte-length $current) 0) (buffer:drop $ev (str:byte-length $current)) True))
              ($_ (buffer:append $ev (str:concat $saved $current)))
              ($_ (cc:set "frame-events" "")))
         True))))

; Triggered on SIGWINCH or observed terminal:size change.
(= (cc:note-resize $cols $height $now)
   (let* (($_ (cc:set "last-cols" $cols))
          ($_ (cc:set "last-height" $height))
          ($_ (cc:set "dirty" True))
          ($owner (cc:get "output" ConsoleNoBufferV1))
          ((HostBufferInfo $size $_) (buffer:info $owner))
          ($offset (cc:get "output-offset" 0))
          ($was-paint (cc:get "frame-is-paint" False)))
     (if (and (> $size 0) (and (== $offset 0) $was-paint))
       ; Unwritten stale-width paint frame: abort it cleanly before any byte hits the PTY.
       (progn (buffer:drop $owner $size)
              (cc:set "output-offset" 0)
              (cc:set "frame-is-paint" False)
              (cc:restore-frame-events)
              (if (cc:get "cpr-supported" True)
                (progn (cc:set "cpr-phase" "resize-drain")
                       (cc:set "resize-need-cpr" True)
                       (cc:set "resize-raced-inflight" False)
                       (cc:set "cpr-deadline" (+ $now 150000000)) True)
                (progn (cc:set "region-anchored" True) True)))
       ; Either empty output or a frame is partially written (must finish draining first).
       (let $in-flight (and (> $size $offset) (and (> $offset 0) $was-paint))
         (progn
           (if $in-flight (cc:set "resize-raced-inflight" True) True)
           (if (cc:get "cpr-supported" True)
             (progn (cc:set "cpr-phase" "resize-drain")
                    (cc:set "resize-need-cpr" True)
                    (cc:set "cpr-deadline" (+ $now 150000000)) True)
             (progn (cc:set "region-anchored" True) True)))))))

(= (cc:check-resize $terminal $now)
   (let (HostSize $cols $height) (terminal:size $terminal)
     (if (and (== $cols (cc:get "last-cols" 0)) (== $height (cc:get "last-height" 0)))
       False
       (progn (cc:note-resize $cols $height $now) True))))

; Handle a decoded CPR reply ("row;col") from buffer:input-event.
(= (cc:handle-cpr $payload)
   (case (cc:parse-cpr $payload)
     (((HostCursor $row $col)
       (let* (($_ (cc:set "cpr-supported" True))
              ($await (cc:get "cpr-awaiting" 0))
              ($phase (cc:get "cpr-phase" "synced")))
         (if (<= $await 0) True
           (let* (($rem (- $await 1))
                  ($_ (cc:set "cpr-awaiting" $rem)))
             (if (> $rem 0) True
               (case $phase
                 (("attach"
                   (let* (($height (max 1 (cc:get "last-height" 24)))
                          ($need-nl (and (> $col 1) (>= $row $height)))
                          ($start-row (if (> $col 1) (min $height (+ $row 1)) (max 1 (min $height $row))))
                          ($_ (cc:set "cpr-need-newline" $need-nl))
                          ($_ (cc:set "region-top" $start-row))
                          ($_ (cc:set "paint-top" $start-row))
                          ($_ (cc:set "region-anchored" (>= $start-row $height)))
                          ($_ (cc:set "cpr-phase" "synced"))
                          ($_ (cc:set "dirty" True)))
                     True))
                  ("post-events"
                   (let* (($height (max 1 (cc:get "post-events-height" (cc:get "last-height" 24))))
                          ($reserved (max 1 (cc:get "post-events-reserved" 1)))
                          ($clamped (max 1 (min $height $row)))
                          ($reached (>= (+ $clamped (- $reserved 1)) $height))
                          ($_ (if $reached
                                (let $top (max 1 (+ (- $height $reserved) 1))
                                  (progn (cc:set "region-anchored" True)
                                         (cc:set "region-top" $top)
                                         (cc:set "paint-top" $top) True))
                                (progn (cc:set "region-top" $clamped)
                                       (if (not (cc:get "post-events-parked" False))
                                         (cc:set "paint-top" $clamped) True) True)))
                          ($_ (cc:set "post-events-parked" False))
                          ($_ (cc:set "cpr-phase" "synced")))
                     True))
                  ("resize"
                   (let* (($cols (max 1 (cc:get "last-cols" 80)))
                          ($height (max 1 (cc:get "last-height" 24)))
                          ($clamped (max 1 (min $height $row)))
                          ($up (cc:rows-above-after-resize $cols $col))
                          ($erase-top (max 1 (- $clamped $up)))
                          ($_ (cc:set "resize-erase-top" $erase-top))
                          ($_ (cc:set "resize-raced-inflight" False))
                          ($_ (cc:set "region-top" $erase-top))
                          ($_ (cc:set "paint-top" $erase-top))
                          ($_ (cc:set "region-anchored" False))
                          ($_ (cc:set "cpr-phase" "synced"))
                          ($_ (cc:set "dirty" True)))
                     True))
                  ($other (cc:set "cpr-phase" "synced")))))))))
      ($bad True))))

(= (cc:cpr-check-timeout $now)
   (let $phase (cc:get "cpr-phase" "synced")
     (if (or (== $phase "synced") (== $phase "resize-drain")) False
       (if (< $now (cc:get "cpr-deadline" 0)) False
         (let* (($_ (if (== $phase "attach") (cc:set "cpr-supported" False) True))
                ($_ (cc:set "region-anchored" True))
                ($_ (cc:set "cpr-phase" "synced"))
                ($_ (cc:set "cpr-awaiting" 0))
                ($_ (cc:set "resize-raced-inflight" False))
                ($_ (cc:set "dirty" True)))
           True)))))

(= (cc:cpr-enqueue $now)
   (let* (($owner (cc:get "output" ConsoleNoBufferV1))
          ($added (buffer:append $owner (terminal:cpr-request))))
     (if (cc:buffer-failed? $added) False
       (let* (($_ (cc:set "output-offset" 0))
              ($_ (cc:set "frame-is-paint" False))
              ($_ (cc:set "frame-events" ""))
              ($_ (cc:set "resize-need-cpr" False))
              ($_ (cc:set "cpr-phase" "resize"))
              ($_ (cc:set "cpr-awaiting" (+ (cc:get "cpr-awaiting" 0) 1)))
              ($_ (cc:set "cpr-deadline" (+ $now 150000000))))
         True))))

(= (cc:prepare-frame $terminal $now)
   (let* (((HostSize $columns $height) (terminal:size $terminal))
          ($prev-cols (cc:get "last-cols" 0))
          ($prev-height (cc:get "last-height" 0)))
     (if (and (> $prev-cols 0) (or (!= $columns $prev-cols) (!= $height $prev-height)))
       (progn (cc:note-resize $columns $height $now) False)
       (let* (($_ (cc:set "last-cols" $columns))
              ($_ (cc:set "last-height" $height))
              ($small (or (< $columns 5) (< $height 5)))
              ($edit (cc:get "editor" ConsoleNoEditorV1))
              ((HostViewport $rows $cursor-row $cursor-column $first $total)
                (buffer:viewport $edit (max 1 (- $columns 2)) (if $small 1 (max 1 (min 8 (- $height 4))))))
              ($width (max 1 (- $columns 1)))
              ($_ (cc:set "display-width" $width))
              ($rule (cc:style "90" (cc:repeat "─" $width)))
              ($activity (cc:style "90" (cc:activity-text $width)))
              ($status (if (cc:get "search-active" False)
                         (cc:search-bar $width)
                         (cc:status-text $width)))
              ($lines (if $small ((terminal:clip (str:concat "> " (car-atom $rows)) $width))
                        (list:append ($rule $activity) (list:append (cc:editor-rows $rows True) ($status)))))
              ($reserved (size-atom $lines))
              ($cursor-line (if $small 0 (+ 2 $cursor-row)))
              ($target-column (min $columns (+ 3 $cursor-column)))
              ($event-owner (cc:get "events" ConsoleNoBufferV1)) ($events (buffer:value $event-owner))
              ($frame-owner (cc:get "output" ConsoleNoBufferV1))
              ($screen (str:join "\r\n" $lines))
              ($need-nl (cc:get "cpr-need-newline" False))
              ($lead-nl (if $need-nl "\r\n" ""))
              ($resize-erase (cc:get "resize-erase-top" 0))
              ($anchored (cc:get "region-anchored" False))
              ($rtop (max 1 (cc:get "region-top" 1)))
              ($ptop (max 1 (cc:get "paint-top" $rtop)))
              ($old-rows (cc:get "region-rows" 0))
              ($top (max 1 (+ (- $height $reserved) 1)))
              ($ended (if (or (== $events "") (str:ends-with $events "\n")) $events (str:concat $events "\n")))
              ($scroll-events (str:join "\r\n" (str:split "\n" $ended)))
              ($ev-rows (cc:event-rows $events $columns))
              ($after-ev-rtop (+ $rtop $ev-rows))
              ($want-park (and (not $anchored)
                               (and (cc:get "cpr-supported" True)
                                    (and (< $after-ev-rtop $top)
                                         (cc:needs-park? $lines $columns $after-ev-rtop $height $reserved)))))
              ; Decide inline vs bottom-anchored frame payload:
              ($will-anchor (or $anchored (or (not (cc:get "cpr-supported" True))
                                              (and (== $events "") (>= (+ $rtop (- $reserved 1)) $height)))))
              ($clear-top (let $base (min $rtop $ptop) (if (> $resize-erase 0) (min $resize-erase $base) $base)))
              ($new-ptop (if (or $will-anchor $want-park) $top $rtop))
              ($raw-frame
                (if $anchored
                  ; Bottom-anchored painter (once region reached bottom):
                  (let* (($base-old-top (if (> $old-rows 0) (max 1 (+ (- $height $old-rows) 1)) $height))
                         ($old-top (if (> $resize-erase 0) (min $resize-erase $base-old-top) $base-old-top))
                         ($prefix (str:join "" ("\u001b[?25l" (cc:erase-from $old-top $height) "\u001b[?7h" $lead-nl)))
                         ($scroll (str:concat (str:join "\r\n" (str:split "\n" $events)) (cc:repeat "\r\n" (- $reserved 1))))
                         ($target-row (if $small $height (+ $top (+ 2 $cursor-row)))))
                    (str:join "" ($prefix $scroll (cc:cup $top 1) "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                  (cc:cup $target-row $target-column) "\u001b[?25h")))
                  (if (== $events "")
                    ; Inline mode without new scrollback:
                    (if (>= (+ $rtop (- $reserved 1)) $height)
                      ; Live region just grew to reach the bottom: scroll minimally and lock bottom-anchored.
                      (let $target-row (if $small $height (+ $top (+ 2 $cursor-row)))
                        (str:join "" ("\u001b[?25l" (cc:erase-from $clear-top $height) "\u001b[?7h" $lead-nl
                                      (cc:cup $rtop 1) (cc:repeat "\r\n" (- $reserved 1))
                                      (cc:cup $top 1) "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                      (cc:cup $target-row $target-column) "\u001b[?25h")))
                      (if $want-park
                        ; Tall/wide draft in tmux: temporarily park live region at bottom so
                        ; tmux grid_reflow never spills live-region lines into scrollback.
                        (let* (($erase-at (if (> $resize-erase 0) (min $resize-erase $ptop) (min $ptop $top)))
                               ($target-row (if $small $height (+ $top (+ 2 $cursor-row)))))
                          (str:join "" ("\u001b[?25l" (cc:erase-from $erase-at $height)
                                        (cc:cup $top 1) "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                        (cc:cup $target-row $target-column) "\u001b[?25h")))
                        ; Fits inline directly at $rtop with zero blank lines.
                        (let $target-row (+ $rtop $cursor-line)
                          (str:join "" ("\u001b[?25l" (cc:erase-from $clear-top $height) "\u001b[?7h" $lead-nl
                                        (cc:cup $rtop 1) "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                        (cc:cup $target-row $target-column) "\u001b[?25h")))))
                    ; Inline mode WITH new scrollback: write events at $rtop (autowrap ON),
                    ; query exact post-events row via terminal:cpr-request, then paint region.
                    (if $want-park
                      (let* (($cpr-ask (terminal:cpr-request))
                             ($target-row (if $small $height (+ $top (+ 2 $cursor-row)))))
                        (str:join "" ("\u001b[?25l" (cc:erase-from $clear-top $height) "\u001b[?7h" $lead-nl
                                      (cc:cup $rtop 1) $scroll-events $cpr-ask
                                      (cc:cup $top 1) "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                      (cc:cup $target-row $target-column) "\u001b[?25h")))
                      (let* (($up-after (- (- $reserved 1) $cursor-line))
                             ($cpr-ask (if (cc:get "cpr-supported" True) (terminal:cpr-request) "")))
                        (str:join "" ("\u001b[?25l" (cc:erase-from $clear-top $height) "\u001b[?7h" $lead-nl
                                      (cc:cup $rtop 1) $scroll-events $cpr-ask
                                      "\u001b[?7l" $screen "\u001b[0m\u001b[?7h"
                                      (cc:cursor-up $up-after) (cc:cha $target-column) "\u001b[?25h")))))))
              ($frame (terminal:sync $raw-frame))
              ($added (buffer:append $frame-owner $frame)))
         (if (cc:buffer-failed? $added) (progn (cc:set "display-fault" "Terminal frame allocation refused") False)
           (let* (($_ (buffer:drop $event-owner (str:byte-length $events)))
                  ($_ (cc:set "frame-events" $events))
                  ($_ (cc:set "frame-cols" $columns))
                  ($_ (cc:set "frame-height" $height))
                  ($_ (cc:set "frame-is-paint" True))
                  ($_ (cc:set "output-offset" 0))
                  ($_ (cc:set "dirty" False))
                  ($_ (cc:set "cpr-need-newline" False))
                  ($_ (cc:set "resize-erase-top" 0))
                  ($_ (cc:set "region-rows" $reserved))
                  ($_ (cc:set "paint-top" $new-ptop))
                  ($_ (cc:set "painted-lines" (map-atom $lines $line (terminal:clean $line))))
                  ($_ (cc:set "painted-cols" $columns))
                  ($_ (cc:set "painted-height" $height))
                  ($_ (cc:set "painted-cursor-line" $cursor-line))
                  ($_ (cc:set "painted-cursor-column" (- $target-column 1)))
                  ($_ (if $will-anchor
                        (progn (cc:set "region-anchored" True)
                               (cc:set "region-top" $top)
                               (cc:set "paint-top" $top) True)
                        (if (and (!= $events "") (cc:get "cpr-supported" True))
                          (progn (cc:set "cpr-phase" "post-events")
                                 (cc:set "cpr-awaiting" (+ (cc:get "cpr-awaiting" 0) 1))
                                 (cc:set "cpr-deadline" (+ $now 150000000))
                                 (cc:set "post-events-reserved" $reserved)
                                 (cc:set "post-events-height" $height)
                                 (cc:set "post-events-parked" $want-park) True)
                          True))))
             True))))))

(= (cc:detach-sequence $terminal)
   (let* (((HostSize $_ $height) (terminal:size $terminal))
          ($rows (max 1 (cc:get "region-rows" 0)))
          ($top (if (cc:get "region-anchored" True)
                  (max 1 (+ (- $height $rows) 1))
                  (max 1 (min $height (cc:get "region-top" 1))))))
     (str:join "" ((cc:erase-from $top $height)
       "\u001b[0m\u001b[?7h\u001b[?25h\u001b[?2004lDetached; cognition continues.\r\n"))))

(= (cc:publish $terminal $now)
   (let* (($owner (cc:get "output" ConsoleNoBufferV1)) ((HostBufferInfo $size $_) (buffer:info $owner))
          ($offset (cc:get "output-offset" 0)))
     (if (<= $size $offset)
       (progn (if (> $size 0) (buffer:drop $owner $size) True)
         (cc:set "output-offset" 0)
         (cc:set "frame-is-paint" False)
         (cc:set "frame-events" "")
         (if (cc:get "resize-need-cpr" False)
           (if (cc:cpr-enqueue $now) (cc:publish $terminal $now) False)
           (if (and (== (cc:get "cpr-phase" "synced") "synced") (cc:get "dirty" False))
             (if (cc:prepare-frame $terminal $now)
               (cc:publish $terminal $now)
               (if (cc:get "resize-need-cpr" False) (cc:publish $terminal $now) False))
             True)))
       ; Before writing the first byte of a paint frame, verify size has not changed!
       (if (and (== $offset 0) (and (cc:get "frame-is-paint" False) (cc:check-resize $terminal $now)))
         (cc:publish $terminal $now)
         (case (host:write-buffer $terminal $owner $offset 65536)
           (((HostWritten $bytes)
             (let $next (+ $offset $bytes)
               (progn (cc:set "output-offset" $next)
                 (cc:set "physical-bytes" (+ (cc:get "physical-bytes" 0) $bytes))
                 (if (and (>= $next $size) (cc:get "resize-need-cpr" False))
                   (cc:publish $terminal $now) True))))
            (HostPending True)
            ($failed (progn (cc:set "stop" True) (cc:set "display-fault" "Terminal write failed; source records remain intact")))))))))
