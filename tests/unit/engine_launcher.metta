; The shell launcher (run.sh) around a stand-in engine
; (tests/fixtures/launcher_engine.sh): it names the instance for its children,
; runs CeTTa with the PeTTa profile and the bootstrap first, stops a health
; check's or probe's children with itself, keeps the channel's authority from
; probes, renders configuration natively whatever Python settings it
; inherits, and records engine stops and failures (reporting a receipt it
; cannot write, keeping the status).
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (el:copy $from $to $mode)
   (progn (fs:write $to (fs:read-text $from)) (nt:exit-ok (proc:run ("chmod" $mode $to) "/" inherit 5000 64))))
; A fresh agent root under $home/$tag with the real launcher and the stand-in engine.
(= (el:agent $repo $home $tag)
   (let* (($base (path:join $home $tag)) ($root (path:join $base "agent")))
     (progn (fs:make-directories (path:join $root "scripts") 448) (fs:make-directories (path:join $root "src") 448)
            (fs:make-directories (path:join $base "home") 448)
            (el:copy (path:join $repo "run.sh") (path:join $root "run.sh") "755")
            (el:copy (path:join $repo "scripts/native_runtime_env.sh") (path:join $root "scripts/native_runtime_env.sh") "644")
            (fs:write (path:join $root "run.metta") "")
            (el:copy (path:join $repo "tests/fixtures/launcher_engine.sh") (path:join $base "engine") "755")
            $base)))
(= (el:env $base $extra)
   (list:append $extra
     (("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin")) ("LANG" "C.UTF-8") ("HOME" (path:join $base "home"))
      ("XDG_STATE_HOME" (path:join $base "state")) ("CETTA_BIN" (path:join $base "engine"))
      ("ENGINE_TEST_RESULT" (path:join $base "result")) ("METTACLAW_SKIP_INITIALIZE" "1"))))
(= (el:launch $base $args $extra)
   (case (proc:run (cons-atom (path:join $base "agent/run.sh") $args) $base (el:env $base $extra) 20000 1048576)
     (((proc:exited $code $out $err) ($code $out $err)) ((proc:signaled $sig $out $err) ((- 0 $sig) $out $err)) ($bad (-1 "" (repr $bad))))))
(= (el:results $base)
   (let $file (path:join $base "result")
     (if (fs:exists $file) (filter-atom (str:split "\n" (fs:read-text $file)) $l (not (== $l ""))) ())))
(= (el:kinds $base) (map-atom (el:results $base) $l (car-atom (str:split ":" $l))))
(= (el:failure $base) (path:join $base "state/agent/engine-failure"))
(= (el:mode $path) (str:strip-python (nt:exit-ok (proc:run ("stat" "-c" "%a" $path) "/" inherit 5000 64))))
(= (el:wait-file $owner $path $left)
   (if (and (fs:exists $path) (not (== (str:strip-python (fs:read-text $path)) ""))) True
     (if (<= $left 0) False (progn (proc:wait $owner 50) (el:wait-file $owner $path (- $left 1))))))
; Start the launcher as a process group leader whose pid lands in $base/launcher.pid.
(= (el:spawn $base $args $extra)
   (proc:spawn (list:append ("sh" "-c" "echo $$ > \"$1\"; shift; exec \"$@\"" "launch" (path:join $base "launcher.pid") (path:join $base "agent/run.sh")) $args)
               ((cwd $base) (env (el:env $base $extra)) (mode stream) (max-bytes 65536) (timeout-ms 30000) (lifetime owner))))
; The same, leading a session of its own, as a service manager starts it (its
; process group is its pid).
(= (el:spawn-service $base $args $extra)
   (proc:spawn (list:append ("sh" "-c" "echo $$ > \"$1\"; shift; exec \"$@\"" "launch" (path:join $base "launcher.pid") (path:join $base "agent/run.sh")) $args)
               ((cwd $base) (env (el:env $base $extra)) (stdout null) (stderr null) (timeout-ms 10000) (lifetime detached))))
; Stopped by the signal itself, not by the spawn's timeout (which would stop
; the children too): the launcher returns the status its trap recorded.
(= (el:stopped-by-term? $result) (case $result (((proc:result (proc:exited 143) proc:normal $o $e $c) True) ($other False))))
(= (el:wait-exists $owner $path $left)
   (if (fs:exists $path) True (if (<= $left 0) False (progn (proc:wait $owner 50) (el:wait-exists $owner $path (- $left 1))))))
