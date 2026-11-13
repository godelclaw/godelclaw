; Encoder growth workloads (responses_encoder.metta, anthropic_encoder.metta):
; one structured request of n messages, encoded by the engine's codec for one
; protocol and its wire rendered once (preq:encode, preq:wire). A coding
; transcript grows with its session, so an encoder must stay linear in the
; message count. These checks run on the SDK engine, whose library the agents
; load; the core test driver may carry another. An encoding must answer, or
; the child fails.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/provider_request_cetta.metta)
(= (eg:messages $n $out)
   (if (== $n 0) $out
     (eg:messages (- $n 1)
       (cons-atom (JsonObject ((JsonPair "role" (JsonString (if (== (% $n 3) 0) "assistant" "user")))
                               (JsonPair "content" (JsonString (format-args "message {} with some ordinary transcript text, Δ ✓" ($n))))))
                  $out))))
(= (eg:request $n)
   (JsonObject ((JsonPair "model" (JsonString "session")) (JsonPair "max_tokens" (JsonNumber "32768"))
                (JsonPair "messages" (JsonArray (cons-atom (JsonObject ((JsonPair "role" (JsonString "system")) (JsonPair "content" (JsonString "S"))))
                                                           (eg:messages $n ()))))
                (JsonPair "cache_messages" (JsonBool True)))))
(= (eg:encode $protocol $provider $request)
   (case (preq:prepare-structured $protocol "growth-model" $request)
     (((PreparedProviderStructuredV1 $valid $tokens)
       (case (preq:encode $protocol "messages" $valid)
         (((llm:v1:ok (llm:v1:wire $path $body))
           (case (preq:wire $body $provider $protocol "structured" False False ProviderCacheNoneV1)
             (((PreparedProviderWireV1 $wire $active $model $layout) (str:byte-length $wire))
              ($refused 0))))
          ($failed 0))))
      ($refused 0))))
; The request is built before measuring starts: only the encoding is measured.
(= (eg:measure $protocol $provider)
   (let $request (eg:request (growth:size))
     (growth:measure (eg:encoded $protocol $provider $request))))
(= (eg:encoded $protocol $provider $request)
   (let* (($bytes (eg:encode $protocol $provider $request))
          ($_ (nt:require (EncoderAnswered $protocol) (> $bytes 0))))
     $bytes))
