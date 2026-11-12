; Mutation controls run in temporary source copies, never the working checkout.
!(import! &self ../tests/provider_native_qualification.metta)
(= (im:replace $path $old $new)
   (let* (($source (fs:read-text $path)) ($pieces (str:split $old $source))
          ($_ (nt:require mutation-target-present (> (preq:size $pieces) 1)))
          ($changed (str:join $new $pieces))
          ($_ (nt:require mutation-changes-source (not (nt:equal $changed $source)))))
     (fs:write $path $changed)))
(= (im:case $name $file $old $new $driver $witness)
   (let* (($root (system:cwd)) ($core (nt:required-env "NATIVE_TEST_CORE"))
          ($sdk (nt:required-env "NATIVE_TEST_SDK")) ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($fixture (nt:required-env "NATIVE_TEST_HTTP_FIXTURE"))
          ($parent (nt:required-env "NATIVE_TEST_MUTANTS"))
          ($suffix (str:byte-slice (system:uuid4-hex) 0 10))
          ($copy (nt:join $parent (str:concat $name (str:concat "-" $suffix))))
          ($_ (fs:make-directories $copy 448)) ($base (nt:environment $copy))
          ($_ (nt:exit-ok (proc:run ("cp" "-a" "--reflink=auto" "src" "channels" "modes" "config" "tests" "scripts" $copy)
                $root $base 30000 65536)))
          ($_ (im:replace (nt:join $copy $file) $old $new))
          ($env (preq:append $base (("NATIVE_TEST_CORE" $core) ("NATIVE_TEST_SDK" $sdk)
             ("NATIVE_TEST_ARTIFACTS" $artifacts) ("NATIVE_TEST_HTTP_FIXTURE" $fixture))))
          ($result (proc:run ((nt:join $core "cetta") "--lang" "petta" "--import-mode" "ancestor-walk"
                    "--quiet" $driver) $copy $env 120000 1048576))
          ($_ (fs:write (nt:join $copy "result.metta") (preq:repr $result)))
          ($_ (nt:require mutant-killed-by-required-witness
             (case $result
               (((proc:exited $code $out $err) (and (> $code 0) (nt:contains $out $witness)))
                ($bad False)))))
          ($_ (println! (INPUT_MUTANT_KILLED $name $copy)))) 1))
(= (im:run)
   (let* (($_ (im:case "missing-settlement" "src/input_ledger.metta"
               "($_ (input-ledger:record-all $space (input-ledger:inputs-of $space $id) $now))"
               "($_ ok)" "scripts/test_input_lifecycle_cetta.metta" "FAIL defer-settles-every-linked-delivery"))
          ($_ (im:case "settled-redelivery" "src/input_scope_cetta.metta"
               "(True True) (true True) (False False) (false False)"
               "(True False) (true False) (False False) (false False)"
               "scripts/test_input_delivery_canary_cetta.metta" "FAIL settled-mail-is-quiet"))
          ($_ (im:case "resampled-scope" "src/working_state_host.py"
               "captured = strategy_host._turn_input_ids" "captured = None"
               "scripts/test_input_delivery_canary_cetta.metta" "FAIL working-scope-matches-presented-deliveries"))) NativeInputMutantsPassedV1))
!(nt:require mutation-driver-completed (nt:equal (collapse (im:run)) (NativeInputMutantsPassedV1)))
!(println! NATIVE_INPUT_MUTANTS_PASSED)
