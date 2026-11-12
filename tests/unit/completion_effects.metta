; Unit tests of src/completion_effects_cetta.metta; no Python. The expected
; files are what the retired host observers wrote for the same inputs.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/completion_effects_cetta.metta)

(= (ce:dir) (let $home (nt:required-env "HOME") $home))
(= (ce:usage) (json:parse "{\"input_tokens\": 7, \"cache_read_input_tokens\": 10, \"cache_creation_input_tokens\": 3, \"output_tokens\": 2}"))
!(let* (($dir (ce:dir)) ($usage (ce:usage))
        ($result (json:parse "{\"id\": \"x\", \"choices\": [{\"message\": {\"content\": \"hé\"}, \"finish_reason\": \"stop\"}], \"usage\": {\"input_tokens\": 7, \"cache_read_input_tokens\": 10, \"cache_creation_input_tokens\": 3, \"output_tokens\": 2}, \"provider_stop\": \"end_turn\"}"))
        ($evidence (json:parse "{\"counts\": {\"input_tokens\": 20, \"cached_tokens\": 10, \"output_tokens\": 2}}"))
        ($context (JsonObject ((JsonPair "call_id" "c1") (JsonPair "provider" "anthropic") (JsonPair "model" "claude-fixture") (JsonPair "purpose" "turn"))))
        ($layout (json:parse "{\"stable_chars\": 1, \"blocks\": []}"))
        ($path (path:join $dir "observed/llm-completion.json"))
        ($_ (effects:completion-at $result $layout $context $evidence 1794516124.5 $path))
        ($_ (nt:require completion-observation
              (nt:equal (fs:read-text $path)
                "{\"finish_reason\": \"stop\", \"provider_stop\": \"end_turn\", \"usage\": {\"input_tokens\": 7, \"cache_read_input_tokens\": 10, \"cache_creation_input_tokens\": 3, \"output_tokens\": 2}, \"transport\": \"native\", \"observed_at\": 1794516124.5, \"cache\": {\"status\": \"reported\", \"input_tokens\": 20, \"cached_tokens\": 10, \"fraction\": 0.5}, \"prompt_layout\": {\"stable_chars\": 1, \"blocks\": []}}")))
        ; Known keys of the cache observation keep their place when the counts update them.
        ($_ (nt:require usage-line
              (nt:equal (fs:read-text (path:join $dir "observed/llm-usage.jsonl"))
                "{\"observed_at\": 1794516124.5, \"transport\": \"native\", \"provider_stop\": \"end_turn\", \"output_tokens\": 2, \"status\": \"reported\", \"input_tokens\": 20, \"cached_tokens\": 10, \"fraction\": 0.5, \"call_id\": \"c1\", \"provider\": \"anthropic\", \"model\": \"claude-fixture\", \"purpose\": \"turn\"}\n")))
        ($_ (effects:completion-at $result JsonNull $context $evidence 1794516125.5 $path))
        ($_ (nt:require one-line-per-call (nt:equal (size-atom (str:split "\n" (fs:read-text (path:join $dir "observed/llm-usage.jsonl")))) 3)))
        ($_ (nt:require cache-unknown-when-cached-exceeds-input
              (nt:equal (json:stringify-python (effects:cache-observation (json:parse "{\"prompt_tokens\": 5, \"prompt_tokens_details\": {\"cached_tokens\": 9}}")))
                        "{\"status\": \"unknown\"}")))
        ($_ (nt:require no-path-no-observation (effects:completion-at $result JsonNull $context $evidence 1.0 ""))))
   (println! UNIT_COMPLETION_OBSERVATION_OK))
!(let* (($dir (ce:dir)) ($usage (ce:usage)) ($tally (path:join $dir "tally.json"))
        ($_ (fs:write $tally "{\"today\": \"2026-11-11\", \"in\": 5, \"out\": 6, \"since\": \"2026-11-01\", \"total_in\": 50, \"total_out\": 60}"))
        ($_ (effects:tally-at $usage "2026-11-12" $tally))
        ($_ (nt:require tally-new-day
              (nt:equal (fs:read-text $tally) "{\"today\": \"2026-11-12\", \"in\": 20, \"out\": 2, \"since\": \"2026-11-01\", \"total_in\": 70, \"total_out\": 62}")))
        ($_ (effects:tally-at (json:parse "{\"input_tokens\": 1, \"cache_read_input_tokens\": null, \"output_tokens\": 1}") "2026-11-12" $tally))
        ($_ (nt:require tally-same-day-null-cache-is-zero
              (nt:equal (fs:read-text $tally) "{\"today\": \"2026-11-12\", \"in\": 21, \"out\": 3, \"since\": \"2026-11-01\", \"total_in\": 71, \"total_out\": 63}")))
        ($_ (effects:tally-at (json:parse "{\"input_tokens\": null, \"output_tokens\": 1}") "2026-11-12" $tally))
        ($_ (nt:require tally-refuses-a-null-input
              (nt:equal (fs:read-text $tally) "{\"today\": \"2026-11-12\", \"in\": 21, \"out\": 3, \"since\": \"2026-11-01\", \"total_in\": 71, \"total_out\": 63}")))
        ($cal (path:join $dir "state/context-token-calibration.json"))
        ($_ (fs:make-directories (path:join $dir "state") 448))
        ($_ (fs:write $cal "{\"m0\": {\"bytes_per_token\": 2.0, \"input_tokens\": 9}, \"m1\": {\"bytes_per_token\": 1.0, \"input_tokens\": 1}}"))
        ($_ (effects:calibrate-at 1000 $usage "m1" $cal))
        ($_ (nt:require calibration-updates-the-captured-model-in-place
              (nt:equal (fs:read-text $cal) "{\"m0\": {\"bytes_per_token\": 2.0, \"input_tokens\": 9}, \"m1\": {\"bytes_per_token\": 50.0, \"input_tokens\": 20}}")))
        ($_ (effects:calibrate-at 1000 (json:parse "{\"prompt_tokens\": 0}") "m2" $cal))
        ($_ (nt:require calibration-needs-a-positive-count
              (nt:equal (fs:read-text $cal) "{\"m0\": {\"bytes_per_token\": 2.0, \"input_tokens\": 9}, \"m1\": {\"bytes_per_token\": 50.0, \"input_tokens\": 20}}"))))
   (println! UNIT_COMPLETION_EFFECTS_OK))
