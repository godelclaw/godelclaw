; The native working-state machine across real processes: a missing file
; is one boot with nothing pending; the working set and workspace stores
; compose in either import order; a boot interrupting a claimed rest timer
; reports an unknown outcome and drops the timer; a restart restores the
; banked budget and the scheduled continuation without firing it early, and
; a crash after the claim never replays the command. Against the native
; ledger: an unavailable ledger refuses a save and never loads the file
; projection; a checkpoint commits the scope staged at turn begin, not a
; later one, and stays committed when its operator projection cannot be
; replaced; competing checkpoints each commit once. (A lost checkpoint reply
; replayed exactly once is checked on the live loop path by
; scripts/test_operator_stop_native.metta.)
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_io.metta)
!(import! &self (library json))
!(import! &self (library list))

(= (ws:root) (nt:required-env "TEST_REPOSITORY"))
(= (ws:home) (nt:required-env "HOME"))
(= (ws:engine) (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
(= (ws:case-dir $name)
   (let $dir (path:join (ws:home) $name) (let $_ (fs:make-directories $dir 448) $dir)))
(= (ws:working $dir) (path:join $dir "working.json"))
; The guest environment; a setting in $extra replaces the default.
(= (ws:env $dir $extra)
   (let $keys (map-atom $extra $pair (car-atom $pair))
     (list:append $extra
       (filter-atom (list:append (nt:environment (ws:home))
                      (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta")
                       ("METTACLAW_OBSERVED_STRATEGY" "0") ("METTACLAW_WORKING_SET_PATH" (ws:working $dir))
                       ("TMPDIR" $dir)))
         $pair (not (ws:member? (car-atom $pair) $keys))))))
(= (ws:import $relative) (str:concat "!(import! &self " (str:concat (repr (path:join (ws:root) $relative)) ")")))
; A guest program: the working state, then $forms, one per line.
(= (ws:program $dir $name $imports $forms)
   (let $source (path:join $dir (str:concat $name ".metta"))
     (let $_ (fs:write $source (str:concat (str:join "\n" (list:append (map-atom $imports $i (ws:import $i)) $forms)) "\n"))
       $source)))
(= (ws:standard) ("src/engine_modes.metta" "src/working_state.metta"))
(= (ws:argv $source) ((ws:engine) "--lang" "petta" "--import-mode" "ancestor-walk" $source))
(= (ws:run $dir $name $imports $forms $extra)
   (proc:run (ws:argv (ws:program $dir $name $imports $forms)) $dir (ws:env $dir $extra) 120000 4194304))
(= (ws:out $run) (case $run (((proc:exited $code $out $err) $out) ($other (repr $other)))))
(= (ws:ok $run $marker)
   (case $run
     (((proc:exited 0 $out $err) (ws:saw $out $err $marker))
      ((proc:result (proc:exited 0) proc:normal $out2 $err2 $capture) (ws:saw $out2 $err2 $marker))
      ($other (let $_ (println! (GUEST_FAILED $marker $other)) False)))))
(= (ws:saw $out $err $marker)
   (if (nt:contains $out $marker) True (let $_ (println! (GUEST_OUTPUT $marker $out $err)) False)))
(= (ws:kind $path) (car-atom (cdr-atom (fs:metadata-v2 $path no-follow))))
(= (ws:json $path) (json:parse (fs:read-text $path)))
; Wait through the child's handle, which keeps it referenced: an
; unreferenced handle is reclaimed, and an owned child ends with it.
(= (ws:wait-claim $owner $path $left)
   (if (fs:exists $path) True
     (if (<= $left 0) (let $_ (println! (CLAIM_NOT_SEEN (proc:status $owner))) False)
       (let $_ (proc:wait $owner 50) (ws:wait-claim $owner $path (- $left 1))))))
(= (ws:get $object $key) (json:get $object $key))
(= (ws:has? $object $key)
   (let (JsonObject $pairs) $object
     (not (== (collapse (let (JsonPair $k $v) (superpose $pairs) (if (== $k $key) $k (empty)))) ()))))
(= (ws:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (ws:member? $x (cdr-atom $xs)))))
(= (ws:record $path $value) (str:concat "(fs:write " (str:concat (repr $path) (str:concat " " (str:concat $value ")")))))

!(let* (($a (ws:case-dir "missing"))
        ($_ (nt:require missing-file-is-one-boot-with-nothing-pending
              (ws:ok (ws:run $a "missing" (ws:standard)
                       ("!(println! (WS_MISSING (collapse (working-state:read-file)) (collapse (working:rest-pending)) (collapse (working:boot))))")
                       ())
                     "(WS_MISSING ((JsonObject ())) (\"no-continuation\") (1))")))

        ($b (ws:case-dir "compose"))
        ($workspace (path:join $b "workspaces.metta"))
        ($compose (map-atom (("src/working_state.metta" "src/workspaces_cetta.metta")
                             ("src/workspaces_cetta.metta" "src/working_state.metta")) $order
                    (let* (($_ (fs:remove (ws:working $b))) ($_ (fs:remove $workspace))
                           ($run (ws:run $b "compose" (cons-atom "src/engine_modes.metta" $order)
                                   ("!(println! (WS_PATHS (collapse (ws:path)) (collapse (working-state:path))))"
                                    "!(println! (WS_SAVE (collapse (working:save 0 60 \"feedback\" 90 1 17 0))))"
                                    "!(let $before (fs:read-text (working-state:path)) (let $_ (workspace \"focus\" (\"all\") () \"\") (println! (WS_UNTOUCHED (== $before (fs:read-text (working-state:path)))))))"
                                    "!(println! (WS_SNAPSHOTS (size-atom (collapse (workspace)))))")
                                   (("METTACLAW_WORKSPACES_PATH" $workspace)))))
                      (and (ws:ok $run (str:concat "(WS_PATHS (" (str:concat (repr $workspace) (str:concat ") (" (str:concat (repr (ws:working $b)) "))")))))
                      (and (ws:ok $run "(WS_SAVE (1))")
                      (and (ws:ok $run "(WS_UNTOUCHED true)")
                      (and (ws:ok $run "(WS_SNAPSHOTS 1)")
                      (and (== (ws:get (ws:json (ws:working $b)) "banked_loops") 17)
                           (str:starts-with (fs:read-text $workspace) "(WorkspaceSnapshot 1 ")))))))))
        ($_ (nt:require working-set-and-workspaces-compose-in-either-import-order (== $compose (True True))))

        ($c (ws:case-dir "interrupted"))
        ($persisted-out (path:join $c "persisted.json"))
        ($cache-out (path:join $c "cache.json"))
        ($_ (ws:run $c "interrupted" (ws:standard)
              ((str:concat "!(let* (($timer (working-state:transition (JsonObject ()) (WorkingRestScheduleV1 1 (send \"hello\")) 100.0)) ($running (working-state:transition $timer WorkingRestClaimV1 100.0)) ($boot (working-state:boot-transition $running 101.0 \"2026-10-25 12:00:00\"))) (let (WorkingBootV1 $persisted $cache $write) $boot (let* (($p "
                 (str:concat (ws:record $persisted-out "(working-state:encoded $persisted)")
                   (str:concat ") ($q " (str:concat (ws:record $cache-out "(working-state:encoded $cache)") ")) (println! WS_INTERRUPTED_DONE))))"))))) ()))
        ($_ (nt:require interrupted-boot-reports-unknown-and-drops-the-timer
              (let* (($persisted (ws:json $persisted-out)) ($cache (ws:json $cache-out)) ($last (ws:get $cache "lastresults")))
                (and (not (ws:has? $persisted "rest_timer"))
                     (and (nt:contains $last "outcome unknown; not replayed") (nt:contains $last "hello"))))))

        ($d (ws:case-dir "restart"))
        ($first (ws:run $d "first" (ws:standard)
                  ("!(println! (WS_FIRST (working:save 0 60 \"earlier evidence\" 90 1 17 0)))"
                   "!(working:rest-schedule 120 (shell \"printf scheduled\"))"
                   "!(working:rest-hold 120)"
                   "!(working:intent-save \"restart rest\")")
                  ()))
        ($stored (ws:json (ws:working $d)))
        ($_ (nt:require a-scheduled-rest-is-saved-waiting
              (and (ws:ok $first "(WS_FIRST 1)")
                   (and (== (ws:get $stored "banked_loops") 17)
                        (== (ws:get (ws:get $stored "rest_timer") "status") "waiting")))))
        ($boot-out (path:join $d "boot.json"))
        ($pending-out (path:join $d "pending.json"))
        ($due-out (path:join $d "due"))
        ($claimed (path:join $d "claimed"))
        ($second (proc:spawn (ws:argv (ws:program $d "second" (ws:standard)
                   ("!(working:boot)"
                    (str:concat "!" (ws:record $boot-out "(working-state:encoded (get-state &working-state-boot))"))
                    (str:concat "!(let $pending (working:rest-pending) (case $pending (((\"continuation-value\" $command) "
                      (str:concat (ws:record $pending-out "(working-state:encoded (JsonArray $command))") ") ($other (println! (UNEXPECTED_PENDING $other))))))"))
                    (str:concat "!" (ws:record $due-out "(repr (working:rest-due 1))"))
                    "!(working:rest-claim)"
                    (str:concat "!" (ws:record $claimed "\"claimed\""))
                    "!(sleep 120)")))
                  ((cwd $d) (env (ws:env $d ())) (max-bytes 65536) (lifetime owner))))
        ($_ (nt:require the-restarted-process-claims-the-continuation (ws:wait-claim $second $claimed 600)))
        ($_ (proc:cancel $second))
        ($_ (proc:wait $second 10000))
        ($restored (ws:json $boot-out))
        ($_ (nt:require a-restart-restores-the-budget-and-the-continuation
              (and (== (ws:get $restored "banked_loops") 17)
              (and (nt:contains (ws:get $restored "lastresults") "WOKE_FROM_REST: restart rest")
              (and (<= (ws:get $restored "sleepInterval") 120)
              (and (== (ws:json $pending-out) (JsonArray ("shell" "printf scheduled")))
                   (== (fs:read-text $due-out) "0")))))))
        ($final-boot (path:join $d "final-boot.json"))
        ($final-pending (path:join $d "final-pending"))
        ($_ (ws:run $d "final" (ws:standard)
              ("!(working:boot)"
               (str:concat "!" (ws:record $final-boot "(working-state:encoded (get-state &working-state-boot))"))
               (str:concat "!" (ws:record $final-pending "(repr (working:rest-pending))")))
              ()))
        ($final (ws:json $final-boot))
        ($_ (nt:require a-crash-after-the-claim-never-replays-the-command
              (and (nt:contains (ws:get $final "lastresults") "SCHEDULED_COMMAND_INTERRUPTED: outcome unknown; not replayed")
              (and (not (nt:contains (ws:get $final "lastresults") "WOKE_FROM_REST"))
              (and (== (fs:read-text $final-pending) "\"no-continuation\"")
                   (not (ws:has? (ws:json (ws:working $d)) "rest_timer"))))))))
   (println! UNIT_WORKING_STATE_OK))

; The ledger's handle lives in a state for the whole run: an unreferenced
; handle is reclaimed, and the owned server ends with it.
!(bind! &ws-ledger (new-state none))
(= (ws:wait-socket $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (ws:wait-socket $server $socket (- $left 1))))))
(= (ws:committed? $id) (state:ledger "committed" (($id))))
(= (ws:checkpoint)
   (case (state:ledger "working-set" ())
     ((none none) ($text (if (== (get-type $text) String) (json:parse $text) (Unreadable $text))))))
