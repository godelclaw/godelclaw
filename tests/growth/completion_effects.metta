; Growth workload: one completion of an n-part reply through its native
; effects: the reply is parsed once, its observation and usage line written,
; the captured model calibrated and its Anthropic usage tallied. Only the
; parse depends on the reply; every written file stays small.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/completion_effects_cetta.metta)
(= (ceg:parts $n $out) (if (== $n 0) $out (ceg:parts (- $n 1) (cons-atom "a reply part of ordinary length, Δ ✓. " $out))))
(= (ceg:run $n)
   (let* (($dir (growth:directory))
          ($content (str:join "" (ceg:parts $n ())))
          ($message (JsonObject ((JsonPair "role" "assistant") (JsonPair "content" $content))))
          ($choice (JsonObject ((JsonPair "message" $message) (JsonPair "finish_reason" "stop"))))
          ($usage (JsonObject ((JsonPair "input_tokens" 7) (JsonPair "cache_read_input_tokens" 10) (JsonPair "output_tokens" 2))))
          ($legacy (json:stringify (JsonObject ((JsonPair "choices" (JsonArray ($choice))) (JsonPair "usage" $usage)))))
          ($result (json:parse $legacy))
          ($counts (JsonObject ((JsonPair "input_tokens" 17) (JsonPair "output_tokens" 2))))
          ($evidence (JsonObject ((JsonPair "counts" $counts))))
          ($context (JsonObject ((JsonPair "call_id" "g") (JsonPair "provider" "anthropic") (JsonPair "model" "m") (JsonPair "purpose" "turn"))))
          ($_ (effects:completion-at $result JsonNull $context $evidence 1.5 (path:join $dir "llm-completion.json")))
          ($_ (effects:calibrate-at 1000 $usage "m" (path:join $dir "calibration.json")))
          ($_ (effects:tally-at $usage "2026-11-12" (path:join $dir "tally.json"))))
     (str:byte-length $legacy)))
!(growth:measure (ceg:run (growth:size)))
