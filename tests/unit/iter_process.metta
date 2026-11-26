; Iter's transformation workers run each source in order in a process of its
; own: a failing source is a result and later sources still run, a timeout is
; a result too, and every run removes its files. The syntax checker reads
; bang forms without running them.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (ip:append $text)
   (str:join "" ("(= (iter-transform\n      (coding-view $messages $advertised))\n   (success\n     (coding-view\n"
                 "       (coding-message-append-one\n         $messages (coding-message user " $text "))\n       $advertised)))\n")))
(= (ip:environment $dir)
   (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("OPENAI_API_KEY" "test-only-unused")
                 ("CETTA_BIN" (system:executable)) ("METTACLAW_ITER_PROCESS_DIR" $dir)
                 ("METTACLAW_ITER_TEMP_DIR" (path:join $dir "runs")) ("METTACLAW_ITER_PROCESS_TIMEOUT_SECONDS" "0.1"))
                (nt:environment $dir)))
; Run $code after the iter modules, from a program outside the checkout.
(= (ip:guest $repo $home $dir $code)
   (let* (($program (path:join $home (str:concat "guest-" (str:concat (str:byte-slice (system:uuid4-hex) 0 8) ".metta"))))
          ($_ (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n"
                                               "!(import! &self " (path:join $repo "src/utils.metta") ")\n"
                                               "!(import! &self " (path:join $repo "src/iter_process_policy.metta") ")\n"
                                               "!(import! &self " (path:join $repo "src/metta_coding_policy.metta") ")\n" $code))))
          ($result (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo
                             (ip:environment $dir) 20000 1048576)))
     (case $result
       (((proc:exited 0 $out $err)
         (if (and (nt:contains $out "ITER_OK") (not (nt:contains $err "Assertion failed"))) $out (Failed $out $err)))
        ($bad (Failed $bad))))))
; The request frame a guest printed: the line holding a quoted JSON object.
(= (ip:frame $out)
   (let $lines (filter-atom (str:split "\n" $out) $line (str:starts-with $line "\"{"))
     (if (== $lines ()) NoFrame (json:parse (json:parse (car-atom $lines))))))
(= (ip:get $object $key) (case $object (((JsonObject $pairs) (ip:pair $pairs $key)) ($o NoField))))
(= (ip:pair $pairs $key)
   (if (== $pairs ()) NoField
     (let (JsonPair $k $v) (car-atom $pairs) (if (== $k $key) $v (ip:pair (cdr-atom $pairs) $key)))))
(= (ip:text $v) (case $v (((JsonString $s) $s) ($o $o))))
(= (ip:messages $frame) (case (ip:get $frame "messages") (((JsonArray $items) $items) ($o ()))))
(= (ip:user-contents $frame)
   (map-atom (filter-atom (ip:messages $frame) $m (== (ip:text (ip:get $m "role")) "user")) $m (ip:text (ip:get $m "content"))))
(= (ip:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (ip:last (cdr-atom $items))))
(= (ip:runs-empty? $dir) (== (fs:entries (path:join $dir "runs")) ()))
(= (ip:unquoted $text) (str:join "" (str:split "\"" (str:join "" (str:split "\\\"" $text)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; Ordered sources; a failure in the middle is a result.
         ($ordered (path:join $home "ordered")) ($_ (fs:make-directories $ordered 448))
         ($_ (fs:write (path:join $ordered "10_first.metta") (ip:append "first")))
         ($_ (fs:write (path:join $ordered "20_failure.metta") "(= (iter-transform $visible) failure)\n"))
         ($_ (fs:write (path:join $ordered "30_second.metta") (ip:append "second")))
         ($out1 (ip:guest $repo $home $ordered (str:join "\n" (
           "!(let* (($request (iter-process-prepare-request system activity advertised 1))"
           "        ($prompt (iter-process-render-request $request))"
           "        ($observations (iter-process-request-observations $request)))"
           "   (progn (println! (request:render $prompt)) (println! $observations) (println! ITER_OK)))" ""))))
         ($_ (nt:require sources-run-in-order-past-a-failure
               (== (ip:user-contents (ip:frame $out1)) ("activity" "first" "second"))))
         ($_ (nt:require each-source-reports-its-result
               (and (nt:contains (ip:unquoted $out1) "20_failure.metta failure")
                    (nt:contains (ip:unquoted $out1) "30_second.metta success"))))
         ($_ (nt:require each-run-removes-its-files (ip:runs-empty? $ordered)))
         ; A timeout is a result, and a later source still runs.
         ($slow (path:join $home "slow")) ($_ (fs:make-directories $slow 448))
         ($_ (fs:write (path:join $slow "10_timeout.metta") "(= (spin) (spin))\n(= (iter-transform $x) (spin))\n"))
         ($_ (fs:write (path:join $slow "20_after.metta") (ip:append "after-timeout")))
         ($out2 (ip:guest $repo $home $slow (str:join "\n" (
           "!(let $request (iter-process-prepare-request system activity advertised 1)"
           "   (progn (println! (request:render (iter-process-render-request $request))) (println! ITER_OK)))" ""))))
         ($_ (nt:require a-timeout-is-a-result (nt:contains $out2 "timeout")))
         ($_ (nt:require a-later-source-runs-after-a-timeout
               (== (ip:text (ip:get (ip:last (ip:messages (ip:frame $out2))) "content")) "after-timeout")))
         ($_ (nt:require a-timed-out-run-removes-its-files (ip:runs-empty? $slow)))
         ; The syntax checker reads bang forms and never runs them.
         ($source (path:join $home "input.metta")) ($marker (path:join $home "effect"))
         ($_ (fs:write $source (str:join "" ("!(fs:write " (json-stringify (JsonString $marker)) " \"must not execute\")\n"))))
         ($parsed (proc:run ((system:executable) "--lang" "petta" (path:join $repo "scripts/metta_parse_only.metta") "--" $source)
                            $repo (nt:environment $home) 20000 65536))
         ($_ (nt:require well-formed-input-is-accepted-unrun
               (and (case $parsed (((proc:exited 0 $o $e) (nt:contains $o "SYNTAX_OK")) ($b False))) (not (fs:exists $marker)))))
         ($_ (fs:write $source "(unfinished"))
         ($broken (proc:run ((system:executable) "--lang" "petta" (path:join $repo "scripts/metta_parse_only.metta") "--" $source)
                            $repo (nt:environment $home) 20000 65536))
         ($_ (nt:require malformed-input-is-refused
               (and (case $broken (((proc:exited 0 $o $e) False) ($other True))) (not (fs:exists $marker))))))
   (println! UNIT_ITER_PROCESS_OK))
