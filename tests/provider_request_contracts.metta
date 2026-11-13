; Native request contracts; the same cases run in both extended dialects.
!(import! &self ../src/provider_request_cetta.metta)
!(import! &self ./native_test_support.metta)

(= (request-test:wire $provider $protocol $prompt $visible)
   (case (preq:prepare-command $protocol "fixture" 42 "low" $prompt JsonNull "1h")
     (((PreparedProviderCommandV1 $request $encoding $cache $profile)
       (case (preq:encode $protocol $encoding $request)
         (((llm:v1:ok (llm:v1:wire $path $body))
           (preq:wire $body $provider $protocol "command" False $visible $cache))
          ($bad $bad)))) ($bad $bad))))
; The wire body of a command prepared with the given reasoning effort, checked
; and encoded as a call prepares it (src/provider_call_cetta.metta).
(= (request-test:effort-body $provider $protocol $effort)
   (case (preq:prepare-command $protocol "fixture" 42 $effort "S_cache_boundary_U" JsonNull "1h")
     (((PreparedProviderCommandV1 $request $encoding $cache $profile)
       (case (if (nt:equal $provider "synthetic") (preq:synthetic $request) $request)
         (((ProviderPreparationRefusedV1 $why) (ProviderPreparationRefusedV1 $why))
          ($valid (case (preq:encode $protocol $encoding $request)
            (((llm:v1:ok (llm:v1:wire $path $body))
              (case (preq:wire $body $provider $protocol "command" False False $cache)
                (((PreparedProviderWireV1 $wire $active $model $layout) (preq:parse $wire)) ($bad $bad))))
             ($bad $bad))))))) ($bad $bad))))
(= (request-test:body $provider $protocol $prompt $visible)
   (case (request-test:wire $provider $protocol $prompt $visible)
     (((PreparedProviderWireV1 $wire $active $model $layout) (preq:parse $wire)) ($bad $bad))))
(= (request-test:split)
   (let* (
     ($_ (nt:require missing-region (nt:equal (preq:split "input") (ProviderPromptPartsV1 () "input"))))
     ($_ (nt:require blank-tail-pop-before-filter
         (nt:equal (preq:split "A_cache_boundary_ _cache_boundary_ ") (ProviderPromptPartsV1 ("A") " "))))
     ($_ (nt:require delimiter-limit
         (nt:equal (preq:split "a_cache_boundary_b_cache_boundary_c_cache_boundary_d_cache_boundary_e")
             (ProviderPromptPartsV1 ("a" "b" "c") "d_cache_boundary_e"))))
     ($_ (nt:require omega-empty
         (nt:equal (preq:split " policy :-:-:-: ") (ProviderPromptPartsV1 ("policy") "EMPTY / NO NEW USER INPUT."))))
     ($_ (nt:require unicode-whitespace
         (nt:equal (preq:split "A_cache_boundary_　") (ProviderPromptPartsV1 () "A"))))
     ($_ (nt:require chunk-endpoints
         (nt:equal (preq:chunks "\nA\n_cache_chunk_\nB\n") ("A" "B"))))
     ($_ (nt:require missing-key-nonempty (nt:equal (preq:field (preq:parse "{\"present\":1}") "missing") JsonNull)))) 7))
(= (request-test:hash)
   (let* (
     ($_ (nt:require framed-prefix-not-concatenation
         (not (nt:equal (str:sha256-chain "" ("ab" "c")) (str:sha256-chain "" ("a" "bc"))))))
     ($_ (nt:require empty-and-duplicate-occurrences
       (nt:equal (str:sha256-chain "" ("a" "a" ""))
         (Sha256ChainV1 "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
           ((1 "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
               "3b196fd4907bedf51c3090e9835f2f7cb61e7ee1b2299ea3b8fed9b4183a822a")
            (1 "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
               "07b9822b722b7dc299d0a6c12f4e7239b2431ccd0146cdafa5715318a24e0e7a")
            (0 "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
               "7d350ea3a6b72d0055ffc411c462ee74054044a518849e033d1c39acdf87015b"))))))
     ($_ (nt:require unicode-strip (nt:equal (str:strip-python "　λ🌿　") "λ🌿")))
     ($_ (nt:require character-set-not-prefix (nt:equal (str:strip-python "λ🌿inside🌿λ" "🌿λ") "inside")))
     ($_ (nt:require empty-strip-set (nt:equal (str:strip-python " λ " "") " λ ")))) 5))
