; The native shell's process contract: output and errors in order, quoting
; kept, the checkout as working directory, exit codes and signals reported
; (never as timeouts), deadlines that kill descendants, no capture limit, a
; background child that never delays the return, the service watchdog renewed
; through a long wait, and progress receipts where the engine state says.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (se:quoted $text) (json-stringify (JsonString $text)))
(= (se:environment $work $extra)
   (list:append (list:append $extra
                  (("METTACLAW_ENGINE_STATE_PATH" (path:join $work "engine.json"))
                   ("METTACLAW_PROGRESS_PATH" (path:join $work "progress.json"))
                   ("METTACLAW_SHELL_TIMEOUT_SECONDS" "120") ("SHELL_TEST_VALUE" "λ 🌸")))
                (nt:environment $work)))
; Run $program over the shell module, from the checkout; (code output error).
(= (se:run $repo $work $program $extra)
   (let* (($file (path:join $work "shell-probe.metta"))
          ($_ (fs:write $file (str:join "" ("!(import! &self (library lib_import))\n!(import! &self " (path:join $repo "src/shell_io.metta")
                                            ")\n!(import! &self " (path:join $repo "src/file_io.metta") ")\n" $program)))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $file) $repo
                     (se:environment $work $extra) 60000 16777216)
       (((proc:exited $code $out $err) ($code $out $err)) ($bad (-1 "" (repr $bad)))))))
(= (se:clean? $run)
   (let ($code $out $err) $run
     (and (== $code 0) (and (not (nt:contains $out "Error")) (not (nt:contains $err "Assertion failed"))))))
(= (se:check $command $expected) (str:join "" ("!(assert (== (shell " (se:quoted $command) ") " (se:quoted $expected) "))\n")))
(= (se:check-timed $command $seconds $expected)
   (str:join "" ("!(assert (== (shell " (se:quoted $command) " " $seconds ") " (se:quoted $expected) "))\n")))
