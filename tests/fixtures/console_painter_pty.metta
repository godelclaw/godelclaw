!(import! &self ../native_test_support.metta)
!(import! &self ../../src/console/main_cetta.metta)

(= (cpp:wait-resize $fd $old-cols $old-height $left)
   (let (HostSize $c $h) (terminal:size $fd)
     (if (or (!= $c $old-cols) (!= $h $old-height)) (HostSize $c $h)
       (if (<= $left 0) (HostSize $c $h)
         (progn (host:wait (($fd resize)) 20)
                (cpp:wait-resize $fd $old-cols $old-height (- $left 1)))))))

(= (cpp:write $fd $text $at)
   (if (>= $at (str:byte-length $text)) True
     (case (host:write $fd $text $at)
       (((HostWritten $n) (cpp:write $fd $text (+ $at $n)))
        (HostPending (progn (host:wait (($fd write)) 50) (cpp:write $fd $text $at)))
        ($bad False)))))

!(let* (($home (nt:required-env "HOME"))
        ($client (path:join $home "client"))
        ($_ (fs:make-directories $client 448))
        ($settings (JsonObject ((JsonPair "client" $client) (JsonPair "agent" "pty-test"))))
        ($_ (cc:initialize $settings))
        ($fd (terminal:open "/dev/tty"))
        ($_ (terminal:raw $fd on))
        ((HostSize $c0 $h0) (terminal:size $fd))
        ($_ (cc:cpr-begin-attach $fd 1000))
        ($_ (nt:require attach-records-initial-size
              (and (== (cc:get "last-cols" 0) $c0) (== (cc:get "last-height" 0) $h0))))
        ($_ (cc:handle-cpr "2;1"))
        ($_ (nt:require attach-inline-synced
              (and (== (cc:get "cpr-phase" "") "synced")
                   (and (== (cc:get "region-top" 0) 2)
                        (== (cc:get "region-anchored" True) False)))))

        ; Tell parent fixture we are ready for resize S 64 20
        ($_ (cpp:write $fd "PAINTER_PTY_READY\n" 0))
        ((HostSize $c1 $h1) (cpp:wait-resize $fd $c0 $h0 150))
        ($_ (nt:require pty-resized-to-64x20 (and (== $c1 64) (== $h1 20))))

        ; Critical race guard: call cc:prepare-frame directly WITHOUT cc:check-resize first.
        ; It must detect the size change, invoke cc:note-resize, return False, and request CPR.
        ($prep-raced (cc:prepare-frame $fd 2000))
        ($_ (nt:require prepare-frame-catches-size-race
              (and (== $prep-raced False)
                   (and (== (cc:get "resize-need-cpr" False) True)
                        (and (== (cc:get "cpr-phase" "") "resize-drain")
                             (and (== (cc:get "last-cols" 0) 64)
                                  (== (cc:get "last-height" 0) 20)))))))

        ; cc:publish must enqueue and write terminal:cpr-request (\x1b[6n) immediately.
        ($_ (cc:publish $fd 2500))
        ($_ (nt:require publish-enqueued-resize-cpr
              (and (== (cc:get "cpr-phase" "") "resize")
                   (and (== (cc:get "resize-need-cpr" True) False)
                        (== (cc:get "cpr-awaiting" 0) 1)))))

        ; Simulate CPR reply placing live region at row 1 (must use EL2+ED0 row-2, never ED0 at 1,1).
        ($_ (cc:handle-cpr "1;1"))
        ($_ (nt:require resize-cpr-resynced-row-1
              (and (== (cc:get "cpr-phase" "") "synced")
                   (and (== (cc:get "region-top" 0) 1)
                        (== (cc:get "region-anchored" True) False)))))
        ($prep-ok (cc:prepare-frame $fd 3000))
        ($frame-bytes (buffer:value (cc:get "output" ConsoleNoBufferV1)))
        ($_ (nt:require inline-row1-frame-safe-erase-and-sync
              (and (== $prep-ok True)
                   (and (nt:contains $frame-bytes "\u001b[?2026h")
                        (and (nt:contains $frame-bytes "\u001b[?2026l")
                             (and (nt:contains $frame-bytes "\u001b[1;1H\u001b[2K\u001b[2;1H\u001b[J\u001b[1;1H")
                                  (not (nt:contains $frame-bytes "\u001b[1;1H\u001b[J"))))))))

        ; Detach sequences: row-1 inline uses safe erase; bottom-anchored uses cup(height-rows+1, 1) + ED0.
        ($det-inline (cc:detach-sequence $fd))
        ($_ (nt:require detach-inline-row1-safe
              (and (nt:contains $det-inline "\u001b[1;1H\u001b[2K\u001b[2;1H\u001b[J\u001b[1;1H")
                   (nt:contains $det-inline "Detached; cognition continues."))))
        ($_ (cc:set "region-anchored" True))
        ($_ (cc:set "region-rows" 4))
        ($det-bottom (cc:detach-sequence $fd))
        ($_ (nt:require detach-bottom-anchored
              (and (nt:contains $det-bottom "\u001b[17;1H\u001b[J")
                   (nt:contains $det-bottom "Detached; cognition continues."))))

        ($_ (cpp:write $fd "PAINTER_PTY_PASS\n" 0))
        ($_ (host:close $fd)))
   True)
