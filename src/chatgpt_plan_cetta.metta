; SPDX-License-Identifier: MIT
; Explicit request profile for the published ChatGPT-plan Responses route.
; Authentication is separate. No tokens or account state enter this codec.
!(import! &self llm)

; The ordinary encoder supplies max_output_tokens; the plan profile explicitly
; omits it. Other incompatible options fail visibly instead of losing meaning.
(= (chatgpt:fields $pairs)
   (if (== $pairs ()) True
     (case (car-atom $pairs)
       (((JsonPair $name $value)
         (if (llm:v1:member? $name
               ("background" "conversation" "max_tool_calls" "metadata" "moderation"
                "multi_agent" "prompt" "prompt_cache_retention" "safety_identifier"
                "temperature" "top_logprobs" "top_p" "truncation" "user" "previous_response_id"))
           (llm:v1:failure (unsupported-chatgpt-field $name))
           (let $tail (cdr-atom $pairs) (chatgpt:fields $tail))))))))
(= (chatgpt:input $value)
   (case $value
     (((JsonString $text)
       (JsonArray ((JsonObject ((JsonPair "role" (JsonString "user"))
                                (JsonPair "content" (JsonString $text)))))))
      ((JsonArray $items) (JsonArray $items))
      ($bad (llm:v1:failure expected-input-array)))))
(= (chatgpt:profile $body)
   (case $body
     (((llm:v1:failure $code) $body)
      ((JsonObject $pairs)
       (let* (($rest (llm:v1:without $pairs "max_output_tokens"))
              ($fields (chatgpt:fields $rest))
              ($input (chatgpt:input (llm:v1:get $body "input")))
              ($base (llm:v1:with (JsonObject $rest) "input" $input))
              ($streaming (llm:v1:with $base "stream" (JsonBool True)))
              ($result (llm:v1:with $streaming "store" (JsonBool False))))
         (llm:v1:validated ($fields $input) $result)))
      ($bad (llm:v1:failure expected-object)))))
(= (chatgpt:wire $body)
   (let* (($parsed (llm:v1:parse $body)) ($wire (chatgpt:profile $parsed)))
     (llm:v1:render $wire)))