(= (ws:processed $id)
   (case (state:ledger "request-record" ($id 0))
     (((record $i $k $c $st $flag $n $text $length $dg) $flag) ($other (NoRecord $other)))))
(= (ws:ledger-env $dir $socket)
   (("METTACLAW_OBSERVED_STRATEGY" "1") ("METTACLAW_INPUT_LEDGER_SOCKET" $socket)))
(= (ws:scope $ids $request)
   ((str:concat "!(turn:put \"inputs\" " (str:concat (repr $ids) ")"))
    (str:concat "!(turn:put \"message-focus\" " (str:concat (repr $request) ")"))))

!(let* (($home (ws:home))
        ($socket (nt:required-env "METTACLAW_INPUT_LEDGER_SOCKET"))
        ($ledger (proc:spawn ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker")
                              "--value-bytes" "268435456" "--serve" "--lang" "petta"
                              (path:join (ws:root) "src/input_ledger_server.metta") "db-server:configuration")
                   ((cwd (ws:root))
                    (env (cons-atom ("CETTA_DB_SOURCE" (path:join $home "store"))
                           (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
                    (max-bytes 65536) (lifetime owner))))
        ($_ (change-state! &ws-ledger $ledger))
        ($_ (nt:require native-ledger-ready (ws:wait-socket $ledger $socket 500)))
        ($_ (state:ledger "owner" ("ws-owner" (state:request-id))))
        ($_ (state:ledger "request-accept" ("input:one" "message" "" "message" "s1" "d1" (state:request-id))))

        ($g (ws:case-dir "unavailable"))
        ($_ (fs:write (ws:working $g) "{\"loops\":999,\"lastresults\":\"stale projection\"}"))
        ($unavailable (ws:run $g "unavailable" (ws:standard)
                        ("!(println! (WS_READ_FAILED (working-state:failed? (working-state:read))))"
                         "!(println! (WS_SAVED (working:save 10 3 \"not committed\" 15 0 4 0)))")
                        (ws:ledger-env $g (path:join $g "missing.sock"))))
        ($_ (nt:require an-unavailable-ledger-never-loads-the-file-projection
              (and (ws:ok $unavailable "(WS_READ_FAILED true)")
              (and (ws:ok $unavailable "(WS_SAVED 0)")
              (and (== (ws:get (ws:json (ws:working $g)) "loops") 999)
                   (== (ws:checkpoint) none))))))

        ($e (ws:case-dir "scope"))
        ($scoped (ws:run $e "scope" (ws:standard)
                   (list:append (ws:scope ("runtime:101" "102") "input:one")
                     (cons-atom "!(println! (WS_STAGED (working:turn-commit-stage 1 1 1)))"
                       (list:append (ws:scope ("wrong") "wrong-request")
                         ("!(println! (WS_SAVED (working:save 7 3 \"committed\" 12 0 4 0)))"))))
                   (ws:ledger-env $e $socket)))
        ($_ (nt:require a-checkpoint-commits-the-scope-staged-at-turn-begin
              (and (ws:ok $scoped "(WS_STAGED 1)")
              (and (ws:ok $scoped "(WS_SAVED 1)")
              (and (== (map-atom ("runtime:101" "102" "wrong") $id (ws:committed? $id)) (True True False))
              (and (== (ws:processed "input:one") 1)
                   (== (ws:get (ws:checkpoint) "lastresults") "committed")))))))
        ($projection (path:join $e "projection-directory"))
        ($_ (fs:make-directories $projection 448))
        ($faulted (proc:run (ws:argv (ws:program $e "projection" (ws:standard)
                              ("!(working:turn-commit-reset)"
                               "!(println! (WS_SAVED (working:save 8 3 \"after projection fault\" 13 0 4 0)))")))
                            $e (ws:env $e (cons-atom ("METTACLAW_WORKING_SET_PATH" $projection) (ws:ledger-env $e $socket)))
                            120000 4194304))
        ($_ (nt:require a-committed-checkpoint-survives-a-failed-projection
              (and (ws:ok $faulted "(WS_SAVED 1)")
              (and (== (ws:get (ws:checkpoint) "loops") 8)
                   (== (ws:kind $projection) directory)))))

        ($h (ws:case-dir "race"))
        ($racers (map-atom (("a" "race:a") ("b" "race:b")) $racer
                   (let ($name $identity) $racer
                     (proc:spawn (ws:argv (ws:program $h (str:concat "race-" $name) (ws:standard)
                                   (list:append (ws:scope ($identity) "input:one")
                                     ("!(println! (WS_STAGED (working:turn-commit-stage 1 1 1)))"
                                      (str:concat "!(println! (WS_SAVED (working:save 10 3 " (str:concat (repr $name) " 15 0 4 0)))"))))))
                                 ((cwd $h) (env (ws:env $h (ws:ledger-env $h $socket))) (max-bytes 65536) (lifetime owner))))))
        ($finished (map-atom $racers $racer (proc:wait $racer 120000)))
        ($_ (nt:require competing-checkpoints-each-commit-once
              (and (== (map-atom $finished $f (ws:ok $f "(WS_SAVED 1)")) (True True))
              (and (== (map-atom ("race:a" "race:b") $id (ws:committed? $id)) (True True))
              (and (ws:member? (ws:get (ws:checkpoint) "lastresults") ("a" "b"))
                   (== (ws:processed "input:one") 1))))))
        ($_ (proc:cancel (get-state &ws-ledger))))
   (println! UNIT_WORKING_STATE_LEDGER_CASES_OK))

; The wait mode is kept as data, whatever the budget: a save that waits for
; input is read back as waiting at the next boot, with its banked loops; a
; retained intent keeps every word, its scope qualifier included.
!(let* (($d (ws:case-dir "waiting"))
        ($intent (str:concat (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10) $i "context context context context context context context context context context "))
                             " applies only to this task and pane; never a global restriction"))
        ($first (ws:run $d "first" (ws:standard)
                  ("!(println! (WS_WAIT (working:save 0 60 \"waiting\" 100 0 43 1)))"
                   (str:join "" ("!(println! (WS_INTENT (working:intent-save " (repr $intent) ")))")))
                  ()))
        ($stored (ws:json (ws:working $d)))
        ($_ (nt:require a-waiting-save-is-kept-as-data
              (and (ws:ok $first "(WS_WAIT 1)")
              (and (== (ws:get $stored "rest_await_input") True)
                   (== (ws:get $stored "intent") $intent)))))
        ($second (ws:run $d "second" (ws:standard)
                   ("!(working:boot)"
                    "!(println! (WS_BOOT (working:boot-int \"rest_await_input\" 0) (working:boot-int \"banked_loops\" 0) (working:boot-int \"loops\" -1)))")
                   ())))
   (nt:require the-wait-mode-survives-a-boot (ws:ok $second "(WS_BOOT 1 43 0)")))
!(println! UNIT_WORKING_STATE_LEDGER_OK)
