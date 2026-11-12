; Coding-session authority on the native implementation, on the core engine:
; the traces and contracts of the Lean models CodingSessionAuthority,
; CodingSessionLaunch, CodingSessionRepin and CodingStopSettlement, and the
; durability faults. Child processes supply what one process cannot: a stub
; service manager on PATH, an fsync fault shim (tests/fixtures/fsync_fault_shim.c)
; and concurrent observers. Run from the checkout root:
;   NATIVE_TEST_CORE=... NATIVE_TEST_ARTIFACTS=... CETTA_BIN=$NATIVE_TEST_CORE/cetta \
;     $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk --quiet scripts/test_coding_authority_cetta.metta
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/coding_io_cetta.metta)

(= (cat:overlay $base $extra)
   (if (== $base ()) $extra
     (let* ((($key $value) (car-atom $base)) ($rest (cat:overlay (cdr-atom $base) $extra)))
       (if (cat:has-key $key $extra) $rest (cons-atom ($key $value) $rest)))))
(= (cat:has-key $key $pairs)
   (if (== $pairs ()) False (let ($name $value) (car-atom $pairs) (if (== $name $key) True (cat:has-key $key (cdr-atom $pairs))))))
(= (cat:append $left $right)
   (if (== $left ()) $right (cons-atom (car-atom $left) (cat:append (cdr-atom $left) $right))))
(= (cat:field $dir $file $key) (coding:field (sf:object (path:join $dir $file)) $key))
(= (cat:run $dir) (coding:text (cat:field $dir "runtime.json" "run")))
; The frozen runtime a launch of this session runs from.
(= (cat:root $dir) (coding:text (cat:field $dir "runtime.json" "root")))
(= (cat:dead) "4194301:1")
(= (cat:session $root $name $state)
   (let* (($dir (path:join (path:join $root "memory/coding") $name)) ($_ (fs:make-directories $dir 504))
          ($_ (fs:write (path:join $dir "task.json") (format-args "{\"task\":\"t\",\"workdir\":\"{}\"}" ($root))))
          ($_ (fs:write (path:join $dir "messages.jsonl") "{\"role\":\"user\",\"content\":\"t\"}\n"))
          ($_ (fs:write (path:join $dir "state.json") $state)))
     $dir))
(= (cat:running $owner $run)
   (format-args "{\"status\":\"running\",\"steps\":5,\"progress\":\"step 5\",\"worker_owner\":\"{}\",\"run\":\"{}\",\"inbox\":0,\"updated\":5.0}" ($owner $run)))
(= (cat:started $dir)
   (fs:write (path:join $dir "runtime.json") (json-stringify (sf:put (sf:object (path:join $dir "runtime.json")) "phase" (JsonString "started")))))
(= (cat:pin-elsewhere $dir)
   (fs:write (path:join $dir "runtime.json")
     (json-stringify (sf:put (sf:put (sf:object (path:join $dir "runtime.json")) "digest" (JsonString "old-digest")) "phase" (JsonString "ended")))))
(= (cat:files-text $dir $names)
   (if (== $names ()) ""
     (let $name (car-atom $names)
       (str:concat (if (fs:is-file (path:join $dir $name)) (str:concat $name (fs:read-text (path:join $dir $name))) "")
                   (cat:files-text $dir (cdr-atom $names))))))

