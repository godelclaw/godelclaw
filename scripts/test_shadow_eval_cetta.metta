; The CLI itself is exercised against small sealed releases, Python-enabled
; baseline vs Python-free candidate engine policies, structured diff categories
; (outputs, effects, wire requests, timing), and the native full-path replay
; driver (`scripts/shadow_replay.metta`). Test output is read from the CLI's
; durable comparison receipt, not from a pretty-print.
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/state_file_cetta.metta)
!(import! &self (library list))

(= (shadow-test:release $root $name $value)
   (let* (($release (nt:join $root $name))
          ($files (nt:join $release "files"))
          ($_ (fs:make-directories $files 448))
          ($_ (fs:write (nt:join $files "value.txt") $value))
          ($hash (str:sha256 $value))
          ($manifest (JsonObject ((JsonPair "schema" 1) (JsonPair "release" $hash)
             (JsonPair "files" (JsonObject ((JsonPair "value.txt" $hash)))))))
          ($_ (fs:write (nt:join $release "manifest.json") (json:stringify-python $manifest))))
     $release))

; Stage a synthetic engine bundle copying NATIVE_TEST_CORE with custom
; python_enabled / python_prefix manifest fields to qualify engine-policy gates.
(= (shadow-test:engine-copy $root $name $core $py-enabled $py-prefix)
   (let* (($dest (nt:join $root $name))
          ($_ (nt:exit-ok (proc:run ("cp" "-r" $core $dest) $root (nt:environment $root) 15000 65536)))
          ($orig (sf:native-object (nt:join $dest "build-manifest.json")))
          ($next (sf:put-all $orig
                   ((JsonPair "python_enabled" $py-enabled)
                    (JsonPair "python_version" (if $py-enabled "3.12" JsonNull))
                    (JsonPair "python_prefix" (if (== $py-prefix "") JsonNull $py-prefix)))))
          ($_ (fs:write (nt:join $dest "build-manifest.json") (sf:python-json $next))))
     $dest))

(= (shadow-test:fake-python-prefix $root $name)
   (let* (($prefix (nt:join $root $name))
          ($bin (nt:join $prefix "bin"))
          ($lib (nt:join $prefix "lib"))
          ($_ (fs:make-directories $bin 448))
          ($_ (fs:make-directories $lib 448))
          ($py3 (nt:join $bin "python3"))
          ($_ (fs:write $py3 "#!/bin/sh\necho fake-python\n"))
          ($_ (nt:exit-ok (proc:run ("chmod" "755" $py3) $root (nt:environment $root) 5000 4096)))
          ($_ (fs:write (nt:join $lib "py-marker") "baseline-python-only")))
     $prefix))

(= (shadow-test:hash-staged-files $dest-files $names $pairs)
   (if (== $names ()) $pairs
     (let* (($rel (car-atom $names))
            ($to (nt:join $dest-files $rel))
            ((fs:hashed-v1 $digest $_) (fs:sha256-file $to)))
       (shadow-test:hash-staged-files $dest-files (cdr-atom $names)
         (cons-atom (JsonPair $rel $digest) $pairs)))))

(= (shadow-test:current-release $root $name)
   (let* (($src-root (system:cwd))
          ($release (nt:join $root $name))
          ($files (nt:join $release "files"))
          ($_ (fs:make-directories $files 448))
          ($sel (sf:native-object (nt:join $src-root "config/runtime-files.json")))
          ($names (case (sf:get $sel "files" (JsonArray ())) (((JsonArray $xs) $xs) ($o ()))))
          ($list-path (nt:join $release "files.list"))
          ($_ (fs:write $list-path (str:concat (str:join "\n" $names) "\n")))
          ($copy-cmd (str:join "" ("tar -C " (repr $src-root) " -cf - -T " (repr $list-path) " | tar -C " (repr $files) " -xf -")))
          ($_ (nt:exit-ok (proc:run ("sh" "-c" $copy-cmd) $src-root inherit 15000 65536)))
          ($_ (fs:remove $list-path))
          ($pairs (list:reverse (shadow-test:hash-staged-files $files $names ())))
          ($files-obj (JsonObject $pairs))
          ($identity (str:sha256 (sf:python-json $files-obj)))
          ($manifest (JsonObject ((JsonPair "schema" 1)
                                  (JsonPair "release" $identity)
                                  (JsonPair "files" $files-obj))))
          ($_ (fs:write (nt:join $release "manifest.json") (sf:python-json $manifest))))
     $release))