(= (se:wait-socket $owner $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (progn (proc:wait $owner 20) (se:wait-socket $owner $socket (- $left 1))))))
(= (se:work $home $tag) (let $w (path:join $home $tag) (progn (fs:make-directories $w 448) $w)))
(= (se:kill-recorded $work)
   (let $file (path:join $work "child")
     (if (fs:exists $file) (proc:run ("sh" "-c" "kill -9 \"$(cat \"$1\")\" 2>/dev/null; true" "kill" $file) $work inherit 5000 64) True)))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; The contract, as assertions inside one program.
         ($work (se:work $home "contract"))
         ($large (path:join $work "large"))
         ($_ (nt:exit-ok (proc:run ("sh" "-c" "head -c 2097152 /dev/zero | tr '\\0' x > \"$1\"" "make" $large) $work inherit 10000 64)))
         ($contract (str:join "" (
           (se:check "printf '%s' \"$SHELL_TEST_VALUE\"" "λ 🌸")
           (se:check "printf 'one'; printf 'two' >&2; printf 'three'" "onetwothree")
           (se:check "printf '%s' 'quoted \"$value\"'" "quoted \"$value\"")
           (se:check "pwd" (str:concat $repo "\n"))
           (se:check "true" "SHELL_OK_NO_OUTPUT (exit 0)")
           (se:check "printf failure >&2; exit 7" "SHELL_ERROR exit 7\nfailure")
           (se:check-timed "printf partial; sleep 2; printf late" "0.1" "SHELL_TIMEOUT after 0.1 seconds\npartial")
           (se:check-timed "printf no-deadline" "0" "no-deadline")
           (se:check-timed "printf string-timeout" "\"1\"" "string-timeout")
           (se:check "sleep 6; printf survived" "survived")
           "!(assert (== (shell " (se:quoted (str:concat "cat " $large)) ") (read-file " (se:quoted $large) " 0)))\n"
           (se:check (str:join "" ("sleep 5 </dev/null & printf '%s' $! > '" (path:join $work "child") "'; printf background")) "background")
           "!(println! SHELL_CONTRACT_OK)\n")))
         ($run (se:run $repo $work $contract ()))
         ($_ (se:kill-recorded $work))
         ($_ (nt:require the-shell-keeps-its-contract
               (and (se:clean? $run) (let ($c $o $e) $run (nt:contains $o "SHELL_CONTRACT_OK")))))
         ; Exit 124 and signals are errors, never timeouts; a negative deadline is refused.
         ($codes (se:work $home "codes"))
         ($_ (nt:require exit-codes-and-signals-are-not-timeouts
               (let ($c2 $o2 $e2) (se:run $repo $codes (str:join "" (
                     (se:check-timed "exit 124" "1" "SHELL_ERROR exit 124\n")
                     (se:check-timed "kill -TERM $$" "1" "SHELL_ERROR signal 15\n")
                     (se:check-timed "printf should-not-run" "-0.000001" "SHELL_ERROR timeout seconds must be nonnegative")
                     "!(println! CODES_OK)\n")) ())
                 (and (== $c2 0) (and (nt:contains $o2 "CODES_OK") (not (nt:contains $e2 "Assertion failed")))))))
         ; A deadline kills the command's descendants too.
         ($deadline (se:work $home "deadline")) ($leaked (path:join $deadline "leaked"))
         ($timed (se:run $repo $deadline (str:join "" ("!(println! (shell "
                    (se:quoted (str:join "" ("(sleep 0.5; printf leaked > '" $leaked "') & printf partial; sleep 2"))) " 0.1))\n")) ()))
         ($_ (proc:run ("sleep" "0.6") $deadline inherit 5000 64))
         ($_ (nt:require a-deadline-kills-descendants
               (let ($c3 $o3 $e3) $timed
                 (and (and (nt:contains $o3 "SHELL_TIMEOUT after 0.1 seconds") (nt:contains $o3 "partial")) (not (fs:exists $leaked))))))
         ; Without a configured progress path, receipts go beside the engine state.
         ($derived (se:work $home "derived"))
         ($_ (nt:require the-progress-path-follows-the-engine-state
               (let ($c4 $o4 $e4) (se:run $repo $derived "!(println! (shell \"printf derived\"))\n" (("METTACLAW_PROGRESS_PATH" "")))
                 (and (nt:contains $o4 "derived") (and (not (nt:contains $o4 "Error")) (fs:is-file (path:join $derived "progress.json")))))))
         ; A background child never delays the return.
         ($background (se:work $home "background"))
         ($started (system:monotonic-ns))
         ($quick (se:run $repo $background (str:join "" ("!(println! (shell "
                    (se:quoted (str:join "" ("sleep 10 </dev/null & printf '%s' $! > '" (path:join $background "child") "'; printf ready"))) " 0))\n")) ()))
         ($elapsed (- (system:monotonic-ns) $started))
         ($_ (se:kill-recorded $background))
         ($_ (nt:require a-background-child-never-delays-the-return
               (let ($c5 $o5 $e5) $quick (and (nt:contains $o5 "ready") (< $elapsed 5000000000)))))
         ; A long wait renews the service watchdog and records its progress.
         ($watch (se:work $home "watch"))
         ($sink-bin (path:join $home "notify-sink"))
         ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror" (path:join $repo "tests/fixtures/notify_sink.c")
                                    "-o" $sink-bin) $repo (nt:environment $home) 30000 65536)))
         ($socket (path:join $watch "notify.sock")) ($messages (path:join $watch "messages"))
         ($sink (proc:spawn ($sink-bin $socket $messages "2" "60000") ((env (nt:environment $watch)) (max-bytes 4096) (lifetime owner))))
         ($_ (nt:require the-notification-socket-is-ready (se:wait-socket $sink $socket 300)))
         ($waited (se:run $repo $watch "!(assert (== (shell \"sleep 31; printf observed\" 0) \"observed\"))\n!(println! WAIT_OK)\n"
                          (("NOTIFY_SOCKET" $socket))))
         ($_ (proc:close-input $sink)) ($sunk (proc:await $sink)) ($_ (proc:close $sink))
         ($_ (nt:require a-long-wait-completes (let ($c6 $o6 $e6) $waited (and (nt:contains $o6 "WAIT_OK") (not (nt:contains $o6 "Error"))))))
         ($_ (nt:require the-watchdog-is-renewed-during-the-wait
               (== (filter-atom (str:split "\n" (fs:read-text $messages)) $l (not (== $l ""))) ("WATCHDOG=1" "WATCHDOG=1"))))
         ($progress (json:parse (fs:read-text (path:join $watch "progress.json"))))
         ($_ (nt:require the-wait-is-recorded-as-progress
               (and (== (json:get $progress "phase") "process-wait")
                    (and (>= (json:get $progress "sequence") 2)
                         (and (str:starts-with (json:get $progress "owner") (str:concat (repr (json:get $progress "pid")) ":"))
                              (> (json:get $progress "wait_until") (/ (system:monotonic-ns) 1000000000.0))))))))
   (println! UNIT_SHELL_ENGINE_OK))
