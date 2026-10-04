!(import! &self ../src/native_llm_chat.metta)

; PeTTa's assert exits nonzero on a failed check. These tests are scripted
; locally and never require an LLM provider or a credential.
!(assert (==
   (native-chat:strip-layout
     "a_cache_boundary_b_cache_boundary_c_cache_boundary_d_cache_boundary_e")
   "abcd_cache_boundary_e"))
!(assert (==
   (native-chat:completion-url "http://127.0.0.1:8877/v1///")
   "http://127.0.0.1:8877/v1/chat/completions"))
!(assert (==
   (json-parse (native-chat:encode "m\"x" 42 "medium" "hi\n🌱"))
   (json-parse
     "{\"model\":\"m\\\"x\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\\n🌱\"}],\"max_tokens\":42,\"reasoning\":{\"effort\":\"medium\"}}")))
!(assert (==
   (native-chat:request "http://127.0.0.1:8877/v1" "m" 7 "low" "p")
   (http:request "POST" "http://127.0.0.1:8877/v1/chat/completions"
     ((http:header "Content-Type" "application/json"))
     "{\"model\":\"m\",\"messages\":[{\"role\":\"user\",\"content\":\"p\"}],\"max_tokens\":7,\"reasoning\":{\"effort\":\"low\"}}"
     120000 1048576)))
!(assert (==
   (native-chat:decode
     "{\"choices\":[{\"message\":{\"content\":\" done \"}},{\"message\":{\"content\":\"ignored\"}}]}")
   (native-chat:reply " done ")))
!(assert (== (native-chat:decode "{\"choices\":[]}")
             (native-chat:failure missing-choice)))
!(assert (== (native-chat:decode "{secret:broken}")
             (native-chat:failure invalid-json)))
!(assert (== (native-chat:submitted turn-7 (io:pending 12))
             (native-chat:pending turn-7 12)))
!(assert (==
   (native-chat:submitted turn-7 (Error private-source private-reason))
   (native-chat:failed turn-7 submit-error)))
!(assert (==
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (http:response 200
       "{\"choices\":[{\"message\":{\"content\":\"done\"}}]}")))
   (native-chat:completed turn-7 "done")))
!(assert (==
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (http:response 429 "private provider body")))
   (native-chat:failed turn-7 (http-status 429))))
!(assert (==
   (native-chat:observe (native-chat:pending turn-7 12)
     (io:event 12 (Error (io:request 12) (http:error transport 7 "private"))))
   (native-chat:failed turn-7 transport-error)))
!(assert (==
   (native-chat:event-id
     (io:event 13 (Error (io:request 13) (http:error transport 7 "private"))))
   13))

; This check submits only when native HTTP is absent, so it cannot make a
; network request on an HTTP-enabled build.
!(if (== (io:capabilities) ())
     (assert (==
       (native-chat:submit turn-7 "http://127.0.0.1:8877/v1" "m" 7 "low" "p")
       (native-chat:failed turn-7 submit-error)))
     ())
