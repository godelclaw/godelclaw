; Unit tests of channels/telegram_control_io.metta, the command responder's
; I/O between its answers, against a scripted channel service
; (tests/fixtures/native_cwp_peer.c); no Python. The expected behaviour is
; that of the Python it replaces (mode_channel.pump and heartbeat,
; input_ack.arm_changes and wait_changes), with the differences the module
; states: an empty outbox is not touched, and a file that is not an intent is
; set aside instead of stopping the outbox.
!(import! &self ../native_test_support.metta)
!(import! &self ../../channels/telegram_control_io.metta)

(= (cio:home) (nt:required-env "HOME"))
(= (cio:peer-dir) (path:join (cio:home) "peer"))
(= (cio:outbox) (tio:outbox))
(= (cio:write $path $text) (let $_ (fs:make-directories (path:dirname $path) 448) (fs:write $path $text)))
(= (cio:script $name $text) (cio:write (path:join (cio:peer-dir) $name) $text))
(= (cio:unscript $name) (let $path (path:join (cio:peer-dir) $name) (if (fs:exists $path) (fs:remove $path) True)))
(= (cio:requests)
   (let $path (path:join (cio:peer-dir) "requests")
     (if (fs:exists $path) (filter-atom (str:split "\n" (fs:read-text $path)) $line (not (== $line ""))) ())))
(= (cio:clear-requests)
   (let $path (path:join (cio:peer-dir) "requests") (if (fs:exists $path) (fs:remove $path) True)))
; An intent as Python's mode_channel.store writes it (json.dump, ensure_ascii=False).
(= (cio:intent $key $text)
   (cio:write (path:join (cio:outbox) (str:concat $key ".json"))
     (str:join "" ("{\"key\": \"" $key "\", \"commands\": [[\"send\", \"" $text "\", \"plain\"]]}"))))
(= (cio:exists $name) (fs:exists (path:join (cio:outbox) $name)))
(= (cio:wait-file $path $left)
   (if (fs:exists $path) True
     (if (<= $left 0) False (let $_ (sleep 0.02) (cio:wait-file $path (- $left 1))))))
(= (cio:ms-since $started) (/ (- (system:monotonic-ns) $started) 1000000))
(= (cio:timed-wait)
   (let* (($armed (tio:arm)) ($started (system:monotonic-ns)) ($result (tio:wait $armed)))
     ($result (cio:ms-since $started))))
(= (cio:passed $block) (let $_ (add-atom &self (cio:block-passed $block)) (println! $block)))

!(let* (($home (cio:home)) ($root (system:cwd))
        ($peer (path:join $home "native-cwp-peer"))
        ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror" "tests/fixtures/native_cwp_peer.c" "-o" $peer)
                                  $root (nt:environment $home) 30000 65536)))
        ($_ (fs:make-directories (cio:peer-dir) 448))
        ($server (proc:spawn ($peer (tio:socket) (cio:peer-dir)) ((cwd $root) (env (nt:environment $home)) (max-bytes 65536))))
        ($_ (add-atom &self (cio:server $server)))
        ($_ (nt:require peer-ready (cio:wait-file (path:join (cio:peer-dir) "ready") 500))))
   (cio:passed UNIT_CONTROL_IO_PEER_OK))

; The outbox: one intent a cycle, first by name, in the bytes Python sent.
!(let* (($_ (nt:require empty-outbox-submits-nothing (nt:equal (tio:pump) 0)))
        ($_ (nt:require empty-outbox-is-not-touched (not (cio:exists "pump.lock"))))
        ($_ (nt:require empty-outbox-asks-nothing (nt:equal (cio:requests) ())))
        ($_ (cio:intent "42.0.002" "second"))
        ($_ (cio:intent "42.0.001" "žluť \\\"q\\\" ✓"))
        ($_ (nt:require first-stored (nt:equal (tio:pump) 1)))
        ($_ (nt:require first-by-name-in-python-bytes
              (nt:equal (cio:requests) ("4\t42.0.001\t[[\"send\",\"žluť \\\"q\\\" ✓\",\"plain\"]]"))))
        ($_ (nt:require stored-intent-removed (not (cio:exists "42.0.001.json"))))
        ($_ (nt:require later-intent-kept (cio:exists "42.0.002.json")))
        ($_ (nt:require second-stored-next-cycle (nt:equal (tio:pump) 1)))
        ($_ (nt:require outbox-drained (nt:equal (tio:intents (cio:outbox)) ())))
        ($_ (cio:clear-requests)))
   (cio:passed UNIT_CONTROL_IO_ORDER_OK))

