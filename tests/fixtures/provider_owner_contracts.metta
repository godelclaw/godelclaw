; Run by the native driver against the actual SDK owner implementation.
(: owner-test:seed (-> %Undefined%))
(= (owner-test:seed)
   (let ($plan $facts) (py-call (native_llm_host.native_command_seed "fixture-model" 42)) $plan))
(= (owner-test:metadata $model)
   (let $quoted (preq:repr $model) (preq:render (preq:parse
     (format-args "{\"fingerprint\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"model\":{},\"encoding\":\"default\",\"prompt_layout\":{},\"prompt_bytes\":5,\"feedback_visible\":false,\"footer_visible\":true,\"stream\":false,\"cache_layout\":null}"
       ($quoted "{}"))))))
(= (owner-test:run)
   (let* (($seal "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
          ($plan (owner-test:seed)) ($metadata (owner-test:metadata "fixture-model"))
          ($bound (py-call (native_llm_host.bind_native_command $plan $seal $metadata)))
          ($_ (nt:require native-owner-bound (host:flag $bound)))
          ($_ (nt:require matching-native-description
                (host:flag (py-call (native_llm_host.native_prepared_matches $plan $seal)))))
          ($_ (nt:require wrong-description-refused
                (not (host:flag (py-call (native_llm_host.native_prepared_matches $plan "other"))))))
          ($_ (nt:require rebind-refused
                (not (host:flag (py-call (native_llm_host.bind_native_command $plan $seal "{broken"))))))
          ($_ (nt:require live-owner-not-revoked (nt:equal (py-call (native_llm_host.field $plan "ready")) "true")))
          ($_ (nt:require request-not-retained (not (host:flag (py-call (builtins.hasattr $plan "request"))))))
          ($_ (nt:require prompt-not-retained (not (host:flag (py-call (builtins.hasattr $plan "prompt_text"))))))
          ($other (owner-test:seed)) ($wrong-model (owner-test:metadata "other"))
          ($_ (nt:require wrong-model-never-bound
                (not (host:flag (py-call (native_llm_host.bind_native_command $other $seal $wrong-model))))))
          ($_ (nt:require wrong-model-remains-unready
                (nt:equal (py-call (native_llm_host.field $other "ready")) "false")))
          ($missing (owner-test:seed))
          ($_ (nt:require incomplete-metadata-never-bound
                (not (host:flag (py-call (native_llm_host.bind_native_command $missing $seal "{}"))))))
          ($_ (nt:require no-partial-description
                (not (host:flag (py-call (builtins.hasattr $missing "prepared_seal")))))))
     (println! NATIVE_OWNER_CONTRACTS_OK)))