(= (el:pid $base) (str:strip-python (fs:read-text (path:join $base "launcher.pid"))))
(= (el:gone? $pid) (case (proc:run ("sh" "-c" "s=/proc/$1/status; [ ! -e $s ] || grep -q '^State:.Z' $s" "gone" $pid) "/" inherit 5000 64)
                     (((proc:exited 0 $o $e) True) ($other False))))
(= (el:wait-gone $owner $pid $left) (if (el:gone? $pid) True (if (<= $left 0) False (progn (proc:wait $owner 50) (el:wait-gone $owner $pid (- $left 1))))))
(= (el:held $repo $home $phase)
   (let* (($base (el:agent $repo $home (str:concat "hold-" $phase)))
          ($_ (fs:write (path:join $base "agent/utility.metta") ""))
          ($child-file (path:join $base "child"))
          ; Detached, so nothing but the launcher itself can stop the child, and
          ; the child is checked before the handle is closed.
          ($launcher (el:spawn-service $base ("utility.metta") (("FAKE_MODE" "hold") ("FAKE_HOLD" $phase) ("PROBE_CHILD" $child-file))))
          ($started (el:wait-file $launcher $child-file 200))
          ($child (if $started (str:strip-python (fs:read-text $child-file)) "0"))
          ($args (if $started (fs:read-text (str:concat $child-file ".args")) ""))
          ($_ (nt:exit-ok (proc:run ("kill" "-TERM" (el:pid $base)) "/" inherit 5000 64)))
          ($exit (proc:await $launcher))
          ($stopped (el:wait-gone $launcher $child 100))
          ($_ (if $stopped True (proc:run ("kill" "-KILL" $child) "/" inherit 5000 64)))
          ($_ (proc:close $launcher)))
     (and $started (and (nt:contains $args (if (== $phase "health") "memory_health_cetta.metta" "utility.metta"))
                        (and (el:stopped-by-term? $exit) $stopped)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($_ (nt:require the-launcher-names-the-instance-for-its-children
               (nt:contains (fs:read-text (path:join $repo "run.sh")) "export METTACLAW_INSTANCE=\"$INSTANCE\"")))
         ($service (fs:read-text (path:join $repo "systemd/pettaclaw-godel.service")))
         ($_ (nt:require the-service-names-the-stable-engine
               (and (nt:contains $service "Environment=CETTA_BIN=%h/repos/CeTTa-runtime/cetta") (not (nt:contains $service "cetta-godel-command-boundary")))))
         ; CeTTa with the PeTTa profile, the bootstrap before the program, no module listed twice.
         ($profile (el:agent $repo $home "profile"))
         (($c1 $o1 $e1) (el:launch $profile () (("CETTA_REQUEST_RECYCLE" "1"))))
         ($line (car-atom (el:results $profile)))
         ($_ (nt:require the-launcher-runs-cetta-with-the-petta-profile
               (and (== $c1 0) (and (str:starts-with $line "cetta:cetta:") (nt:contains $line "--lang petta")))))
         ($_ (nt:require the-bootstrap-comes-first-and-no-module-twice
               (case (str:find $line (path:join $profile "agent/cetta_bootstrap.metta"))
                 (((TextNotFound $_) False)
                  ($at-boot (case (str:find $line (path:join $profile "agent/cetta_run.metta"))
                              (((TextNotFound $_) False)
                               ($at-run (and (< $at-boot $at-run)
                                             (and (not (nt:contains $line (path:join $profile "agent/src/")))
                                                  (not (nt:contains $line (path:join $profile "agent/lib_nal.metta")))))))))))))
         ; A health check's or probe's children stop with the launcher.
         ($_ (nt:require a-health-checks-children-stop-with-the-launcher (el:held $repo $home "health")))
         ($_ (nt:require a-probes-children-stop-with-the-launcher (el:held $repo $home "probe")))
         ; A probe never inherits the configured channel's authority.
         ($authority (el:agent $repo $home "authority"))
         ($_ (fs:write (path:join $authority "agent/.env")
               "export METTACLAW_TELEGRAM_SERVICE_SOCKET=/configuration/live.sock\nexport METTACLAW_TELEGRAM_BOT_TOKEN=configuration-token\nexport TELEGRAM_BOT_TOKEN=configuration-token\n"))
         ($_ (fs:write (path:join $authority "agent/utility.metta") ""))
         (($c2 $o2 $e2) (el:launch $authority ("utility.metta") (("FAKE_MODE" "channel"))))
         ($_ (nt:require a-probe-gets-no-configured-channel-authority (and (== $c2 0) (nt:contains (str:concat "\n" $o2) "\n||\n"))))
         (($c3 $o3 $e3) (el:launch $authority ("utility.metta")
                           (("FAKE_MODE" "channel") ("METTACLAW_TELEGRAM_SERVICE_SOCKET" "/fixture/channel.sock") ("METTACLAW_TELEGRAM_BOT_TOKEN" "fixture-token"))))
         ($_ (nt:require a-probe-keeps-the-channel-its-caller-gave (and (== $c3 0) (nt:contains $o3 "/fixture/channel.sock|fixture-token|\n"))))
         ; Configuration renders natively whatever Python settings are inherited.
         ($render (el:agent $repo $home "render"))
         ($_ (fs:make-directories (path:join $render "agent/config") 448))
         ($_ (fs:write (path:join $render "agent/config/local.toml") ""))
         ($_ (el:copy (path:join $repo "scripts/render_config_native.sh") (path:join $render "agent/scripts/render_config_native.sh") "644"))
         (($c4 $o4 $e4) (el:launch $render () (("CETTA_REQUEST_RECYCLE" "1") ("PYTHONHOME" (path:join $render "foreign-python-prefix"))
                                              ("PYTHONPATH" (path:join $render "foreign-modules")))))
         ($_ (nt:require configuration-renders-natively-whatever-python-is-set
               (and (== $c4 0) (nt:contains (car-atom (el:results $render)) "scripts/config_to_env_cetta.metta"))))
         ; An unrequested clean stop is a recorded failure.
         ($clean (el:agent $repo $home "clean-stop"))
         (($c5 $o5 $e5) (el:launch $clean () ()))
         ($_ (nt:require an-unrequested-stop-is-recorded
               (and (== $c5 1)
                    (and (== (el:kinds $clean) ("cetta"))
                         (and (nt:contains $e5 "stopped without a recycle request")
                              (let $f (fs:read-text (el:failure $clean))
                                (and (nt:contains $f "engine=cetta\n")
                                     (and (nt:contains $f "status=1\n")
                                          (and (nt:contains $f "reason=CeTTa stopped without a recycle request\n")
                                               (== (el:mode (el:failure $clean)) "600"))))))))))
         ; An engine failure is recorded and its status returned.
         ($failed (el:agent $repo $home "failure"))
         (($c6 $o6 $e6) (el:launch $failed () (("CETTA_EXIT_CODE" "7"))))
         ($_ (nt:require an-engine-failure-is-recorded-and-returned
               (and (== $c6 7) (and (== (size-atom (el:results $failed)) 1) (nt:contains (fs:read-text (el:failure $failed)) "status=7\n")))))
         ; An intentional stop of the service records no failure.
         ($intentional (el:agent $repo $home "intentional"))
         ($ready (path:join $intentional "cetta-ready"))
         ($service-run (el:spawn-service $intentional () (("CETTA_WAIT_FOR_TERM" "1") ("CETTA_READY" $ready))))
         ($up (el:wait-exists $service-run $ready 100))
         ($_ (nt:exit-ok (proc:run ("kill" "-TERM" "--" (str:concat "-" (el:pid $intentional))) "/" inherit 5000 64)))
         ($stopped-result (proc:await $service-run)) ($_ (proc:close $service-run))
         ($_ (nt:require an-intentional-stop-records-no-failure
               (and $up (and (case $stopped-result (((proc:result (proc:exited 0) $how $out $err $_) True) ($other False)))
                             (and (== (el:kinds $intentional) ("cetta")) (not (fs:exists (el:failure $intentional))))))))
         ; A receipt that cannot be written is reported; the status is kept.
         ($unwritable (el:agent $repo $home "unwritable"))
         ($parent (path:join $unwritable "unwritable")) ($_ (fs:make-directories $parent 448))
         ($_ (nt:exit-ok (proc:run ("chmod" "500" $parent) "/" inherit 5000 64)))
         (($c7 $o7 $e7) (el:launch $unwritable () (("CETTA_EXIT_CODE" "7") ("METTACLAW_ENGINE_FAILURE_PATH" (path:join $parent "engine-failure")))))
         ($_ (nt:exit-ok (proc:run ("chmod" "700" $parent) "/" inherit 5000 64)))
         ($_ (nt:require an-unwritable-receipt-is-reported-and-the-status-kept
               (and (== $c7 7) (and (== (el:kinds $unwritable) ("cetta")) (nt:contains $e7 "could not persist engine failure receipt"))))))
   (println! UNIT_ENGINE_LAUNCHER_OK))
