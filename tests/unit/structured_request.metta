; Unit tests of the native structured request preparation in
; src/provider_request_cetta.metta; no Python. Expected texts are what the
; retired Python structured_plan produced for the same requests.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/provider_request_cetta.metta)

(= (sr:full) (preq:parse "{\"model\": \"session\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S LAST_SKILL_USE_RESULTS: r\"}, {\"role\": \"developer\", \"content\": \"D\"}, {\"role\": \"user\", \"content\": \"U\"}], \"cache_messages\": true, \"cache_ttl\": \"1h\", \"extra_body\": {\"enable_thinking\": true, \"reasoning\": {\"effort\": \"low\"}, \"foo\": 1}, \"tools\": []}"))
(= (sr:prepared $protocol $model $request)
   (case (preq:prepare-structured $protocol $model $request)
     (((PreparedProviderStructuredV1 $value $tokens) (Prepared (preq:render $value) $tokens))
      ((ProviderPreparationRefusedV1 $why) (Refused $why)))))
(= (sr:refused $protocol $request)
   (case (preq:prepare-structured $protocol "m" (preq:parse $request))
     (((ProviderPreparationRefusedV1 $why) $why) ($other accepted))))
!(let* (($_ (nt:require chat-completions-profile
              (nt:equal (sr:prepared "chat-completions" "fixture-model" (sr:full))
                (Prepared "{\"model\": \"fixture-model\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S LAST_SKILL_USE_RESULTS: r\"}, {\"role\": \"developer\", \"content\": \"D\"}, {\"role\": \"user\", \"content\": \"U\"}], \"tools\": [], \"chat_profile\": \"compatible\", \"extensions\": {\"enable_thinking\": true, \"reasoning\": {\"effort\": \"low\"}, \"foo\": 1}}" 64))))
        ($_ (nt:require responses-cache-key-from-system-and-developer
              (nt:equal (sr:prepared "responses" "k3" (sr:full))
                (Prepared "{\"model\": \"k3\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S LAST_SKILL_USE_RESULTS: r\"}, {\"role\": \"developer\", \"content\": \"D\"}, {\"role\": \"user\", \"content\": \"U\"}], \"tools\": [], \"role_policy\": \"preserve\", \"extensions\": {\"enable_thinking\": true, \"reasoning\": {\"effort\": \"low\"}, \"foo\": 1, \"prompt_cache_key\": \"openai:k3:8de0b3c47f112c59745f717a\"}}" 64))))
        ($_ (nt:require anthropic-thinking-and-reasoning
              (nt:equal (sr:prepared "anthropic" "claude-fixture" (sr:full))
                (Prepared "{\"model\": \"claude-fixture\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S LAST_SKILL_USE_RESULTS: r\"}, {\"role\": \"developer\", \"content\": \"D\"}, {\"role\": \"user\", \"content\": \"U\"}], \"cache_messages\": true, \"cache_ttl\": \"1h\", \"tools\": [], \"role_policy\": \"merge-system-developer\", \"thinking\": {\"type\": \"adaptive\"}, \"tool_choice_policy\": \"auto-for-thinking\", \"reasoning\": {\"effort\": \"low\"}, \"extensions\": {\"foo\": 1}}" 64))))
        ; A missing model is appended; the request's own reasoning wins over extra_body's.
        ($own (preq:parse "{\"max_tokens\": 64, \"messages\": [], \"reasoning\": {\"effort\": \"high\"}, \"extensions\": {\"store\": false}, \"extra_body\": {\"reasoning\": {\"effort\": \"low\"}}}"))
        ($_ (nt:require anthropic-own-reasoning-wins
              (nt:equal (sr:prepared "anthropic" "claude-fixture" $own)
                (Prepared "{\"max_tokens\": 64, \"messages\": [], \"reasoning\": {\"effort\": \"high\"}, \"model\": \"claude-fixture\", \"role_policy\": \"merge-system-developer\", \"extensions\": {\"store\": false}}" 64))))
        ($_ (nt:require extra-reasoning-is-an-extension-elsewhere
              (nt:equal (sr:prepared "responses" "o1" $own)
                (Prepared "{\"max_tokens\": 64, \"messages\": [], \"reasoning\": {\"effort\": \"high\"}, \"model\": \"o1\", \"role_policy\": \"preserve\", \"extensions\": {\"store\": false, \"reasoning\": {\"effort\": \"low\"}}}" 64))))
        ($kept (preq:parse "{\"model\": \"session\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S\"}], \"extensions\": {\"prompt_cache_key\": \"mine\"}}"))
        ($_ (nt:require own-cache-key-kept
              (nt:equal (sr:prepared "responses" "k3" $kept)
                (Prepared "{\"model\": \"k3\", \"max_tokens\": 64, \"messages\": [{\"role\": \"system\", \"content\": \"S\"}], \"role_policy\": \"preserve\", \"extensions\": {\"prompt_cache_key\": \"mine\"}}" 64)))))
   (println! UNIT_STRUCTURED_REQUESTS_OK))
!(let* (($_ (nt:require disabling-thinking-refused
              (nt:equal (sr:refused "anthropic" "{\"max_tokens\": 1, \"extra_body\": {\"enable_thinking\": false}}") disabling-thinking-needs-capability)))
        ($_ (nt:require duplicate-extension-refused
              (nt:equal (sr:refused "responses" "{\"max_tokens\": 1, \"messages\": [], \"extensions\": {\"a\": 1}, \"extra_body\": {\"a\": 2}}") duplicate-native-extension)))
        ($_ (nt:require extra-body-object
              (nt:equal (sr:refused "chat-completions" "{\"max_tokens\": 1, \"extra_body\": [1]}") invalid-extra-body)))
        ($_ (nt:require extensions-object
              (nt:equal (sr:refused "chat-completions" "{\"max_tokens\": 1, \"extensions\": \"x\"}") invalid-extensions)))
        ($_ (nt:require responses-system-text
              (nt:equal (sr:refused "responses" "{\"max_tokens\": 1, \"messages\": [{\"role\": \"system\", \"content\": null}]}") invalid-system-content)))
        ($_ (nt:require responses-messages-array
              (nt:equal (sr:refused "responses" "{\"max_tokens\": 1}") invalid-messages)))
        ($_ (nt:require integer-max-tokens
              (nt:equal (sr:refused "chat-completions" "{\"max_tokens\": \"64\"}") invalid-max-tokens)))
        ($_ (nt:require object-request
              (nt:equal (preq:prepare-structured "chat-completions" "m" (preq:parse "[1]")) (ProviderPreparationRefusedV1 expected-object))))
        ; Synthetic sees extension reasoning fields as body fields.
        ($_ (nt:require synthetic-conflicting-reasoning
              (nt:equal (preq:structured-synthetic (preq:parse "{\"reasoning\": {\"effort\": \"high\"}, \"extensions\": {\"reasoning\": {\"effort\": \"low\"}}}"))
                        (ProviderPreparationRefusedV1 conflicting-synthetic-reasoning))))
        ($_ (nt:require synthetic-unsupported-effort
              (nt:equal (preq:structured-synthetic (preq:parse "{\"extensions\": {\"reasoning_effort\": \"max\"}}"))
                        (ProviderPreparationRefusedV1 unsupported-synthetic-effort))))
        ($valid (preq:parse "{\"max_tokens\": 1, \"extensions\": {\"reasoning_effort\": \"high\", \"foo\": 1}}"))
        ($_ (nt:require synthetic-valid-request-unchanged (nt:equal (preq:structured-synthetic $valid) $valid))))
   (println! UNIT_STRUCTURED_REFUSALS_OK))
