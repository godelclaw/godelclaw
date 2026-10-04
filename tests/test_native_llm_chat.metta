!(import! &self ../src/native_llm_chat.metta)

; Request parity, including escaping and exactly three layout boundaries.
!(assertEqual
   (native-chat:strip-layout
     "A_cache_boundary_B_cache_boundary_C_cache_boundary_D_cache_boundary_E")
   "ABCD_cache_boundary_E")
!(assertEqual
   (native-chat:completion-url "http://127.0.0.1:8877/v1///")
   "http://127.0.0.1:8877/v1/chat/completions")
!(assertEqual
   (json-parse
     (native-chat:encode "model\"x" 42 "medium" "hi\n_cache_boundary_🌱"))
   (json-parse
     "{\"model\":\"model\\\"x\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\\n🌱\"}],\"max_tokens\":42,\"reasoning\":{\"effort\":\"medium\"}}"))

; The request constructor carries no credential-bearing header.
!(assertEqual
   (native-chat:request "http://127.0.0.1:8877/v1" "m" 7 "low" "p")
   (http:request "POST" "http://127.0.0.1:8877/v1/chat/completions"
     ((http:header "Content-Type" "application/json"))
     "{\"model\":\"m\",\"messages\":[{\"role\":\"user\",\"content\":\"p\"}],\"max_tokens\":7,\"reasoning\":{\"effort\":\"low\"}}"
     120000 1048576))

; Success preserves assistant whitespace, while empty or malformed responses
; produce typed failures without echoing response bytes.
!(assertEqual
   (native-chat:decode
     "{\"choices\":[{\"message\":{\"content\":\"  hello 🌱  \"}},{\"message\":{\"content\":\"ignore\"}}]}")
   (native-chat:reply "  hello 🌱  "))
!(assertEqual (native-chat:decode "{\"choices\":[]}")
              (native-chat:failure missing-choice))
!(assertEqual (native-chat:decode "{\"choices\":[{}]}")
              (native-chat:failure missing-message))
!(assertEqual (native-chat:decode "{\"choices\":[{\"message\":{}}]}")
              (native-chat:failure missing-content))
!(assertEqual
   (native-chat:decode "{\"choices\":[{\"message\":{\"content\":\" \\n \"}}]}")
   (native-chat:failure empty-content))
!(assertEqual (native-chat:decode "{secret:broken}")
              (native-chat:failure invalid-json))

; Scripted async events exercise the correlation and bounded error surface.
!(assertEqual (native-chat:submitted turn-7 (io:pending 12))
              (native-chat:pending turn-7 12))
!(assertEqual (native-chat:submitted turn-7 (Error private-source private-reason))
              (native-chat:failed turn-7 submit-error))
!(assertEqual
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (http:response 200
       "{\"choices\":[{\"message\":{\"content\":\"done\"}}]}")))
   (native-chat:completed turn-7 "done"))
!(assertEqual
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (http:response 429 "private provider body")))
   (native-chat:failed turn-7 (http-status 429)))
!(assertEqual
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (Error (io:request 12) (http:error transport 7 "private"))))
   (native-chat:failed turn-7 transport-error))
!(assertEqual
   (native-chat:observe (native-chat:pending turn-7 12) (io:idle))
   (native-chat:pending turn-7 12))
!(assertEqual
   (native-chat:event-id (io:event 13 (http:response 200 "other completion")))
   13)
!(assertEqual
   (native-chat:event-id
     (io:event 13 (Error (io:request 13) (http:error transport 7 "private"))))
   13)

; On a CeTTa build with no HTTP provider, submission fails locally. The
; guard prevents this test from making a request on HTTP-enabled builds.
!(if (== (io:capabilities) ())
     (assertEqual
       (native-chat:submit turn-7 "http://127.0.0.1:8877/v1" "m" 7 "low" "p")
       (native-chat:failed turn-7 submit-error))
     ())