(= (shadow-test:driver $file $host-marker)
   (fs:write $file (str:join "\n"
     ("!(import! &self (library fs))"
      "!(import! &self (library json))"
      "!(import! &self (library system))"
      "!(let* (($value (fs:read-text \"/app/value.txt\"))"
      "         ($input (fs:read-text (system:getenv-or-default \"SHADOW_INPUT\" \"\")))"
      "         ($_ (fs:write \"/app/value.txt\" \"must stay read-only\"))"
      "         ($still (fs:read-text \"/app/value.txt\"))"
      (str:concat "         ($hidden-path (not (fs:exists " (str:concat (repr $host-marker) ")))"))
      "         ($hidden-env (== (system:getenv-or-default \"SHADOW_CALLER_FIXTURE\" \"\") \"\"))"
      "         ($ok (and (and $hidden-path $hidden-env) (and (== $value $still) (== $input \"seed λ\"))))"
      "         ($report (JsonObject ((JsonPair \"schema\" 1) (JsonPair \"complete\" True)"
      "              (JsonPair \"checks_passed\" $ok)"
      "              (JsonPair \"observations\" (JsonArray ($value $value $input)))))))"
      "   (fs:write (system:getenv-or-default \"SHADOW_REPORT\" \"\") (json:stringify $report)))"
      ""))))

; Driver verifying Python-enabled baseline mounts /python read-only while the
; Python-free candidate has no /python, no PYTHONHOME, and no PYTHONPATH.
(= (shadow-test:python-isolation-driver $file $host-marker)
   (fs:write $file (str:join "\n"
     ("!(import! &self (library fs))"
      "!(import! &self (library json))"
      "!(import! &self (library system))"
      "!(let* (($side (system:getenv-or-default \"SHADOW_SIDE\" \"\"))"
      "         ($py-flag (system:getenv-or-default \"SHADOW_PYTHON_ENABLED\" \"\"))"
      "         ($py-home (system:getenv-or-default \"PYTHONHOME\" \"\"))"
      "         ($py-path (system:getenv-or-default \"PYTHONPATH\" \"\"))"
      (str:concat "         ($hidden-host (not (fs:exists " (str:concat (repr $host-marker) ")))"))
      "         ($side-ok (if (== $side \"baseline\")"
      "                     (let* (($_ (fs:write \"/python/lib/py-marker\" \"must-fail\"))"
      "                            ($marker (if (fs:is-file \"/python/lib/py-marker\") (fs:read-text \"/python/lib/py-marker\") \"\")))"
      "                       (and (== $py-flag \"1\") (and (== $py-home \"/python\") (== $marker \"baseline-python-only\"))))"
      "                     (and (== $py-flag \"0\") (and (== $py-home \"\") (and (== $py-path \"\") (not (fs:exists \"/python\")))))))"
      "         ($ok (and $hidden-host $side-ok))"
      "         ($report (JsonObject ((JsonPair \"schema\" 1) (JsonPair \"complete\" True)"
      "              (JsonPair \"checks_passed\" $ok)"
      "              (JsonPair \"observations\" (JsonArray (\"isolated-parity\")))))))"
      "   (fs:write (system:getenv-or-default \"SHADOW_REPORT\" \"\") (json:stringify $report)))"
      ""))))

