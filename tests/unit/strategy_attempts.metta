; Unit tests of src/strategy_attempts_cetta.metta and display:receipt, on a
; native ledger server and a display under the unit's scratch directory; no
; Python. The host's raise on an attempt the ledger did not record is replaced
; by a recorder of its arguments.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/strategy_attempts_cetta.metta)

!(match &self (= (strategy:not-recorded $ticket $position $why) $body)
   (remove-atom &self (= (strategy:not-recorded $ticket $position $why) $body)))
(= (strategy:not-recorded $ticket $position $why)
   (let $_ (add-atom &self (sa:refused $ticket $position)) (NotRecorded $ticket $position)))
(= (sa:refusals) (collapse (match &self (sa:refused $ticket $position) ($ticket $position))))

(= (sa:wait-socket $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (sa:wait-socket $server $socket (- $left 1))))))
(= (sa:attempts $socket $ticket)
   (map-atom (ledger-client:call $socket "attempts" ((ticket $ticket))) $row
     (let (attempt $t $position $command $result $order) $row ($position $command))))
; The display's records, parsed.
(= (sa:records)
   (let $directory (display:root)
     (if (not (fs:exists $directory)) ()
       (sa:parse-all $directory (fs:entries $directory)))))
(= (sa:parse-all $directory $names)
   (if (== $names ()) ()
     (let $name (car-atom $names)
       (if (str:ends-with $name ".jsonl")
         (sa:join (sa:lines (str:split "\n" (fs:read-text (path:join $directory $name)))) (sa:parse-all $directory (cdr-atom $names)))
         (sa:parse-all $directory (cdr-atom $names))))))
(= (sa:lines $lines)
   (if (== $lines ()) ()
     (if (== (car-atom $lines) "") (sa:lines (cdr-atom $lines)) (cons-atom (json-parse (car-atom $lines)) (sa:lines (cdr-atom $lines))))))
(= (sa:join $front $back) (if (== $front ()) $back (cons-atom (car-atom $front) (sa:join (cdr-atom $front) $back))))
(= (sa:last $list) (if (== (cdr-atom $list) ()) (car-atom $list) (sa:last (cdr-atom $list))))
(= (sa:fields (JsonObject $pairs) $names) (map-atom $names $name (display:last $pairs $name none)))
(= (sa:control $text) (fs:write (path:join (display:root) "control.json") $text))

; Each block records its end; the marker needs both.
!(bind! &sa-ended (new-state 0))
(= (sa:ended) (change-state! &sa-ended (+ (get-state &sa-ended) 1)))