(= (request-test:frame)
   (let* (
     ($_ (nt:require malformed-json-is-plain (nt:equal (preq:frame "{broken") LegacyPrompt)))
     ($_ (nt:require invalid-role-context
         (nt:equal (preq:frame "{\"kind\":\"role-context-v1\",\"messages\":[]}")
             (ProviderPreparationRefusedV1 invalid-role-context))))
     ($_ (nt:require boolean-is-not-point
       (nt:equal (preq:frame "{\"kind\":\"role-context-v1\",\"messages\":[{\"role\":\"user\",\"content\":\"data\"}],\"cache_points\":[true]}")
           (ProviderPreparationRefusedV1 invalid-role-context))))
     ($_ (nt:require duplicate-json-last-value-first-position
         (nt:equal (preq:render (preq:parse "{\"a\":1,\"b\":2,\"a\":3}")) "{\"a\": 3, \"b\": 2}")))
     ($_ (nt:require unsupported-effort
         (nt:equal (preq:synthetic (preq:parse "{\"reasoning\":{\"effort\":\"xhigh\"}}"))
             (ProviderPreparationRefusedV1 unsupported-synthetic-reasoning))))) 5))
(= (request-test:wire-contracts)
   (let* (
     ($chat (request-test:body "synthetic" "chat-completions" "S_cache_boundary_U" False))
     ($_ (nt:require stable-and-user-order
         (nt:equal (preq:field $chat "messages")
             (preq:parse "[{\"role\":\"system\",\"content\":\"S\"},{\"role\":\"user\",\"content\":\"U\"}]"))))
     ($_ (nt:require synthetic-reasoning (nt:equal (preq:field $chat "reasoning_effort") (JsonString "low"))))
     ; The loop's policy names its effort as a symbol; the wire carries text.
     ($_ (nt:require symbol-effort-is-text-on-chat-completions
         (nt:equal (preq:field (request-test:effort-body "synthetic" "chat-completions" high) "reasoning_effort") (JsonString "high"))))
     ($_ (nt:require symbol-effort-is-text-on-anthropic
         (nt:equal (preq:field (preq:field (request-test:effort-body "anthropic" "anthropic" high) "output_config") "effort") (JsonString "high"))))
     ($_ (nt:require symbol-effort-is-text-on-responses
         (nt:equal (preq:field (preq:field (request-test:effort-body "kimi" "responses" high) "reasoning") "effort") (JsonString "high"))))
     ($_ (nt:require text-effort-unchanged
         (nt:equal (preq:field (preq:field (request-test:effort-body "anthropic" "anthropic" "max") "output_config") "effort") (JsonString "max"))))
     ($_ (nt:require default-symbol-effort-sends-none
         (not (preq:has (request-test:effort-body "anthropic" "anthropic" default) "output_config"))))
     ($responses (request-test:body "kimi" "responses" "S_cache_boundary_U" True))
     ($_ (nt:require command-reasoning-is-not-replay (not (preq:has $responses "include"))))
     ($_ (nt:require visible-response-reasoning
         (nt:equal (preq:field (preq:field $responses "reasoning") "summary") (JsonString "auto"))))
     ($anthropic (request-test:body "anthropic" "anthropic" "S_cache_boundary_U" False))
     ($_ (nt:require anthropic-cache-ttl
         (nt:equal (preq:field (preq:field (preq:head (preq:items $anthropic "system")) "cache_control") "ttl") (JsonString "1h"))))
     ($inert (request-test:body "synthetic" "chat-completions" "(Error data value)" False))
     ($_ (nt:require syntax-remains-data
         (nt:equal (preq:field (preq:head (preq:items $inert "messages")) "content") (JsonString "(Error data value)"))))
     ($role "{\"kind\":\"role-context-v1\",\"messages\":[{\"role\":\"system\",\"content\":\"policy\"},{\"role\":\"assistant\",\"content\":\"(Error data value)\"},{\"role\":\"user\",\"content\":\"λ\"}],\"cache_points\":[-1,0,100]}")
     ($role-body (request-test:body "anthropic" "anthropic" $role False))
     ($_ (nt:require negative-cache-point-does-not-erase-request
         (nt:equal (preq:size (preq:items $role-body "messages")) 2)))
     ($_ (nt:require role-content-keeps-inert-data
         (nt:equal (preq:field (preq:head (preq:items (preq:head (preq:items $role-body "messages")) "content")) "text")
                   (JsonString "(Error data value)"))))) 8))
