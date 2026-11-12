; Input lifecycle mutations use the common native source runner.
!(import! &self ../tests/native_source_mutation.metta)
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