!(let* (($home (nt:required-env "HOME")) ($root (system:cwd))
        ($socket (path:join $home "l.sock")) ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($module (path:join $root "src/input_ledger_server.metta"))
        ($server-env (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
        ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta" $module "db-server:configuration")
                             ((cwd $root) (env $server-env) (max-bytes 65536))))
        ($_ (nt:require ledger-ready (sa:wait-socket $server $socket 500)))
        ; No open batch: nothing is recorded or shown.
        ($_ (nt:require no-batch-no-attempt (nt:equal (strategy:reserve 5 (quote (shell "ls"))) 0)))
        ($_ (nt:require no-batch-no-identity (nt:equal (strategy:attempt 5) none)))
        ($_ (strategy:open-batch 5 ""))
        ($_ (nt:require no-ticket-no-batch (nt:equal (strategy:batch) none)))
        ($_ (nt:require nothing-shown (nt:equal (sa:records) ())))
        ; A batch under a ticket: each command is an attempt at its position.
        ($_ (strategy:open-batch 5 "unit-ticket"))
        ($_ (nt:require other-turn-not-recorded (nt:equal (strategy:reserve 6 (quote (shell "ls"))) 0)))
        ($_ (nt:require opened-before-any-attempt (nt:equal (strategy:attempt 5) none)))
        ($_ (nt:require first-attempt (nt:equal (strategy:reserve 5 (quote (shell "ls"))) 1)))
        ($_ (nt:require first-identity (nt:equal (strategy:attempt 5) (StrategyAttemptV1 "unit-ticket:0" 0))))
        ($_ (nt:require second-attempt (nt:equal (strategy:reserve 5 (quote (send "héllo \"x\"" 3 True))) 1)))
        ($_ (nt:require second-identity (nt:equal (strategy:attempt 5) (StrategyAttemptV1 "unit-ticket:1" 1))))
        ($_ (nt:require attempts-recorded-as-the-host-encoded-them
              (nt:equal (sa:attempts $socket "unit-ticket")
                        ((0 "[\"shell\",\"ls\"]") (1 "[\"send\",\"héllo \\\"x\\\"\",3,\"true\"]")))))
        ; The dispatch is shown under the attempt, with the command as the host's str() gave it.
        ($records (sa:records))
        ($_ (nt:require two-dispatches (nt:equal (size-atom $records) 2)))
        ($_ (nt:require dispatch-fields
              (nt:equal (sa:fields (car-atom $records) ("kind" "item" "command" "text" "status" "source" "epoch"))
                        ((JsonString "tool.dispatching") (JsonString "unit-ticket:0") (JsonString "['shell', 'ls']")
                         (JsonString "") (JsonString "dispatching") (JsonString "executor") (JsonString "initial")))))
        ($_ (nt:require second-dispatch-command
              (nt:equal (sa:fields (sa:last $records) ("item" "command"))
                        ((JsonString "unit-ticket:1") (JsonString "['send', 'héllo \"x\"', 3, 'true']")))))
        ($_ (nt:require dispatch-holds-the-epoch (nt:equal (display:item-epoch "unit-ticket:1") "initial")))
        ; A batch opened again under the same ticket starts at 0: the ledger
        ; refuses the attempt, the host is asked to fail the batch, nothing moves.
        ($_ (strategy:open-batch 5 "unit-ticket"))
        ($_ (nt:require repeat-refused (nt:equal (strategy:reserve 5 (quote (shell "again"))) (NotRecorded "unit-ticket" 0))))
        ($_ (nt:require repeat-reported (nt:equal (sa:refusals) (("unit-ticket" 0)))))
        ($_ (nt:require repeat-keeps-the-position (nt:equal (strategy:batch) (5 "unit-ticket" 0))))
        ($_ (nt:require repeat-shown-nothing (nt:equal (size-atom (sa:records)) 2)))
        ($_ (nt:require repeat-recorded-nothing (nt:equal (size-atom (sa:attempts $socket "unit-ticket")) 2)))
        ; A new turn's batch replaces the old; closing it leaves none.
        ($_ (strategy:open-batch 7 "other-ticket"))
        ($_ (nt:require replaced (nt:equal (strategy:reserve 5 (quote (shell "ls"))) 0)))
        ($_ (strategy:close-batch))
        ($_ (nt:require closed (nt:equal (strategy:batch) none)))
        ; A ledger that does not answer: the host is asked to fail the batch.
        ($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))
        ($_ (strategy:open-batch 8 "down-ticket"))
        ($_ (nt:require dead-ledger-refused
              (nt:equal (strategy:reserve 8 (quote (shell "ls"))) (NotRecorded "down-ticket" 0))))
        ($_ (nt:require dead-ledger-shown-nothing (nt:equal (size-atom (sa:records)) 2))))
   (sa:ended))

; A receipt ends the dispatched command in the epoch its dispatch kept; in
; another epoch it is dropped, and the epoch is released either way.
!(let* (($_ (nt:require receipt-written
              (nt:equal (display:receipt "unit-ticket:0" "['shell', 'ls']" "listing" "returned") 1)))
        ($_ (nt:require receipt-fields
              (nt:equal (sa:fields (sa:last (sa:records)) ("kind" "item" "command" "text" "status" "source" "epoch"))
                        ((JsonString "tool.finished") (JsonString "unit-ticket:0") (JsonString "['shell', 'ls']")
                         (JsonString "listing") (JsonString "returned") (JsonString "executor") (JsonString "initial")))))
        ($_ (nt:require receipt-releases-the-epoch (nt:equal (display:item-epoch "unit-ticket:0") none)))
        ($_ (sa:control "{\"enabled\": true, \"epoch\": \"e2\"}"))
        ($before (size-atom (sa:records)))
        ($_ (nt:require old-epoch-receipt-dropped
              (nt:equal (display:receipt "unit-ticket:1" "['send']" "sent" "returned") 0)))
        ($_ (nt:require old-epoch-released (nt:equal (display:item-epoch "unit-ticket:1") none)))
        ($_ (nt:require nothing-written-for-it (nt:equal (size-atom (sa:records)) $before)))
        ; A receipt without a dispatch (no observed strategy) is in the console's epoch.
        ($_ (nt:require unheld-receipt-written
              (nt:equal (display:receipt "f00d" "['help']" "help text" "returned") 1)))
        ($_ (nt:require unheld-receipt-epoch (nt:equal (sa:fields (sa:last (sa:records)) ("epoch")) ((JsonString "e2"))))))
   (sa:ended))

!(if (== (get-state &sa-ended) 2) (println! UNIT_STRATEGY_ATTEMPTS_OK)
   (println! (FAIL blocks-ended (get-state &sa-ended))))