; Structured (coding and iter) requests: preparation, encoding and wire.
(= (request-test:structured $provider $protocol $request-text)
   (case (preq:prepare-structured $protocol "fixture" (preq:parse $request-text))
     (((PreparedProviderStructuredV1 $request $tokens)
       (case (preq:encode $protocol "messages" $request)
         (((llm:v1:ok (llm:v1:wire $path $body))
           (case (preq:wire $body $provider $protocol "structured" False False ProviderCacheNoneV1)
             (((PreparedProviderWireV1 $wire $active $model $layout) (preq:parse $wire)) ($bad $bad))))
          ($bad $bad))))
      ($refused $refused))))
(= (request-test:structured-contracts)
   (let* (
     ($request "{\"model\":\"session\",\"max_tokens\":64,\"messages\":[{\"role\":\"system\",\"content\":\"S\"},{\"role\":\"user\",\"content\":\"U\"}],\"cache_messages\":true,\"extra_body\":{\"enable_thinking\":true}}")
     ($chat (request-test:structured "synthetic" "chat-completions" $request))
     ($_ (nt:require structured-model-named (nt:equal (preq:field $chat "model") (JsonString "fixture"))))
     ($_ (nt:require structured-extension-on-wire (nt:equal (preq:field $chat "enable_thinking") (JsonBool True))))
     ($responses (request-test:structured "kimi" "responses" $request))
     ($_ (nt:require structured-keeps-reasoning-replay (preq:has $responses "include")))
     ($_ (nt:require structured-cache-key
         (nt:equal (preq:field $responses "prompt_cache_key") (JsonString "openai:fixture:8de0b3c47f112c59745f717a"))))
     ($anthropic (request-test:structured "anthropic" "anthropic" $request))
     ($_ (nt:require structured-thinking
         (nt:equal (preq:field (preq:field $anthropic "thinking") "type") (JsonString "adaptive"))))
     ($_ (nt:require structured-cached-messages
         (preq:has (preq:head (preq:items (preq:head (preq:items $anthropic "messages")) "content")) "cache_control")))
     ($_ (nt:require structured-disabled-thinking-refused
         (nt:equal (request-test:structured "anthropic" "anthropic" "{\"max_tokens\":1,\"extra_body\":{\"enable_thinking\":false}}")
                   (ProviderPreparationRefusedV1 disabling-thinking-needs-capability))))) 7))
; The runtime encodes on the codec's value route only. The codec's text
; entries are its independent oracle here: the request rendered, encoded as
; text and its body parsed back must give the same wire, same path, and
; preq:wire must render the same bytes with the same observation.
(= (request-test:text-oracle $protocol $encoding $request)
   (let* (($text (preq:render $request))
          ($encoded (if (== $encoding "default") (llm:v1:encode-command $protocol $text)
                      (llm:v1:encode $protocol $text))))
     (case $encoded
       (((llm:v1:ok (llm:v1:encoded $path $body))
         (let $value (preq:parse $body) (llm:v1:ok (llm:v1:wire $path $value))))
        ($failed $failed)))))
(= (request-test:routes-agree $provider $protocol $kind $request $encoding $cache $visible)
   (let* (($value (preq:encode $protocol $encoding $request))
          ($text (request-test:text-oracle $protocol $encoding $request)))
     (case $value
       (((llm:v1:ok (llm:v1:wire $path $body))
         (case $text
           (((llm:v1:ok (llm:v1:wire $text-path $text-body))
             (and (nt:equal $path $text-path)
                  (nt:equal (preq:wire $body $provider $protocol $kind False $visible $cache)
                            (preq:wire $text-body $provider $protocol $kind False $visible $cache))))
            ($other False))))
        ((llm:v1:error $code) (nt:equal $value $text))
        ($other False)))))
(= (request-test:command-routes $provider $protocol $prompt $visible)
   (case (preq:prepare-command $protocol "fixture" 42 "low" $prompt JsonNull "1h")
     (((PreparedProviderCommandV1 $request $encoding $cache $profile)
       (request-test:routes-agree $provider $protocol "command" $request $encoding $cache $visible))
      ($bad False))))