;; A runtime whose checker obeys the copy's check-mode file: ready; fail;
;; messages (changes the live transcript during the check); inbox (appends
;; live guidance, which is not part of the checkpoint); manifest (re-reserves
;; the live run); ready-substring (a failure naming a ready verdict).
(= (cat:program $root)
   (let* (($tree (path:join $root "program")) ($_ (fs:make-directories (path:join $tree "config") 504))
          ($_ (fs:write (path:join $tree "coding_main.metta") "; fixture\n"))
          ($_ (fs:write (path:join $tree "coding_check.metta") (str:join "" (
                "!(import! &self (library fs))\n!(import! &self (library system))\n!(import! &self (library path))\n"
                "(= (live $dir) (fs:read-text (path:join $dir \"live-dir\")))\n"
                "!(let* (($dir (system:arg (- (system:argc) 1)))\n"
                "        ($mode (if (fs:is-file (path:join $dir \"check-mode\")) (fs:read-text (path:join $dir \"check-mode\")) \"ready\")))\n"
                "   (if (== $mode \"fail\") (CheckpointFailed \"no checkpoint here\")\n"
                "    (if (== $mode \"ready-substring\") (CheckpointFailed \"text with (CheckpointReady 3) inside\")\n"
                "     (if (== $mode \"messages\") (let $_ (fs:append-text (path:join (live $dir) \"messages.jsonl\") \"{}\\n\") (CheckpointReady 1))\n"
                "      (if (== $mode \"inbox\") (let $_ (fs:append-text (path:join (live $dir) \"inbox.jsonl\") \"\\\"guidance\\\"\\n\") (CheckpointReady 1))\n"
                "       (if (== $mode \"manifest\") (let $_ (fs:write (path:join (live $dir) \"runtime.json\") \"{\\\"run\\\":\\\"other\\\",\\\"phase\\\":\\\"reserved\\\"}\") (CheckpointReady 1))\n"
                "        (CheckpointReady 2)))))))\n"))))
          ($_ (fs:write (path:join $tree "config/runtime-files.json")
                "[\"coding_main.metta\", \"coding_check.metta\", \"config/runtime-files.json\"]")))
     $tree))
(= (cat:check-mode $dir $mode)
   (let* (($_ (fs:write (path:join $dir "live-dir") $dir))) (fs:write (path:join $dir "check-mode") $mode)))

