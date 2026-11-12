; Run by the native driver against the actual SDK owner implementation.
(: owner-test:seed (-> %Undefined%))
(= (owner-test:seed)
   (let ($plan $facts) (py-call (native_llm_host.native_call_seed "fixture-model" 42 "command" "turn")) $plan))
(= (owner-test:structured-seed $purpose)
   (let ($plan $facts) (py-call (native_llm_host.native_call_seed "fixture-model" 42 "structured" $purpose)) $plan))
; A structured request has no prompt to measure.
(= (owner-test:structured-metadata $layout $bytes)
   (preq:render (preq:parse
     (format-args "{\"fingerprint\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"model\":\"fixture-model\",\"encoding\":\"default\",\"prompt_layout\":{},\"prompt_bytes\":{},\"feedback_visible\":false,\"footer_visible\":false,\"stream\":false,\"cache_layout\":null}"
       ($layout $bytes)))))
(= (owner-test:metadata $model)
   (let $quoted (preq:repr $model) (preq:render (preq:parse
     (format-args "{\"fingerprint\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"model\":{},\"encoding\":\"default\",\"prompt_layout\":{},\"prompt_bytes\":5,\"feedback_visible\":false,\"footer_visible\":true,\"stream\":false,\"cache_layout\":null}"
       ($quoted "{}"))))))
(= (owner-test:run)
   (let* (($seal "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
          ($plan (owner-test:seed)) ($metadata (owner-test:metadata "fixture-model"))
          ($bound (py-call (native_llm_host.bind_native_call $plan $seal $metadata)))
          ($_ (nt:require native-owner-bound (host:flag $bound)))
          ($owner (call:owner $plan (JsonObject ((JsonPair "correlation" "owner-contract"))) $seal))
          ($_ (nt:require matching-native-description (call:seal-matches? $owner $seal)))
          ($_ (nt:require wrong-description-refused (not (call:seal-matches? $owner "other"))))
          ($_ (call:release $owner))
          ($_ (nt:require rebind-refused
                (not (host:flag (py-call (native_llm_host.bind_native_call $plan $seal "{broken"))))))
          ($_ (nt:require live-owner-not-revoked (nt:equal (py-call (builtins.getattr $plan "ready")) "true")))
          ($_ (nt:require request-not-retained (not (host:flag (py-call (builtins.hasattr $plan "request"))))))
          ($_ (nt:require prompt-not-retained (not (host:flag (py-call (builtins.hasattr $plan "prompt_text"))))))
          ($other (owner-test:seed)) ($wrong-model (owner-test:metadata "other"))
          ($_ (nt:require wrong-model-never-bound
                (not (host:flag (py-call (native_llm_host.bind_native_call $other $seal $wrong-model))))))
          ($_ (nt:require wrong-model-remains-unready
                (nt:equal (py-call (builtins.getattr $other "ready")) "false")))
          ($missing (owner-test:seed))
          ($_ (nt:require incomplete-metadata-never-bound
                (not (host:flag (py-call (native_llm_host.bind_native_call $missing $seal "{}"))))))
          ($_ (nt:require no-partial-description
                (not (host:flag (py-call (builtins.hasattr $missing "prepared_seal"))))))
          ($coding (owner-test:structured-seed "coding"))
          ($_ (nt:require structured-purpose-captured
                (nt:equal (py-call (builtins.getattr $coding "purpose")) "coding")))
          ($_ (nt:require structured-kind-captured
                (nt:equal (py-call (builtins.getattr $coding "kind")) "structured")))
          ($_ (nt:require structured-refuses-a-prompt-layout
                (not (host:flag (py-call (native_llm_host.bind_native_call $coding $seal (owner-test:structured-metadata "{}" "0")))))))
          ($structured (owner-test:structured-seed "turn"))
          ($_ (nt:require structured-refuses-a-prompt-size
                (not (host:flag (py-call (native_llm_host.bind_native_call $structured $seal (owner-test:structured-metadata "null" "5")))))))
          ($fresh (owner-test:structured-seed "compaction"))
          ($_ (nt:require structured-metadata-bound
                (host:flag (py-call (native_llm_host.bind_native_call $fresh $seal (owner-test:structured-metadata "null" "0"))))))
          ($command (owner-test:seed))
          ($_ (nt:require command-refuses-structured-metadata
                (not (host:flag (py-call (native_llm_host.bind_native_call $command $seal (owner-test:structured-metadata "null" "0")))))))
          ($_ (nt:require unknown-purpose-refused
                (case (catch (py-call (native_llm_host.native_call_seed "fixture-model" 42 "structured" "other")))
                  (((Error $a $b) True) ($other False))))))
     (println! NATIVE_OWNER_CONTRACTS_OK)))