(= (request-test:structured-routes $provider $protocol $request-text)
   (case (preq:prepare-structured $protocol "fixture" (preq:parse $request-text))
     (((PreparedProviderStructuredV1 $request $tokens)
       (request-test:routes-agree $provider $protocol "structured" $request "messages" ProviderCacheNoneV1 False))
      ($bad False))))
; The engine has the value entries: a missing entry leaves its call unevaluated.
(= (request-test:route)
   (case (preq:value-wire "chat-completions" "messages" (preq:parse "{\"model\":\"m\",\"max_tokens\":1,\"messages\":[]}"))
     (((llm:v1:ok $wire) value) ((llm:v1:error $code) value) ($unevaluated missing))))
(= (request-test:route-contracts)
   (let* (
     ($role "{\"kind\":\"role-context-v1\",\"messages\":[{\"role\":\"system\",\"content\":\"policy \\\"q\\\"\\n\"},{\"role\":\"assistant\",\"content\":\"(Error data value)\"},{\"role\":\"user\",\"content\":\"λ\"}],\"cache_points\":[-1,0,100]}")
     ($structured "{\"model\":\"session\",\"max_tokens\":64,\"messages\":[{\"role\":\"system\",\"content\":\"S\"},{\"role\":\"user\",\"content\":\"U\"}],\"cache_messages\":true,\"extra_body\":{\"enable_thinking\":true}}")
     ($_ (nt:require value-codec-route (nt:equal (request-test:route) value)))
     ($_ (nt:require route-command-default (request-test:command-routes "openai" "chat-completions" "S_cache_boundary_U" False)))
     ($_ (nt:require route-command-synthetic-visible (request-test:command-routes "synthetic" "chat-completions" "(Error data value)" True)))
     ($_ (nt:require route-command-responses (request-test:command-routes "kimi" "responses" "S_cache_boundary_U" True)))
     ($_ (nt:require route-command-anthropic-regions (request-test:command-routes "anthropic" "anthropic" "S_cache_boundary_T_cache_boundary_U" False)))
     ($_ (nt:require route-command-anthropic-roles (request-test:command-routes "anthropic" "anthropic" $role False)))
     ($_ (nt:require route-command-responses-roles (request-test:command-routes "openai" "responses" $role True)))
     ($_ (nt:require route-structured-chat (request-test:structured-routes "synthetic" "chat-completions" $structured)))
     ($_ (nt:require route-structured-responses (request-test:structured-routes "kimi" "responses" $structured)))
     ($_ (nt:require route-structured-anthropic (request-test:structured-routes "anthropic" "anthropic" $structured)))
     ; The subscription route's own request: tools, reasoning and its cache key.
     ($chatgpt "{\"model\":\"gpt-fixture\",\"max_tokens\":42,\"messages\":[{\"role\":\"system\",\"content\":\"policy\"},{\"role\":\"system\",\"content\":\"memory\"},{\"role\":\"user\",\"content\":\"λ input\"}],\"role_policy\":\"preserve\",\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"metta\",\"description\":\"Run one complete GödelClaw MeTTa command batch.\",\"parameters\":{\"type\":\"object\",\"properties\":{\"program\":{\"type\":\"string\",\"description\":\"Complete ((skillName args) ...) S-expression.\"}},\"required\":[\"program\"],\"additionalProperties\":false}}}],\"tool_choice\":\"auto\",\"reasoning\":{\"effort\":\"low\"},\"extensions\":{\"prompt_cache_key\":\"0123abcd\"}}")
     ($_ (nt:require route-chatgpt-command
         (request-test:routes-agree "chatgpt" "responses" "command" (preq:parse $chatgpt) "messages" ProviderCacheNoneV1 True)))
     ($_ (nt:require route-codec-refusal
         (request-test:structured-routes "openai" "responses" "{\"model\":\"m\",\"max_tokens\":5,\"messages\":[{\"role\":\"narrator\",\"content\":\"x\"}]}")))) 12))
(= (request-test:run)
   (let* (($a (request-test:split)) ($b (request-test:hash)) ($c (request-test:frame))
          ($d (request-test:wire-contracts)) ($e (request-test:structured-contracts))
          ($f (request-test:route-contracts)))
     (+ (+ (+ $a $b) (+ $c $d)) (+ $e $f))))
