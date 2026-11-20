!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/iter/core.metta)
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
; A tool that fails, by raising or by returning an Error, is the step's output.
!(let* (($none (ih:object))
        ($failed (iter:tool-run $none "query" (ih:object)))
        ($_ (nt:require failing-tool-is-its-output (str:starts-with $failed "Tool execution failed: ")))
        ($fault (iter:tool-outcome ((Error component-call (Fault "deliberate failure")))))
        ($_ (nt:require fault-is-a-failure (str:starts-with $fault "Tool execution failed: ")))
        ($_ (nt:require failure-is-bounded (<= (str:char-length $fault) 700)))
        ($uncertain (iter:tool-outcome ((Error component-call (Incomplete (worker-result-unknown died))))))
        ($_ (nt:require incomplete-is-uncertain (str:starts-with $uncertain "Tool outcome uncertain: ")))
        ($ordinary (iter:tool-outcome ((MemoryUnavailable embedding endpoint-not-configured))))
        ($_ (nt:require ordinary-value-kept (== $ordinary "(MemoryUnavailable embedding endpoint-not-configured)")))
        ($_ (nt:require text-kept (== (iter:tool-outcome ("SUCCESS")) "SUCCESS")))
        ($_ (nt:require nothing-is-a-failure (== (iter:tool-outcome ()) "Tool execution failed: no result")))
        ($_ (nt:require backoff-schedule
              (== (map-atom (1 3 4 5 8 9 10 20) $n (iter:failure-wait $n)) (1 1 15 30 240 480 600 600)))))
   (println! UNIT_ITER_TOOL_FAILURE_OK))