; An unavailable or full service keeps the intent; a refusal is a receipt.
!(let* (($_ (cio:intent "42.0.010" "held"))
        ($_ (cio:script "reply-4" "71"))
        ($_ (nt:require unavailable-keeps (nt:equal ((tio:pump) (cio:exists "42.0.010.json")) (0 True))))
        ($_ (cio:script "reply-4" "72"))
        ($_ (nt:require limit-keeps (nt:equal ((tio:pump) (cio:exists "42.0.010.json")) (0 True))))
        ($_ (nt:require same-key-same-bytes-each-time
              (nt:equal (cio:requests) ("4\t42.0.010\t[[\"send\",\"held\",\"plain\"]]" "4\t42.0.010\t[[\"send\",\"held\",\"plain\"]]"))))
        ($_ (cio:script "reply-4" "69"))
        ($_ (nt:require refused (nt:equal (tio:pump) 0)))
        ($_ (nt:require refused-intent-removed (not (cio:exists "42.0.010.json"))))
        ($receipt (json:parse (fs:read-text (path:join (cio:outbox) "42.0.010.refused"))))
        ($_ (nt:require refusal-receipt-keeps-the-intent-and-code
              (nt:equal ((sf:get $receipt "key" none) (sf:get $receipt "code" none)) ("42.0.010" 69))))
        ($_ (nt:require refusal-is-final (nt:equal (tio:intents (cio:outbox)) ())))
        ($_ (cio:unscript "reply-4"))
        ($_ (cio:clear-requests)))
   (cio:passed UNIT_CONTROL_IO_REFUSAL_OK))

; A file that is not an intent is set aside; the next intent still goes.
!(let* (($_ (cio:write (path:join (cio:outbox) "42.0.020.json") "not json"))
        ($_ (cio:write (path:join (cio:outbox) "42.0.021.json") "{\"commands\": []}"))
        ($_ (cio:intent "42.0.022" "after"))
        ($_ (nt:require unreadable-set-aside (nt:equal ((tio:pump) (cio:exists "42.0.020.unreadable")) (0 True))))
        ($_ (nt:require keyless-set-aside (nt:equal ((tio:pump) (cio:exists "42.0.021.unreadable")) (0 True))))
        ($_ (nt:require nothing-sent-for-them (nt:equal (cio:requests) ())))
        ($_ (nt:require next-intent-goes (nt:equal (tio:pump) 1)))
        ($_ (cio:clear-requests)))
   (cio:passed UNIT_CONTROL_IO_UNREADABLE_OK))

; A pump that finds the lock held submits nothing.
!(let* (($_ (cio:intent "42.0.030" "locked"))
        ($lock (fs:open-write (path:join (cio:outbox) "pump.lock") ("create" "append") 438))
        ($_ (nt:require other-pump-holds (nt:equal (fs:lock $lock exclusive-nowait) fs:locked)))
        ($_ (nt:require held-lock-submits-nothing (nt:equal ((tio:pump) (cio:requests) (cio:exists "42.0.030.json")) (0 () True))))
        ($_ (fs:unlock $lock)) ($_ (fs:close $lock))
        ($_ (nt:require released-lock-submits (nt:equal (tio:pump) 1)))
        ($_ (cio:clear-requests)))
   (cio:passed UNIT_CONTROL_IO_LOCK_OK))

; The readiness record: written at most every five seconds; the inbox view
; only while the service answers.
(= (cio:status) (sf:native-object (path:join (tio:mode-root) "control-status.json")))
(= (cio:view) (JsonObject ((JsonPair "pending" (JsonNumber "2")) (JsonPair "observedAt" (JsonNumber "5.5")))))
!(let* (($before (system:wall-time))
        ($_ (nt:require record-written (nt:equal (tio:heartbeat (cio:view)) 1)))
        ($status (cio:status))
        ($_ (nt:require record-names-this-process (nt:equal (sf:get $status "pid" none) (system:pid))))
        ($_ (nt:require record-ready (nt:equal (sf:get $status "channel_ready" none) True)))
        ($_ (nt:require record-carries-the-view
              (nt:equal (sf:get $status "operator_inbox" none) (JsonObject ((JsonPair "pending" 2) (JsonPair "observedAt" 5.5))))))
        ($_ (nt:require record-is-dated (>= (sf:get $status "observed_at" 0) $before)))
        ($_ (nt:require asked-next (nt:equal (cio:requests) ("1\t\t"))))
        ($_ (fs:remove (path:join (tio:mode-root) "control-status.json")))
        ($_ (nt:require throttled (nt:equal ((tio:heartbeat (cio:view)) (fs:exists (path:join (tio:mode-root) "control-status.json"))) (1 False))))
        ($_ (tio:set health-at (- (system:monotonic-ns) 5000000000)))
        ($_ (cio:script "reply-1" "71"))
        ($_ (nt:require due-again-after-five-seconds (nt:equal (tio:heartbeat (cio:view)) 1)))
        ($_ (nt:require unready-service-shows-no-inbox
              (nt:equal ((sf:get (cio:status) "channel_ready" none) (sf:get (cio:status) "operator_inbox" none)) (False JsonNull))))
        ($_ (cio:unscript "reply-1"))
        ($_ (cio:clear-requests)))
   (cio:passed UNIT_CONTROL_IO_RECORD_OK))

