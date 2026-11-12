; Growth workload: one command prompt of n lines in three cache regions and
; a user part, through native preparation for each protocol: the prompt split
; once, the request encoded on the runtime's route, the wire adjusted and
; rendered once, and the request fingerprinted. A prompt grows with the
; agent's context, so each step must stay linear in its bytes.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/provider_request_cetta.metta)
(= (cg:lines $n $label $out)
   (if (== $n 0) $out
     (cg:lines (- $n 1) $label
       (cons-atom (format-args "{} line {}: ordinary context text, \"quoted\", Δ ✓\n" ($label $n)) $out))))
(= (cg:prompt $n)
   (str:join "_cache_boundary_"
     ((str:join "" (cg:lines $n "policy" ())) (str:join "" (cg:lines $n "memory" ()))
      "recent" (str:join "" (cg:lines $n "input" ())))))
(= (cg:prepare $protocol $provider $prompt)
   (case (preq:prepare-command $protocol "growth-model" 4096 "low" $prompt JsonNull "1h")
     (((PreparedProviderCommandV1 $request $encoding $cache $profile)
       (let* (((llm:v1:ok (llm:v1:wire $path $body)) (preq:encode $protocol $encoding $request))
              ((PreparedProviderWireV1 $wire $active $model $layout)
                (preq:wire $body $provider $protocol "command" False False $cache))
              ($fingerprint (str:sha256 (preq:canonical $request))))
         (+ (str:byte-length $wire) (str:byte-length $fingerprint)))))))
(= (cg:run $n)
   (let* (($prompt (cg:prompt $n))
          ($a (cg:prepare "chat-completions" "openai" $prompt))
          ($b (cg:prepare "responses" "kimi" $prompt))
          ($c (cg:prepare "anthropic" "anthropic" $prompt)))
     (+ (+ $a $b) $c)))
!(growth:measure (cg:run (growth:size)))
