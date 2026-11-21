!(import! &self ../native_test_support.metta)
!(import! &self ../../src/console/render_cetta.metta)
; The painter moves up from the cursor to the top of the region it painted.
; In a narrowed tmux pane each painted line has reflowed into ceil(cells /
; columns) rows, and the cursor stays on its character, never past the
; rows of its own line.
(= (paint:up $widths $line $column $columns)
   (let* (($_ (cc:set "painted-widths" $widths)) ($_ (cc:set "painted-cursor-line" $line))
          ($_ (cc:set "painted-cursor-column" $column)))
     (cc:rows-above-cursor $columns)))
!(let* (($_ (nt:require inside-tmux (!= (system:getenv-or-default "TMUX" "") "")))
        ($_ (nt:require nothing-painted (== (paint:up () 0 0 80) 0)))
        ($_ (nt:require same-width (== (paint:up (191 40 10 30) 2 5 192) 2)))
        ($_ (nt:require narrowed (== (paint:up (191 40 10 30) 2 5 64) 4)))
        ($_ (nt:require cursor-in-wrapped-line (== (paint:up (10 10 130 5) 2 130 64) 4)))
        ($_ (nt:require cursor-at-exact-width (== (paint:up (10 10 64 5) 2 64 64) 2)))
        ($_ (nt:require no-move (== (cc:cursor-up 0) "")))
        ($_ (nt:require move-up (== (cc:cursor-up 3) "\u001b[3A"))))
   (println! UNIT_CONSOLE_PAINTER_OK))
