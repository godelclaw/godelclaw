; Run by the native driver against the actual SDK owner implementation. The
; binding is the native owner's: a call is bound once, to a wire for the model
; its seed named, and the host handle takes no part in it.
(= (owner-test:seed $kind $purpose)
   (py-call (native_llm_host.native_call_seed "fixture-model" 42 $kind $purpose)))
(= (owner-test:bind $plan $text $model)
   (pcall:bound $plan (preq:parse $text) "/v1/fixture" (StructuredMetadataV1 (preq:object ()))
                (PreparedProviderWireV1 "{}" False (preq:string $model) JsonNull)))
(= (owner-test:bound? $result)
   (case $result (((PreparedProviderCallV1 $owner $transport) True) ($other False))))
(= (owner-test:run)
   (let* ((($plan $text) (owner-test:seed "command" "turn"))
          ($first (owner-test:bind $plan $text "fixture-model"))
          ($_ (nt:require native-owner-bound (owner-test:bound? $first)))
          ((PreparedProviderCallV1 $owner $transport) $first)
          ($_ (nt:require matching-native-description (call:seal-matches? $owner (pcall:seal $transport))))
          ($_ (nt:require wrong-description-refused (not (call:seal-matches? $owner "other"))))
          ($_ (nt:require first-claim (call:begin-claim $owner)))
          ; A second binding of a live call would return its phase to ready,
          ; and so permit a second claim and a second request.
          ($_ (nt:require rebind-refused (not (owner-test:bound? (owner-test:bind $plan $text "fixture-model")))))
          ($_ (nt:require live-owner-not-revoked (not (call:begin-claim $owner))))
          ($_ (nt:require request-not-retained (not (host:flag (py-call (builtins.hasattr $plan "request"))))))
          ($_ (nt:require prompt-not-retained (not (host:flag (py-call (builtins.hasattr $plan "prompt_text"))))))
          ($_ (nt:require description-not-retained (not (host:flag (py-call (builtins.hasattr $plan "prepared_seal"))))))
          ($_ (call:release $owner))
          (($other $other-text) (owner-test:seed "command" "turn"))
          ($_ (nt:require wrong-model-never-bound (not (owner-test:bound? (owner-test:bind $other $other-text "other")))))
          ($_ (nt:require wrong-model-remains-unready
                (nt:equal (py-call (builtins.getattr $other "ready")) "false")))
          ($_ (nt:require wrong-model-has-no-phase
                (not (call:live? (pcall:fact (preq:parse $other-text) "correlation")))))
          (($coding $coding-text) (owner-test:seed "structured" "coding"))
          ($_ (nt:require structured-purpose-captured
                (nt:equal (py-call (builtins.getattr $coding "purpose")) "coding")))
          ($_ (nt:require structured-kind-captured
                (nt:equal (py-call (builtins.getattr $coding "kind")) "structured")))
          ((PreparedProviderCallV1 $structured $structured-transport) (owner-test:bind $coding $coding-text "fixture-model"))
          ($_ (nt:require structured-facts-owned
                (nt:equal ((call:fact $structured "kind" "") (call:fact $structured "purpose" "") (call:fact $structured "prompt_bytes" -1))
                          ("structured" "coding" 0))))
          ; The display is native; commentary is the claim's only host effect.
          ($_ (nt:require unobserved-call-has-no-display (not (display:configured?))))
          ($_ (nt:require unobserved-call-has-no-claim-effect
                (nt:equal (call:fact $structured "commentary" true) false)))
          ($_ (call:release $structured))
          ($_ (nt:require unknown-purpose-refused
                (case (catch (py-call (native_llm_host.native_call_seed "fixture-model" 42 "structured" "other")))
                  (((Error $a $b) True) ($other False))))))
     (println! NATIVE_OWNER_CONTRACTS_OK)))
