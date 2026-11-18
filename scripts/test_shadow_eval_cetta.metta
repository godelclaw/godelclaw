; The CLI itself is exercised against small sealed releases. Test output is
; read from the CLI's durable comparison receipt, not from a pretty-print.
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/state_file_cetta.metta)

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

(= (shadow-test:invoke $root $left $right $suite $input $label $pairs)
   (let* (($directory (nt:join $root $label))
          ($env (cons-atom ("SHADOW_CALLER_FIXTURE" "outside the evaluation") (nt:environment $root)))
          ($trial (proc:run ("bash" (nt:join (system:cwd) "run.sh") "--shadow-eval"
             "--baseline" $left "--candidate" $right "--suite" $suite "--input" $input
             "--engine" (nt:required-env "NATIVE_TEST_CORE") "--pairs" $pairs "--output" $directory)
             (system:cwd) $env 60000 1048576))
          ($_ (fs:write (str:concat $directory ".log") (repr $trial))))
     (case $trial
       (((proc:exited $code $out $err) (ShadowResult $code $directory))
        ($failed (progn (println! (SHADOW_DRIVER_FAILED $label $failed)) (system:exit 1)))))))

(= (shadow-test:report $directory $expected)
   (let $value (sf:native-object (nt:join $directory "report.json"))
     (case (sf:get $value "pairs" JsonNull)
       (((JsonArray $rows) (nt:require (pair-outcomes $expected)
          (== (map-atom $rows $row (sf:get $row "status" "")) $expected)))
        ($bad (nt:require comparison-receipt-present False))))))

!(let* (($root (nt:join (nt:required-env "NATIVE_TEST_ARTIFACTS") (system:uuid4-hex)))
         ($_ (fs:make-directories $root 448))
         ($marker (nt:join $root "host-marker"))
         ($_ (fs:write $marker "fixture outside the trial"))
         ($input (nt:join $root "input"))
         ($_ (fs:write $input "seed λ"))
         ($suite (nt:join $root "observed.metta"))
         ($_ (shadow-test:driver $suite $marker))
         ($left (shadow-test:release $root "a" "observed value"))
         ($right (shadow-test:release $root "b" "changed value"))
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
         ($_ (nt:require fixture-marker-and-input-still-exist
                (and (== (fs:read-text $marker) "fixture outside the trial")
                     (== (fs:read-text $input) "seed λ")))))
   (println! NATIVE_SHADOW_EVAL_PASSED))
