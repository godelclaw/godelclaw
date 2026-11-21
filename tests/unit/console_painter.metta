!(import! &self ../native_test_support.metta)
!(import! &self ../native_line_child.metta)
!(import! &self ../../src/console/main_cetta.metta)

(= (cp:buf-size $key)
   (let (HostBufferInfo $size $_) (buffer:info (cc:get $key ConsoleNoBufferV1)) $size))

(= (cp:pty-until $job $buf $capture $needle $left)
   (if (and (fs:is-file $capture) (nt:contains (fs:read-text $capture) $needle)) True
     (if (<= $left 0) False
       (progn (nline:request $job $buf "R 50\n" 2000)
              (cp:pty-until $job $buf $capture $needle (- $left 1))))))

(= (cp:pty-ended $job $buf $left)
   (let $rec (nline:request $job $buf "R 50\n" 2000)
     (if (or (sf:get $rec "ended" False) (<= $left 0)) $rec
       (cp:pty-ended $job $buf (- $left 1)))))

(= (cp:run-pty-check)
   (let* (($home (nt:required-env "HOME"))
          ($root (nt:required-env "TEST_REPOSITORY"))
          ($binary (system:executable))
          ($fixture (nt:required-env "TEST_PTY_FIXTURE"))
          ($capture (path:join $home "painter-pty.bin"))
          ($program (path:join $root "tests/fixtures/console_painter_pty.metta"))
          ($argv ($fixture $capture $binary "--no-results" "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk" $program))
          ($job (proc:spawn $argv ((mode stream) (max-bytes 65536) (lifetime owner) (timeout-ms 15000))))
          ($buf (buffer:new 65536 bytes))
          ($_ (nt:require painter-pty-ready (cp:pty-until $job $buf $capture "PAINTER_PTY_READY" 80)))
          ($_ (nline:request $job $buf "S 64 20\n" 2000))
          ($_ (nt:require painter-pty-pass (cp:pty-until $job $buf $capture "PAINTER_PTY_PASS" 80)))
          ($stopped (cp:pty-ended $job $buf 40))
          ($_ (nt:require painter-pty-clean-exit (and (sf:get $stopped "ended" False) (== (sf:get $stopped "status" -1) 0))))
          ($_ (nt:require painter-pty-cpr-sent (nt:contains (fs:read-text $capture) "\u001b[6n")))
          ($_ (proc:close-input $job)) ($_ (proc:await $job)) ($_ (proc:close $job)) ($_ (host:close $buf)))
     True))