; Driver emitting identical observations while toggling a single structured
; category (outputs, effects, wire tokens, or wire cache_layout) via SHADOW_INPUT.
(= (shadow-test:structured-driver $file)
   (fs:write $file (str:join "\n"
     ("!(import! &self (library fs))"
      "!(import! &self (library json))"
      "!(import! &self (library system))"
      "!(let* (($val (fs:read-text \"/app/value.txt\"))"
      "         ($mode (fs:read-text (system:getenv-or-default \"SHADOW_INPUT\" \"\")))"
      "         ($is-cand (== $val \"cand\"))"
      "         ($out-v (if (and $is-cand (== $mode \"diff-outputs\")) \"cand-out\" \"same-out\"))"
      "         ($eff-v (if (and $is-cand (== $mode \"diff-effects\")) 2 1))"
      "         ($tok-v (if (and $is-cand (== $mode \"diff-wire-tokens\")) 512 256))"
      "         ($role-v (if (and $is-cand (== $mode \"diff-wire-cache\")) \"developer\" \"assistant\"))"
      "         ($wire (JsonObject ((JsonPair \"name\" \"probe-0\") (JsonPair \"protocol\" \"responses\")"
      "                   (JsonPair \"model\" \"gpt-fixture\") (JsonPair \"prompt_bytes\" 1000)"
      "                   (JsonPair \"max_tokens\" 1024) (JsonPair \"estimated_tokens\" $tok-v)"
      "                   (JsonPair \"cache_layout\" (JsonObject ((JsonPair \"role_sequence\" (JsonArray (\"system\" $role-v \"user\")))"
      "                             (JsonPair \"prefix_roles_before_first_user\" (JsonArray (\"system\" $role-v)))))))))"
      "         ($report (JsonObject ((JsonPair \"schema\" 1) (JsonPair \"complete\" True)"
      "              (JsonPair \"checks_passed\" True)"
      "              (JsonPair \"observations\" (JsonArray (\"same-obs\")))"
      "              (JsonPair \"outputs\" (JsonArray ((JsonObject ((JsonPair \"result\" $out-v))))))"
      "              (JsonPair \"effects\" (JsonArray ((JsonObject ((JsonPair \"effects\" $eff-v))))))"
      "              (JsonPair \"wire_requests\" (JsonArray ($wire)))))))"
      "   (fs:write (system:getenv-or-default \"SHADOW_REPORT\" \"\") (json:stringify $report)))"
      ""))))

(= (shadow-test:invoke-engines $root $left $right $base-eng $cand-eng $suite $input $label $pairs)
   (let* (($directory (nt:join $root $label))
          ($env (cons-atom ("SHADOW_CALLER_FIXTURE" "outside the evaluation") (nt:environment $root)))
          ($base-argv ("bash" (nt:join (system:cwd) "run.sh") "--shadow-eval"
                       "--baseline" $left "--candidate" $right
                       "--baseline-engine" $base-eng "--candidate-engine" $cand-eng
                       "--suite" $suite "--pairs" $pairs "--output" $directory))
          ($argv (if (== $input "") $base-argv (list:append $base-argv ("--input" $input))))
          ($trial (proc:run $argv (system:cwd) $env 90000 1048576))
          ($_ (fs:write (str:concat $directory ".log") (repr $trial))))
     (case $trial
       (((proc:exited $code $out $err) (ShadowResult $code $directory))
        ($failed (progn (println! (SHADOW_DRIVER_FAILED $label $failed)) (system:exit 1)))))))

(= (shadow-test:invoke $root $left $right $suite $input $label $pairs)
   (let $core (nt:required-env "NATIVE_TEST_CORE")
     (shadow-test:invoke-engines $root $left $right $core $core $suite $input $label $pairs)))

(= (shadow-test:report $directory $expected)
   (let $value (sf:native-object (nt:join $directory "report.json"))
     (case (sf:get $value "pairs" JsonNull)
       (((JsonArray $rows) (nt:require (pair-outcomes $expected)
          (== (map-atom $rows $row (sf:get $row "status" "")) $expected)))
        ($bad (nt:require comparison-receipt-present False))))))