; The wait: the token is taken once; a change of the outbox after arming, or
; new work at the service, ends it at once; a refused token waits the bound
; out and is asked for again.
!(let* (($_ (nt:require token-taken (nt:equal (tio:token (tio:socket)) "0123456789abcdef0123456789abcdef.1")))
        ($_ (cio:script "delay-5" "1500"))
        (($first $first-ms) (cio:timed-wait))
        ($_ (nt:require token-asked-once-then-used
              (nt:equal (cio:requests) ("5\t\t" "5\t0123456789abcdef0123456789abcdef.1\t2000"))))
        ($_ (nt:require quiet-cycle-waits (and (== $first waited) (>= $first-ms 1400))))
        ($_ (cio:clear-requests))
        ($armed (tio:arm))
        ($started (system:monotonic-ns))
        ($_ (cio:intent "42.0.040" "arrived"))
        ($woken (tio:wait $armed))
        ($_ (nt:require outbox-change-after-arming-wakes (and (== $woken woken) (< (cio:ms-since $started) 1000))))
        ($_ (nt:require token-not-asked-again (nt:equal (size-atom (filter-atom (cio:requests) $line (str:starts-with $line "5\t\t"))) 0)))
        ; The cancelled wait holds the peer until its delay ends.
        ($_ (cio:unscript "delay-5"))
        ($_ (sleep 1.6))
        ($_ (nt:require arrival-submitted (nt:equal (tio:pump) 1)))
        ($_ (cio:script "reply-5" "67 fedcba9876543210fedcba9876543210.9"))
        (($pending $pending-ms) (cio:timed-wait))
        ($_ (nt:require new-work-wakes (and (== $pending woken) (< $pending-ms 1000))))
        ($_ (nt:require new-token-kept (nt:equal (tio:get token "") "fedcba9876543210fedcba9876543210.9")))
        ($_ (cio:script "reply-5" "70"))
        (($refused $refused-ms) (cio:timed-wait))
        ($_ (nt:require refused-token-waits-the-bound (and (== $refused waited) (>= $refused-ms 1900))))
        ($_ (nt:require refused-token-forgotten (nt:equal (tio:get token "") "")))
        ($_ (cio:unscript "reply-5"))
        ($_ (cio:clear-requests))
        ($_ (tio:arm))
        ($_ (nt:require new-token-asked (nt:equal (cio:requests) ("5\t\t"))))
        ; A service that holds its answer costs the arm its short timeout only.
        ($_ (tio:set token ""))
        ($_ (cio:script "delay-5" "1500"))
        ($hung (system:monotonic-ns))
        ($_ (tio:arm))
        ($_ (nt:require a-hung-service-does-not-hold-the-arm
              (and (< (cio:ms-since $hung) 1000) (== (tio:get token "") ""))))
        ($_ (cio:unscript "delay-5"))
        ($_ (sleep 1.6)))
   (cio:passed UNIT_CONTROL_IO_WAIT_OK))

; An unreachable service: nothing is lost and the cycle still waits.
!(let* (($server (car-atom (collapse (match &self (cio:server $s) $s))))
        ($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))
        ($_ (cio:intent "42.0.050" "later"))
        ($_ (nt:require unreachable-keeps (nt:equal ((tio:pump) (cio:exists "42.0.050.json")) (0 True))))
        ($_ (tio:set health-at (- (system:monotonic-ns) 5000000000)))
        ($_ (nt:require unreachable-is-not-ready
              (nt:equal ((tio:heartbeat (cio:view)) (sf:get (cio:status) "channel_ready" none)) (1 False))))
        ($_ (tio:set token ""))
        (($unreachable $unreachable-ms) (cio:timed-wait))
        ($_ (nt:require unreachable-still-waits (>= $unreachable-ms 1900))))
   (cio:passed UNIT_CONTROL_IO_UNREACHABLE_OK))

!(if (== (size-atom (collapse (match &self (cio:block-passed $block) $block))) 8)
   (println! UNIT_CONTROL_IO_OK)
   (println! (UNIT_CONTROL_IO_INCOMPLETE (collapse (match &self (cio:block-passed $block) $block)))))
