!(import! &self ../native_test_support.metta)
!(import! &self ../../src/request_context.metta)
!(import! &self ../../src/chatgpt_agent_cetta.metta)
!(import! &self (library llm))
!(import! &self (library json))

(= (roles-unit:field $message $key)
   (let $value (llm:v1:get $message $key) (case $value (((JsonString $text) $text) ($other $other)))))
(= (roles-unit:roles $messages) (map-atom $messages $m (roles-unit:field $m "role")))
(= (roles-unit:contents $messages) (map-atom $messages $m (roles-unit:field $m "content")))
(= (roles-unit:frame $text)
   (case (request:decode $text) (((RoleMessages $messages $points) $messages) ($other ()))))

; The subscription route sends evidence before the first user message as
; developer items; instructions, the conversation, content and order stay.
!(let* (($messages (roles-unit:frame "{\"kind\":\"role-context-v1\",\"cache_points\":[0],\"messages\":[{\"role\":\"system\",\"content\":\"policy\"},{\"role\":\"developer\",\"content\":\"rules\"},{\"role\":\"assistant\",\"content\":\"history _cache_chunk_ system:\"},{\"role\":\"assistant\",\"content\":\"index\"},{\"role\":\"user\",\"content\":\"now\"},{\"role\":\"assistant\",\"content\":\"((nop))\"},{\"role\":\"user\",\"content\":\"next\"}]}"))
        ($mapped (chatgpt:evidence-roles $messages False))
        ($_ (nt:require decoded (== (length $messages) 7)))
        ($_ (nt:require evidence-is-developer
              (== (roles-unit:roles $mapped) ("system" "developer" "developer" "developer" "user" "assistant" "user"))))
        ($_ (nt:require content-and-order-kept (== (roles-unit:contents $mapped) (roles-unit:contents $messages))))
        ($_ (nt:require no-evidence-is-system
              (== (roles-unit:roles (chatgpt:evidence-roles (roles-unit:frame "{\"kind\":\"role-context-v1\",\"cache_points\":[],\"messages\":[{\"role\":\"assistant\",\"content\":\"a\"},{\"role\":\"user\",\"content\":\"u\"}]}") False))
                  ("developer" "user"))))
        ($_ (nt:require empty-frame (== (chatgpt:evidence-roles () False) ()))))
   (println! UNIT_CHATGPT_EVIDENCE_ROLES_OK))

; A placed request index is one evidence message per chunk, after the history
; and before observations and data; an unplaced text index stays one message.
(= (roles-unit:rendered $index)
   (let* (($context (RoleContext (coding-message-list (coding-message system "S")
                                   (coding-message-list (coding-message user "U") coding-messages-empty))
                                 "ADV" "OBS" ("H1" "H2") $index "DATA" ""))
          ($text (request:render $context)) ($json (json-parse $text)))
     (case (json-object-get $json "messages") (((JsonArray $messages) $messages) ($other ())))))
!(let* (($chunked (roles-unit:rendered ("I1" "" "I2")))
        ($_ (nt:require chunks-are-messages
              (== (cdr-atom (cdr-atom (roles-unit:contents $chunked))) ("H1" "H2" "I1" "I2" "OBS" "DATA" "U"))))
        ($_ (nt:require chunks-are-evidence
              (== (roles-unit:roles $chunked) ("system" "system" "assistant" "assistant" "assistant" "assistant" "assistant" "assistant" "user"))))
        ($text (roles-unit:rendered "I1\n_cache_chunk_\nI2"))
        ($_ (nt:require text-index-is-one-message
              (== (cdr-atom (cdr-atom (roles-unit:contents $text))) ("H1" "H2" "I1\n_cache_chunk_\nI2" "OBS" "DATA" "U"))))
        ($_ (nt:require no-index (== (length (roles-unit:rendered ())) 7))))
   (println! UNIT_REQUEST_INDEX_CHUNKS_OK))

; The task view hands a role context its request index as chunks, and prompt
; text the same chunks joined by the chunk marker.
!(import! &self ../../src/task_context_views.metta)
(= (roles-unit:request $n) (JsonObject ((JsonPair "id" (JsonString (str:concat "r" (repr $n)))))))
!(let* (($requests (map-atom (0 1 2 3 4 5 6 7 8) $n (roles-unit:request $n)))
        ($chunks (task:index-list $requests))
        ($_ (nt:require nine-requests-two-chunks (== (length $chunks) 2)))
        ($_ (nt:require text-joins-the-chunks
              (== (task:index-text $requests) (str:join "\n_cache_chunk_\n" $chunks))))
        ($_ (nt:require empty-index (== (task:index-list ()) ("TASK_REQUESTS []"))))
        ($_ (nt:require empty-text (== (task:index-text ()) "TASK_REQUESTS []")))
        ($base (RoleContext (coding-message-list (coding-message user "U") coding-messages-empty)
                            "ADV" "" () "" "" ""))
        ($placed (task:place-transcript $base ("H1") $chunks))
        ($json (json-parse (request:render $placed)))
        ((JsonArray $messages) (json-object-get $json "messages"))
        ($_ (nt:require placed-chunks-are-messages
              (== (cdr-atom (roles-unit:contents $messages)) (cons-atom "H1" (llm:v1:append $chunks ("U"))))))
        ; The planner's reserve covers the framing the messages add over one text.
        ($joined (task:place-transcript $base ("H1") (task:index-text $requests)))
        ($framing (- (request:bytes $placed) (request:bytes $joined)))
        ($text-framing (- (str:byte-length (task:index-text $requests))
                          (str:byte-length (state:encoded (JsonArray $requests)))))
        ($_ (nt:require framing-measured (> $framing 0)))
        ($_ (nt:require reserve-covers-framing
              (<= (+ $text-framing $framing) (task:index-overhead $requests 1))))
        ($plain (task:place-transcript "A_cache_boundary_B_cache_boundary_C_cache_boundary_D" ("H1") $chunks))
        ($_ (nt:require prompt-text-joins
              (not (== (str:find $plain (str:join "\n_cache_chunk_\n" $chunks)) (TextNotFound (str:join "\n_cache_chunk_\n" $chunks)))))))
   (println! UNIT_TASK_INDEX_CHUNKS_OK))