!(let* (($home (nt:required-env "HOME"))
        ($client (path:join $home "client"))
        ($_ (fs:make-directories $client 448))
        ($settings (JsonObject ((JsonPair "client" $client) (JsonPair "agent" "paint-test"))))
        ($_ (cc:initialize $settings))

        ; --- 1. cc:erase-from avoids tmux ED0-at-(1,1) scrollback push ---
        ($_ (nt:require erase-from-row5 (== (cc:erase-from 5 20) "\u001b[5;1H\u001b[J")))
        ($_ (nt:require erase-from-row1-multi (== (cc:erase-from 1 20) "\u001b[1;1H\u001b[2K\u001b[2;1H\u001b[J\u001b[1;1H")))
        ($_ (nt:require erase-from-row1-single (== (cc:erase-from 1 1) "\u001b[1;1H\u001b[2K")))

        ; --- 2. Attach CPR handshake (fresh, prefilled, mid-line, bottom mid-line, timeout) ---
        ($_ (cc:set "last-cols" 110)) ($_ (cc:set "last-height" 28))
        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:handle-cpr "2;1"))
        ($_ (nt:require attach-fresh-inline
              (and (== (cc:get "region-top" 0) 2)
                   (and (== (cc:get "paint-top" 0) 2)
                        (and (== (cc:get "region-anchored" True) False)
                             (and (== (cc:get "cpr-need-newline" True) False)
                                  (== (cc:get "cpr-phase" "") "synced")))))))

        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:handle-cpr "28;1"))
        ($_ (nt:require attach-prefilled-anchored
              (and (== (cc:get "region-top" 0) 28)
                   (and (== (cc:get "region-anchored" False) True)
                        (== (cc:get "cpr-need-newline" True) False)))))

        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:handle-cpr "5;15"))
        ($_ (nt:require attach-midline-advances-row
              (and (== (cc:get "region-top" 0) 6)
                   (and (== (cc:get "region-anchored" True) False)
                        (== (cc:get "cpr-need-newline" True) False)))))

        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:handle-cpr "28;15"))
        ($_ (nt:require attach-bottom-midline-needs-newline
              (and (== (cc:get "region-top" 0) 28)
                   (and (== (cc:get "region-anchored" False) True)
                        (== (cc:get "cpr-need-newline" False) True)))))

        ; Timeout on dumb non-CPR PTY falls back cleanly to bottom-anchored
        ($_ (cc:render-reset))
        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:set "cpr-deadline" 1000))
        ($_ (nt:require cpr-timeout-not-before-deadline (== (cc:cpr-check-timeout 999) False)))
        ($_ (nt:require cpr-timeout-fallback
              (and (== (cc:cpr-check-timeout 1000) True)
                   (and (== (cc:get "cpr-supported" True) False)
                        (and (== (cc:get "region-anchored" False) True)
                             (and (== (cc:get "cpr-phase" "") "synced")
                                  (== (cc:get "cpr-awaiting" 1) 0)))))))

        ; --- 3. Post-events CPR: inline update, parked preservation, permanent bottom lock ---
        ($_ (cc:render-reset))
        ($_ (cc:set "cpr-phase" "post-events")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:set "post-events-height" 28)) ($_ (cc:set "post-events-reserved" 4))
        ($_ (cc:set "post-events-parked" False))
        ($_ (cc:handle-cpr "6;1"))
        ($_ (nt:require post-events-stays-inline
              (and (== (cc:get "region-top" 0) 6)
                   (and (== (cc:get "paint-top" 0) 6)
                        (== (cc:get "region-anchored" True) False)))))

        ($_ (cc:set "paint-top" 21))
        ($_ (cc:set "cpr-phase" "post-events")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:set "post-events-parked" True))
        ($_ (cc:handle-cpr "9;1"))
        ($_ (nt:require post-events-parked-keeps-paint-top
              (and (== (cc:get "region-top" 0) 9)
                   (and (== (cc:get "paint-top" 0) 21)
                        (and (== (cc:get "post-events-parked" True) False)
                             (== (cc:get "region-anchored" True) False))))))

        ($_ (cc:set "cpr-phase" "post-events")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:handle-cpr "25;1"))
        ($_ (nt:require post-events-locks-bottom-anchored
              (and (== (cc:get "region-anchored" False) True)
                   (and (== (cc:get "region-top" 0) 25)
                        (== (cc:get "paint-top" 0) 25)))))

        ; --- 4. Unwritten stale-width frame abort restores events; in-flight drains ---
        ($_ (cc:render-reset))
        ($_ (buffer:append (cc:get "output" ConsoleNoBufferV1) "STALE-110-COL-FRAME"))
        ($_ (cc:set "output-offset" 0))
        ($_ (cc:set "frame-is-paint" True))
        ($_ (cc:set "frame-events" "FIRST-LINE\n"))
        ($_ (buffer:append (cc:get "events" ConsoleNoBufferV1) "SECOND-LINE\n"))
        ($_ (cc:note-resize 64 20 5000))
        ($_ (nt:require unwritten-frame-aborted-and-events-restored
              (and (== (cp:buf-size "output") 0)
                   (and (== (buffer:value (cc:get "events" ConsoleNoBufferV1)) "FIRST-LINE\nSECOND-LINE\n")
                        (and (== (cc:get "frame-events" "x") "")
                             (and (== (cc:get "resize-need-cpr" False) True)
                                  (and (== (cc:get "resize-raced-inflight" True) False)
                                       (== (cc:get "cpr-phase" "") "resize-drain"))))))))

        ; Partially written frame is NOT dropped; sets resize-raced-inflight = True
        ($_ (buffer:append (cc:get "output" ConsoleNoBufferV1) "PARTIAL-FRAME-BYTES"))
        ($_ (cc:set "output-offset" 7))
        ($_ (cc:set "frame-is-paint" True))
        ($_ (cc:note-resize 48 16 6000))
        ($_ (nt:require inflight-frame-kept-to-drain
              (and (> (cp:buf-size "output") 0)
                   (and (== (cc:get "resize-raced-inflight" False) True)
                        (== (cc:get "resize-need-cpr" False) True)))))

        ; --- 5. Rapid multi-resize cpr-awaiting ignores stale earlier CPR ---
        ($_ (buffer:drop (cc:get "output" ConsoleNoBufferV1) (cp:buf-size "output")))
        ($_ (cc:render-reset))
        ($_ (cc:set "last-cols" 64)) ($_ (cc:set "last-height" 20))
        ($_ (cc:set "painted-lines" ("line0" "line1" "line2")))
        ($_ (cc:set "painted-cols" 64)) ($_ (cc:set "painted-cursor-line" 2))
        ($_ (cc:set "painted-cursor-column" 2))
        ($_ (cc:cpr-enqueue 7000))
        ($_ (cc:cpr-enqueue 7100))
        ($_ (nt:require two-cprs-awaiting (== (cc:get "cpr-awaiting" 0) 2)))
        ($_ (cc:handle-cpr "10;3"))
        ($_ (nt:require stale-first-cpr-ignored
              (and (== (cc:get "cpr-awaiting" 0) 1)
                   (== (cc:get "cpr-phase" "") "resize"))))
        ($_ (cc:handle-cpr "14;3"))
        ($_ (nt:require second-cpr-completes-resync
              (and (== (cc:get "cpr-awaiting" 1) 0)
                   (and (== (cc:get "cpr-phase" "") "synced")
                        (== (cc:get "region-top" 0) 12)))))

        ; --- 6. Wide-char tmux 3.6 3-slot vs 2-cell CPR disambiguation & needs-park? ---
        ($wide-line (str:concat "› " (cc:repeat "雪花🌿" 10))) ; 2 + 60 cells (30 wide graphemes)
        ($rule109 (cc:repeat "─" 109))
        ($_ (cc:set "painted-lines" ($rule109 "Enter sends" $wide-line "status")))
        ($_ (cc:set "painted-cols" 110))
        ($_ (cc:set "painted-cursor-line" 2))
        ($_ (cc:set "painted-cursor-column" 62))
        ($_ (cc:set "resize-raced-inflight" False))
        ; At 64 cols: standard 2-cell width = 2 + 60 = 62 -> col 63 (0 extra rows);
        ; tmux 3.6 3-slot width: 2 + 3*30 = 92 -> wraps to 2nd row (1 extra row, col != 63).
        ($std-up (cc:rows-above-after-resize 64 63))
        ($t36-up (cc:rows-above-after-resize 64 31))
        ($_ (nt:require wide-cpr-distinguishes-std-vs-tmux36
              (and (== $std-up 3) (== $t36-up 4))))
        ; Widening never double-counts
        ($_ (nt:require widen-returns-cur-line (== (cc:rows-above-after-resize 120 63) 2)))
        ; In-flight raced returns cur-line
        ($_ (cc:set "resize-raced-inflight" True))
        ($_ (nt:require raced-returns-cur-line (== (cc:rows-above-after-resize 64 31) 2)))
        ($_ (cc:set "resize-raced-inflight" False))

        ; cc:needs-park?: empty fresh attach stays inline; tall wide draft parks safely
        ($empty-lines ($rule109 "Enter sends · Alt-Enter new line · /help commands · Ctrl-D detaches" "› " "agent · ok"))
        ($tall-lines ($rule109 "Enter sends · Alt-Enter new line · /help commands · Ctrl-D detaches"
                      $wide-line $wide-line $wide-line $wide-line "agent · ok"))
        ($_ (nt:require empty-fresh-attach-stays-inline (== (cc:needs-park? $empty-lines 110 4 28 4) False)))
        ($_ (nt:require tall-wide-fresh-draft-parks (== (cc:needs-park? $tall-lines 110 4 28 7) True)))
        ($_ (nt:require event-rows-counts-lines (== (cc:event-rows "line1\nline2\n" 80) 2)))

        ; --- 7. cc:keys handles cursor-position without modifying draft or draft-dirty ---
        ($_ (cc:render-reset))
        ($_ (cc:set "last-cols" 110)) ($_ (cc:set "last-height" 28))
        ($_ (cc:set "cpr-phase" "attach")) ($_ (cc:set "cpr-awaiting" 1))
        ($_ (cc:set "draft-dirty" False))
        ($in (buffer:new 128 bytes))
        ($_ (buffer:append $in "\u001b[12;1R"))
        ($_ (cc:keys $in flush 8))
        ($_ (host:close $in))
        ($_ (nt:require keys-cpr-updates-region-without-dirtying-draft
              (and (== (cc:get "region-top" 0) 12)
                   (and (== (cc:get "draft-dirty" True) False)
                        (== (buffer:value (cc:get "editor" ConsoleNoEditorV1)) "")))))

        ; --- 8. Real PTY check of cc:prepare-frame race guard, cc:publish CPR, and cc:detach-sequence ---
        ($_ (cp:run-pty-check)))
   (println! UNIT_CONSOLE_PAINTER_OK))
