!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/iter/host_cetta.metta)
!(import! &self ../../src/provider_request_cetta.metta)
!(import! &self (library llm))
; Iter's provider request meets the native request contract that every
; provider path, the agents' ChatGPT models among them, checks before sending.
!(let* (($message (JsonObject ((JsonPair "role" "user") (JsonPair "content" "unit"))))
        ($body (ih:request-body "chatgpt:unit-model" (JsonArray ($message)) (JsonArray ())))
        ; As llm:structured-plan hands it to the provider path: tagged JSON.
        ($tagged (preq:tag $body))
        ($_ (nt:require iter-request-fields-admitted (== (llm:v1:request-fields $tagged) True)))
        ($_ (nt:require iter-request-valid (== (llm:v1:validate-request $tagged) True)))
        ($_ (nt:require iter-reasoning-is-an-effort
              (== (sf:get (sf:get $body "reasoning" JsonNull) "effort" "") "medium"))))
   (println! UNIT_ITER_REQUEST_OK))