(= (shadow-test:pair0-get $directory)
   (let $report (sf:native-object (nt:join $directory "report.json"))
     (case (sf:get $report "pairs" JsonNull)
       (((JsonArray $rows) (if (== $rows ()) (JsonObject ()) (car-atom $rows)))
        ($bad (JsonObject ()))))))

(= (shadow-test:first-wire-probe $diffs)
   (case (sf:get (sf:get (sf:get $diffs "details" (JsonObject ())) "wire_requests" (JsonObject ())) "probes" (JsonArray ()))
     (((JsonArray $rows) (if (== $rows ()) (JsonObject ()) (car-atom $rows)))
      ($bad (JsonObject ())))))

!(let* (($core (nt:required-env "NATIVE_TEST_CORE"))
         ($root (nt:join (nt:required-env "NATIVE_TEST_ARTIFACTS") (system:uuid4-hex)))
         ($_ (fs:make-directories $root 448))
         ($marker (nt:join $root "host-marker"))
         ($_ (fs:write $marker "fixture outside the trial"))
         ($input (nt:join $root "input"))
         ($_ (fs:write $input "seed λ"))
         ($suite (nt:join $root "observed.metta"))
         ($_ (shadow-test:driver $suite $marker))
         ($left (shadow-test:release $root "a" "observed value"))
         ($right (shadow-test:release $root "b" "changed value"))
         ; 1. Existing baseline contracts
         ((ShadowResult $equal-code $equal) (shadow-test:invoke $root $left $left $suite $input "equal" "2"))
         ($_ (nt:require equal-trials-pass (== $equal-code 0)))
         ($_ (shadow-test:report $equal ("match" "match")))
         ($report (sf:native-object (nt:join $equal "report.json")))
         ($_ (nt:require timing-order-alternates
                (case (sf:get $report "pairs" JsonNull)
                  (((JsonArray ($first $second)) (and (== (sf:get $first "order" "") "baseline-first")
                                                    (== (sf:get $second "order" "") "candidate-first")))
                   ($bad False)))))
         ($observed (json:parse-python (fs:read-text (nt:join $equal "1/candidate/observations.json"))))
         ($_ (nt:require retained-occurrences-include-equal-values
                (== $observed (JsonArray ("observed value" "observed value" "seed λ")))))
         ((ShadowResult $changed-code $changed) (shadow-test:invoke $root $left $right $suite $input "changed" "1"))
         ($_ (nt:require changed-observations-fail (== $changed-code 1)))
         ($_ (shadow-test:report $changed ("different")))
         ($wrong (nt:join $root "wrong.metta"))
         ($_ (fs:write $wrong (str:concat "!(import! &self (library fs))\n"
              "!(fs:write \"/work/observations.json\" \"{\\\"schema\\\":1,\\\"complete\\\":true,\\\"checks_passed\\\":false,\\\"observations\\\":[\\\"same wrong answer\\\"]}\")\n")))
         ((ShadowResult $wrong-code $failed-checks) (shadow-test:invoke $root $left $left $wrong $input "failed-checks" "1"))
         ($_ (nt:require matching-failed-checks-are-rejected (== $wrong-code 1)))
         ($_ (shadow-test:report $failed-checks ("failed-checks")))
         ($empty (nt:join $root "empty.metta"))
         ($_ (fs:write $empty "!(println! \"No observation was produced\")\n"))
         ((ShadowResult $empty-code $incomplete) (shadow-test:invoke $root $left $left $empty $input "incomplete" "1"))
         ($_ (nt:require successful-exit-without-observation-is-incomplete (== $empty-code 2)))
         ($_ (shadow-test:report $incomplete ("incomplete")))
         ($_ (fs:write (nt:join $right "files/value.txt") "changed since this release was built"))
         ((ShadowResult $drift-code $drift) (shadow-test:invoke $root $left $right $suite $input "drift" "1"))
         ($_ (nt:require manifest-drift-blocks-execution
                (and (== $drift-code 2) (not (fs:exists (nt:join $drift "1"))))))
         ; 2. Python-enabled baseline accepted & isolated; Python-enabled candidate refused
         ($py-prefix (shadow-test:fake-python-prefix $root "fake-py"))
         ($py-eng (shadow-test:engine-copy $root "eng-py" $core True $py-prefix))
         ($py-bad-eng (shadow-test:engine-copy $root "eng-py-bad" $core True "/nonexistent/python"))
         ($py-suite (nt:join $root "py-isolation.metta"))
         ($_ (shadow-test:python-isolation-driver $py-suite $marker))
         ((ShadowResult $cand-py-code $cand-py)
           (shadow-test:invoke-engines $root $left $left $core $py-eng $py-suite "" "cand-py-refused" "1"))
         ($_ (nt:require python-enabled-candidate-refused
                (and (== $cand-py-code 2) (not (fs:exists (nt:join $cand-py "1"))))))
         ((ShadowResult $bad-py-code $bad-py)
           (shadow-test:invoke-engines $root $left $left $py-bad-eng $core $py-suite "" "base-py-bad-refused" "1"))
         ($_ (nt:require python-enabled-baseline-bad-prefix-refused
                (and (== $bad-py-code 2) (not (fs:exists (nt:join $bad-py "1"))))))
         ((ShadowResult $base-py-code $base-py)
           (shadow-test:invoke-engines $root $left $left $py-eng $core $py-suite "" "base-py-ok" "1"))
         ($_ (nt:require python-enabled-baseline-accepted (== $base-py-code 0)))
         ($_ (shadow-test:report $base-py ("match")))
         ($py-rep (sf:native-object (nt:join $base-py "report.json")))
         ($_ (nt:require python-enabled-recorded-in-report
                (and (== (sf:get (sf:get $py-rep "baseline" (JsonObject ())) "python_enabled" False) True)
                     (== (sf:get (sf:get $py-rep "candidate" (JsonObject ())) "python_enabled" True) False))))
         ; 3. Structured diff reporting: outputs, effects, wire_requests (tokens & cache layout), timing
         ($s-base (shadow-test:release $root "s-base" "base"))
         ($s-cand (shadow-test:release $root "s-cand" "cand"))
         ($s-suite (nt:join $root "structured.metta"))
         ($_ (shadow-test:structured-driver $s-suite))
         ($m-match (nt:join $root "mode-match")) ($_ (fs:write $m-match "match"))
         ($m-out (nt:join $root "mode-out")) ($_ (fs:write $m-out "diff-outputs"))
         ($m-eff (nt:join $root "mode-eff")) ($_ (fs:write $m-eff "diff-effects"))
         ($m-tok (nt:join $root "mode-tok")) ($_ (fs:write $m-tok "diff-wire-tokens"))
         ($m-cache (nt:join $root "mode-cache")) ($_ (fs:write $m-cache "diff-wire-cache"))
         ((ShadowResult $sm-code $sm-dir) (shadow-test:invoke $root $s-base $s-cand $s-suite $m-match "struct-match" "1"))
         ($_ (nt:require structured-match-passes (== $sm-code 0)))
         ($sm-rep (sf:native-object (nt:join $sm-dir "report.json")))
         ($sm-sum (sf:get $sm-rep "summary" (JsonObject ())))
         ($_ (nt:require structured-summary-timing-present
                (and (== (sf:get $sm-sum "changed_categories" JsonNull) (JsonArray ()))
                     (!= (sf:get (sf:get $sm-sum "timing" (JsonObject ())) "baseline_median_elapsed_seconds" JsonNull) JsonNull))))
         ((ShadowResult $so-code $so-dir) (shadow-test:invoke $root $s-base $s-cand $s-suite $m-out "struct-out" "1"))
         ($so-diffs (sf:get (shadow-test:pair0-get $so-dir) "differences" (JsonObject ())))
         ($_ (nt:require structured-outputs-diff
                (and (== $so-code 1)
                     (and (== (sf:get $so-diffs "outputs" False) True)
                          (and (== (sf:get $so-diffs "effects" True) False)
                               (== (sf:get $so-diffs "changed_categories" JsonNull) (JsonArray ("outputs"))))))))
         ((ShadowResult $se-code $se-dir) (shadow-test:invoke $root $s-base $s-cand $s-suite $m-eff "struct-eff" "1"))
         ($se-diffs (sf:get (shadow-test:pair0-get $se-dir) "differences" (JsonObject ())))
         ($_ (nt:require structured-effects-diff
                (and (== $se-code 1)
                     (and (== (sf:get $se-diffs "effects" False) True)
                          (== (sf:get $se-diffs "changed_categories" JsonNull) (JsonArray ("effects")))))))
         ((ShadowResult $st-code $st-dir) (shadow-test:invoke $root $s-base $s-cand $s-suite $m-tok "struct-tok" "1"))
         ($st-diffs (sf:get (shadow-test:pair0-get $st-dir) "differences" (JsonObject ())))
         ($st-probe0 (shadow-test:first-wire-probe $st-diffs))
         ($_ (nt:require structured-wire-tokens-diff
                (and (== $st-code 1)
                     (and (== (sf:get $st-diffs "wire_requests" False) True)
                          (and (== (sf:get $st-probe0 "tokens_changed" False) True)
                               (== (sf:get $st-probe0 "cache_layout_changed" True) False))))))
         ((ShadowResult $sc-code $sc-dir) (shadow-test:invoke $root $s-base $s-cand $s-suite $m-cache "struct-cache" "1"))
         ($sc-diffs (sf:get (shadow-test:pair0-get $sc-dir) "differences" (JsonObject ())))
         ($sc-probe0 (shadow-test:first-wire-probe $sc-diffs))
         ($_ (nt:require structured-wire-cache-layout-diff
                (and (== $sc-code 1)
                     (and (== (sf:get $sc-diffs "wire_requests" False) True)
                          (and (== (sf:get $sc-probe0 "cache_layout_changed" False) True)
                               (== (sf:get $sc-probe0 "tokens_changed" True) False))))))
         ; 4. End-to-end native shadow_replay.metta (parse + dispatch + disposable tmux + wire probes incl. chatgpt)
         ($current (shadow-test:current-release $root "current-release"))
         ($replay-cfg (nt:join $root "replay-chatgpt.json"))
         ($_ (fs:write $replay-cfg "{\"include_chatgpt\":true}\n"))
         ((ShadowResult $rp-code $rp-dir)
           (shadow-test:invoke $root $current $current (nt:join (system:cwd) "scripts/shadow_replay.metta") $replay-cfg "full-replay" "1"))
         ($_ (nt:require full-shadow-replay-passes (== $rp-code 0)))
         ($_ (shadow-test:report $rp-dir ("match")))
         ($rp-cand (sf:get (shadow-test:pair0-get $rp-dir) "candidate" (JsonObject ())))
         ($rp-metrics (sf:get $rp-cand "metrics" (JsonObject ())))
         ($_ (nt:require full-shadow-replay-scenarios-and-wires
                (and (== (sf:get $rp-metrics "replay_scenarios" 0) 4)
                     (and (== (sf:get $rp-metrics "total_effects" 0) 5)
                          (== (sf:get $rp-metrics "wire_probe_count" 0) 4)))))
         ($_ (nt:require fixture-marker-and-input-still-exist
                (and (== (fs:read-text $marker) "fixture outside the trial")
                     (== (fs:read-text $input) "seed λ")))))
   (println! NATIVE_SHADOW_EVAL_PASSED))