;; A child process running forms against coding_io_cetta.metta.
(= (cat:child $root $name $env $forms)
   (let* (($script (path:join $root (str:concat $name ".metta")))
          ($_ (fs:write $script (str:join "" (
                "!(import! &self " (repr (nt:join (system:cwd) "tests/native_test_support.metta")) ")\n"
                "!(import! &self " (repr (nt:join (system:cwd) "src/coding_io_cetta.metta")) ")\n" $forms))))
          ($base (cat:append (nt:environment $root) (("CETTA_BIN" (nt:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))))))
     (nt:exit-ok (proc:run ((nt:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $script)
                           $root (cat:overlay $base $env) 120000 1048576))))
(= (cat:shim $root)
   (let $shim (path:join $root "fsync-fault.so")
     (let $_ (if (fs:exists $shim) ()
               (nt:exit-ok (proc:run ("cc" "-shared" "-fPIC" "-O2" "-Wall" "-Wextra" "-Werror"
                                      (nt:join (system:cwd) "tests/fixtures/fsync_fault_shim.c") "-ldl" "-o" $shim)
                                     $root (nt:environment $root) 60000 65536)))
       $shim)))
(= (cat:fault $root $dir $mode)
   (("LD_PRELOAD" (cat:shim $root)) ("FSYNC_FAULT_TARGET" $dir) ("FSYNC_FAULT_MODE" $mode)))

; Trace staleReaper: a reaper that observed run 1 dead publishes after a resume
; started run 2; the publication names the observed owner, so it is refused.
(= (cat:stale-reaper $root $program)
   (let* (($d (cat:session $root "work-stale-reaper" (cat:running (cat:dead) "r1")))
          ($_ (fs:write (path:join $d "runtime.json") "{\"root\":\"/nonexistent\",\"run\":\"r1\",\"phase\":\"started\"}"))
          ($_ (fs:write (path:join $d "runtime.json") (json-stringify (sf:put (sf:object (path:join $d "runtime.json")) "root" (JsonString $program)))))
          ($resumed (cio:freeze-runtime $program $d (cio:note-pinned)))
          ($_ (nt:require resume-settles-dead-run-first
                (nt:equal (cat:field $d "state.json" "status") (JsonString "interrupted"))))
          ($_ (nt:require resumed-run-claimed (nt:equal (cio:consume-run $d (cat:run $d) (process:owner) (cat:root $d)) 1)))
          ($_ (nt:require paused-reaper-refused (nt:equal (cio:settle-ending $d (cat:dead)) "")))
          ($_ (nt:require live-run-kept (nt:equal (cat:field $d "state.json" "worker_owner") (JsonString (process:owner))))))
     True))

; Traces staleLaunch and supersededFirst: only the newest reservation starts,
; in either order of registration.
(= (cat:stale-launch $root $program)
   (let* (($a (cat:session $root "work-launch-old-first" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $a NoNote)) ($t1 (cat:run $a))
          ($_ (cio:freeze-runtime $program $a NoNote)) ($t2 (cat:run $a))
          ($_ (nt:require superseding-token-differs (not (nt:equal $t1 $t2))))
          ($_ (nt:require stale-launch-exits (nt:equal (cio:consume-run $a $t1 "1:a" (cat:root $a)) 0)))
          ($_ (nt:require newest-launch-starts (nt:equal (cio:consume-run $a $t2 "1:b" (cat:root $a)) 1)))
          ($b (cat:session $root "work-launch-new-first" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $b NoNote)) ($u1 (cat:run $b))
          ($_ (cio:freeze-runtime $program $b NoNote)) ($u2 (cat:run $b))
          ($_ (nt:require newest-first-starts (nt:equal (cio:consume-run $b $u2 "1:b" (cat:root $b)) 1)))
          ($_ (nt:require superseded-after-exits (nt:equal (cio:consume-run $b $u1 "1:a" (cat:root $b)) 0))))
     True))

; fixed_tokens_consumed_once, while the run is started and after it ended.
(= (cat:consumed-once $root $program)
   (let* (($d (cat:session $root "work-once" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $d NoNote)) ($t (cat:run $d))
          ($_ (nt:require first-start (nt:equal (cio:consume-run $d $t "1:a" (cat:root $d)) 1)))
          ($_ (nt:require second-start-while-started (nt:equal (cio:consume-run $d $t "1:b" (cat:root $d)) 0)))
          ($_ (nt:require run-ends (nt:equal (cio:worker-state $d $t "1:a" "done" 3 "done" 0 "" "report") 1)))
          ($_ (nt:require second-start-after-ending (nt:equal (cio:consume-run $d $t "1:b" (cat:root $d)) 0))))
     True))

; A stop revokes an unconsumed reservation and records no ending.
(= (cat:stop-revokes $root $program)
   (let* (($d (cat:session $root "work-stop-reserved" "{\"status\":\"done\",\"steps\":3}"))
          ($_ (cio:freeze-runtime $program $d NoNote)) ($t (cat:run $d))
          ($_ (cio:stop-session $d))
          ($_ (nt:require reservation-ended (nt:equal (cat:field $d "runtime.json" "phase") (JsonString "ended"))))
          ($_ (nt:require no-ending-recorded (nt:equal (cat:field $d "state.json" "status") (JsonString "done"))))
          ($_ (nt:require revoked-launch-exits (nt:equal (cio:consume-run $d $t "1:a" (cat:root $d)) 0))))
     True))

; Resume and re-pin spare a live worker and an active scope.
(= (cat:live-worker $root $program)
   (let* (($d (cat:session $root "work-live" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $d NoNote))
          ($_ (fs:write (path:join $d "state.json") (cat:running (process:owner) (cat:run $d))))
          ($_ (fs:write (path:join $d "stop") "")) ($_ (fs:write (path:join $d "report.txt") "kept report"))
          ($_ (fs:write (path:join $d "lifecycle.json") "{\"hold\":{\"why\":\"keep hold\"}}"))
          ($_ (fs:write (path:join $d "inbox.jsonl") "\"kept guidance\"\n"))
          ($kept (cat:files-text $d ("stop" "report.txt" "lifecycle.json" "inbox.jsonl")))
          ($before (fs:read-text (path:join $d "runtime.json")))
          ($_ (nt:require resume-spares-live-worker
                (case (cio:freeze-runtime $program $d NoNote) (((CodingLaunch running $r $b $t $x) True) ($o False)))))
          ($_ (nt:require repin-spares-live-worker
                (case (cio:repin $program $d) (((CodingLaunch running $r $b $t $x) True) ($o False)))))
          ($_ (nt:require live-manifest-unchanged (nt:equal (fs:read-text (path:join $d "runtime.json")) $before)))
          ($_ (nt:require refused-resume-leaves-files
                (nt:equal (cat:files-text $d ("stop" "report.txt" "lifecycle.json" "inbox.jsonl")) $kept))))
     True))
(= (cat:scope-stub $root $exit)
   (let* (($bin (path:join $root (str:concat "stub-bin-" (repr $exit)))) ($_ (fs:make-directories $bin 504))
          ($_ (fs:write (path:join $bin "systemctl") (format-args "#!/bin/sh\nexit {}\n" ($exit))))
          ($_ (fs:set-mode (path:join $bin "systemctl") 448)))
     (str:concat $bin (str:concat ":" (system:getenv-or-default "PATH" "/usr/bin:/bin")))))
(= (cat:scope-guard $root $program)
   (let* (($d (cat:session $root "work-scope" "{\"status\":\"stopped\",\"worker_owner\":\"880:1\"}"))
          ($_ (cio:freeze-runtime $program $d NoNote)) ($t0 (cat:run $d))
          ($forms (format-args "!(println! (cio:freeze-runtime {} {} NoNote))\n!(println! (cio:repin {} {}))\n"
                    ((repr $program) (repr $d) (repr $program) (repr $d))))
          ($active (cat:child $root "scope-active" (("PATH" (cat:scope-stub $root 0))) $forms))
          ($_ (nt:require active-scope-keeps-reservation (nt:equal (cat:run $d) $t0)))
          ($_ (nt:require active-scope-reported (nt:contains $active "(CodingLaunch running")))
          ($_ (nt:require active-scope-not-repinned (not (nt:contains $active "(CodingLaunch same"))))
          ($inactive (cat:child $root "scope-inactive" (("PATH" (cat:scope-stub $root 3))) $forms))
          ($_ (nt:require inactive-scope-superseded (not (nt:equal (cat:run $d) $t0))))
          ($_ (nt:require inactive-scope-reserved (nt:contains $inactive "(CodingLaunch reserved"))))
     True))

; CodingSessionRepin: one level of previous; executions and uncertain records kept.
(= (cat:repin-keeps $root $program)
   (let* (($d (cat:session $root "work-repin-keeps" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $d NoNote))
          ($done "0123456789abcdef0123456789abcdef") ($open "fedcba9876543210fedcba9876543210")
          ($_ (cio:execution-create $d $done "echo done")) ($_ (cio:execution-finish $d $done "exited" 0 0.5))
          ($_ (cio:execution-create $d $open "sleep 9"))
          ($records (cat:files-text (path:join $d "executions")
                      ((str:concat $done ".json") (str:concat $open ".json"))))
          ($_ (cat:pin-elsewhere $d))
          ($_ (nt:require first-repin (case (cio:repin $program $d) (((CodingLaunch repinned $r $b $t $x) True) ($o False)))))
          ($_ (cat:pin-elsewhere $d))
          ($_ (nt:require second-repin (case (cio:repin $program $d) (((CodingLaunch repinned $r $b $t $x) True) ($o False)))))
          ($previous (cat:field $d "runtime.json" "previous"))
          ($_ (nt:require one-level-of-previous (nt:equal (coding:field $previous "previous") JsonNull)))
          ($_ (nt:require executions-kept (nt:equal (cat:files-text (path:join $d "executions")
                                                       ((str:concat $done ".json") (str:concat $open ".json"))) $records)))
          ($_ (nt:require uncertain-record-kept (nt:contains (cio:execution-result $d $open) "uncertain")))
          ($_ (nt:require both-repins-logged (nt:equal (size-atom (cio:object-lines (str:split "\n" (fs:read-text (path:join $d "runs.jsonl"))))) 2))))
     True))

; A check reads a copy: changes to the checkpoint during it refuse the move,
; guidance appended meanwhile does not, and a re-reserved run refuses it.
(= (cat:during-check $root $program)
   (let* (($d (cat:session $root "work-during" "{\"status\":\"done\"}"))
          ($_ (cio:freeze-runtime $program $d NoNote))
          ($_ (cat:pin-elsewhere $d)) ($_ (cat:check-mode $d "messages"))
          ($_ (nt:require changed-checkpoint-refused (case (cio:repin $program $d) (((CodingLaunch changed $r $b $t $x) True) ($o False)))))
          ($_ (nt:require refused-pin-kept (nt:equal (cat:field $d "runtime.json" "digest") (JsonString "old-digest"))))
          ($_ (cat:check-mode $d "inbox"))
          ($_ (nt:require guidance-during-check-allowed (case (cio:repin $program $d) (((CodingLaunch repinned $r $b $t $x) True) ($o False)))))
          ($_ (nt:require guidance-kept (nt:contains (fs:read-text (path:join $d "inbox.jsonl")) "guidance")))
          ($_ (cat:pin-elsewhere $d)) ($_ (cat:check-mode $d "manifest"))
          ($_ (nt:require rereserved-run-refused (case (cio:repin $program $d) (((CodingLaunch changed $r $b $t $x) True) ($o False)))))
          ($_ (cat:check-mode $d "ready-substring")) ($_ (cat:pin-elsewhere $d))
          ($_ (nt:require ready-substring-is-failure
                (case (cio:repin $program $d) (((CodingLaunch failed $r $b $t $why) (nt:contains $why "inside")) ($o False)))))
          ($_ (cat:check-mode $d "fail"))
          ($_ (nt:require failed-check-reported (case (cio:repin $program $d) (((CodingLaunch failed $r $b $t $x) True) ($o False)))))
          ($_ (nt:require no-check-copy-left (nt:equal (fs:entries (path:join $root "memory/coding-checks")) ()))))
     True))

; The real checker reads a copy only: the live checkpoint is byte-identical
; afterwards, its environment has no ledger or provider credentials.
(= (cat:real-check $root)
   (let* (($real (system:cwd))
          ($d (cat:session $root "work-real-check" "{\"status\":\"done\"}"))
          ($_ (fs:write (path:join $d "messages.jsonl")
                "{\"role\":\"user\",\"content\":\"t\"}\n{\"role\":\"assistant\",\"content\":\"\",\"tool_calls\":[{\"id\":\"c1\",\"type\":\"function\",\"function\":{\"name\":\"exec_command\",\"arguments\":\"{}\"}}]}\n"))
          ($names ("task.json" "messages.jsonl" "state.json"))
          ($before (cat:files-text $d $names))
          ($bundle (case (cio:freeze-bundle $real (path:join $root "memory/coding-runtimes")) (((FrozenBundle $t $x) $t) ($o $o))))
          ($_ (nt:require real-check-passes (nt:equal (cio:check-checkpoint $d $bundle (cio:engine-binary)) (CheckpointPassed))))
          ($_ (nt:require live-checkpoint-untouched (nt:equal (cat:files-text $d $names) $before)))
          ($env (repr (cio:check-environment $bundle "/tmp")))
          ($_ (nt:require check-has-no-ledger (not (nt:contains $env "LEDGER"))))
          ($_ (nt:require check-has-no-keys (not (nt:contains $env "API_KEY"))))
          ($_ (fs:write (path:join $d "task.json") "not json"))
          ($_ (nt:require real-check-refuses-broken-task
                (case (cio:check-checkpoint $d $bundle (cio:engine-binary)) (((CheckpointRefused $why) True) ($o False))))))
     True))

; Durability faults: before the rename the old file stands (failed); after
; it, durability is unknown (uncertain) and a later resume recovers.
(= (cat:durability $root $program)
   (let* (($d (cat:session $root "work-fsync" "{\"status\":\"stopped\",\"steps\":5}"))
          ($_ (cio:freeze-runtime $program $d NoNote)) ($t1 (cat:run $d))
          ($claim (cat:child $root "fault-claim" (cat:fault $root $d "directories")
                    (format-args "!(println! (Claimed (cio:consume-run {} {} \"1:a\" {})))\n" ((repr $d) (repr $t1) (repr (cat:root $d))))))
          ($_ (nt:require uncertain-claim-refused (nt:contains $claim "(Claimed 0)")))
          ($_ (nt:require uncertain-claim-logged (nt:contains (fs:read-text (path:join $d "run.log")) "uncertain publication")))
          ($_ (nt:require claim-renamed-before-fault (nt:equal (cat:field $d "runtime.json" "phase") (JsonString "started"))))
          ($_ (nt:require state-not-claimed (nt:equal (cat:field $d "state.json" "status") (JsonString "stopped"))))
          ($_ (cio:freeze-runtime $program $d NoNote)) ($t2 (cat:run $d))
          ($_ (nt:require recovered-reservation (not (nt:equal $t1 $t2))))
          ($_ (nt:require recovered-claim (nt:equal (cio:consume-run $d $t2 (process:owner) (cat:root $d)) 1)))
          ($write (cat:child $root "fault-write" (cat:fault $root $d "directories")
                    (format-args "!(println! (Wrote (cio:worker-state {} {} {} \"running\" 6 \"step 6\" 0 \"\" NoReport)))\n"
                      ((repr $d) (repr $t2) (repr (process:owner))))))
          ($_ (nt:require uncertain-write-refused (nt:contains $write "(Wrote 0)")))
          ($_ (fs:write (path:join $d "state.json") (cat:running (cat:dead) $t2)))
          ($settle (cat:child $root "fault-settle" (cat:fault $root $d "all")
                     (format-args "!(println! (Settled (cio:settle-ending {} {})))\n" ((repr $d) (repr (cat:dead))))))
          ($_ (nt:require failed-settlement-unmarked (nt:contains $settle "(Settled \"\")")))
          ($_ (nt:require failed-settlement-logged (nt:contains (fs:read-text (path:join $d "run.log")) "settle error")))
          ($_ (fs:write (path:join $d "state.json") "{\"status\":\"stopped\",\"steps\":5}"))
          ($_ (cat:pin-elsewhere $d))
          ($before (fs:read-text (path:join $d "runtime.json")))
          ($early (cat:child $root "fault-repin-early" (cat:fault $root $d "all")
                    (format-args "!(println! (cio:repin {} {}))\n" ((repr $program) (repr $d)))))
          ($_ (nt:require failure-before-rename (nt:contains $early "(CodingLaunch failed")))
          ($_ (nt:require pin-unchanged-before-rename (nt:equal (fs:read-text (path:join $d "runtime.json")) $before)))
          ($_ (nt:require no-run-logged-before-rename (not (fs:exists (path:join $d "runs.jsonl")))))
          ($late (cat:child $root "fault-repin-late" (cat:fault $root $d "directories")
                   (format-args "!(println! (cio:repin {} {}))\n" ((repr $program) (repr $d)))))
          ($_ (nt:require failure-after-rename-uncertain (nt:contains $late "(CodingLaunch uncertain")))
          ($_ (nt:require renamed-pin-moved (not (nt:equal (cat:field $d "runtime.json" "digest") (JsonString "old-digest"))))))
     True))

; Competing observers record one ending, whose report is its progress.
(= (cat:competing $root)
   (let* (($d (cat:session $root "work-competing" (cat:running (cat:dead) "r")))
          ($_ (fs:write (path:join $d "runtime.json") "{\"run\":\"r\",\"phase\":\"started\"}"))
          ($form (format-args "!(println! (Settled (cio:settle-ending {})))\n" ((repr $d))))
          ($script (path:join $root "competing.metta"))
          ($_ (fs:write $script (str:join "" ("!(import! &self " (repr (nt:join (system:cwd) "src/coding_io_cetta.metta")) ")\n" $form))))
          ($binary (nt:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
          ($argv ($binary "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $script))
          ($options ((cwd $root) (env (nt:environment $root)) (max-bytes 65536)))
          ($p1 (proc:spawn $argv $options)) ($p2 (proc:spawn $argv $options))
          ($p3 (proc:spawn $argv $options)) ($p4 (proc:spawn $argv $options))
          ($outs (str:join "" ((cat:output (proc:await $p1)) (cat:output (proc:await $p2))
                               (cat:output (proc:await $p3)) (cat:output (proc:await $p4)))))
          ($_ (proc:close $p1)) ($_ (proc:close $p2)) ($_ (proc:close $p3)) ($_ (proc:close $p4))
          ($_ (nt:require one-observer-marks (nt:equal (size-atom (cdr-atom (str:split "(Settled \"interrupted\")" $outs))) 1)))
          ($_ (nt:require others-see-it-settled (nt:equal (size-atom (cdr-atom (str:split "(Settled \"\")" $outs))) 3)))
          ($_ (nt:require report-is-progress
                (nt:equal (cat:field $d "state.json" "progress") (JsonString (fs:read-text (path:join $d "report.txt")))))))
     True))
(= (cat:output $result)
   (case $result (((proc:exited 0 $out $err) $out) ((proc:result (proc:exited 0) proc:normal $out $err $capture) $out) ($o (repr $o)))))

; A session keeps the engine file it started on: a link flip later does not move it.
(= (cat:engine-pin $root $program)
   (let* (($engines (path:join $root "engines")) ($_ (fs:make-directories $engines 504))
          ($_ (fs:write (path:join $engines "engine-v1") "engine one")) ($_ (fs:write (path:join $engines "engine-v2") "engine two"))
          ($link (path:join $engines "current"))
          ($_ (nt:exit-ok (proc:run ("ln" "-sfn" "engine-v1" $link) $engines (nt:environment $root) 5000 4096)))
          ($d (cat:session $root "work-engine" "{\"status\":\"done\"}"))
          ($pinned (cat:child $root "engine-pin" (("CETTA_BIN" $link))
                     (format-args "!(println! (cio:freeze-runtime {} {} NoNote))\n" ((repr $program) (repr $d)))))
          ($_ (nt:require engine-file-recorded (nt:equal (cat:field $d "runtime.json" "binary") (JsonString (path:join $engines "engine-v1")))))
          ($_ (nt:exit-ok (proc:run ("ln" "-sfn" "engine-v2" $link) $engines (nt:environment $root) 5000 4096)))
          ($_ (cat:child $root "engine-resume" (("CETTA_BIN" $link))
                (format-args "!(println! (cio:freeze-runtime {} {} NoNote))\n" ((repr $program) (repr $d)))))
          ($_ (nt:require link-flip-keeps-engine (nt:equal (cat:field $d "runtime.json" "binary") (JsonString (path:join $engines "engine-v1"))))))
     True))

(= (cat:run-all)
   (let* (($root (nt:join (nt:required-env "NATIVE_TEST_ARTIFACTS") (str:concat "coding-authority-" (system:uuid4-hex))))
          ($_ (fs:make-directories $root 504))
          ($program (cat:program $root))
          ($_ (cat:stale-reaper $root $program)) ($_ (cat:stale-launch $root $program))
          ($_ (cat:consumed-once $root $program)) ($_ (cat:stop-revokes $root $program))
          ($_ (cat:live-worker $root $program)) ($_ (cat:scope-guard $root $program))
          ($_ (cat:repin-keeps $root $program)) ($_ (cat:during-check $root $program))
          ($_ (cat:real-check $root)) ($_ (cat:durability $root $program))
          ($_ (cat:competing $root)) ($_ (cat:engine-pin $root $program)))
     CodingAuthorityPassed))
!(nt:require coding-authority-completed (nt:equal (collapse (cat:run-all)) (CodingAuthorityPassed)))
!(println! NATIVE_CODING_AUTHORITY_PASSED)
