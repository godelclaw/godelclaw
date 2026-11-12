; Census scenario (world mode): one production-shaped native command turn, with
; native transport and ledger, model commentary and a display stream, against
; the world's loopback provider. The working directory is the test world, so
; the provider's response body and request count are files here.
!(import! &self ../src/native_llm.metta)
!(import! &self ./native_test_support.metta)
!(fs:write "response.body" "{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"t1\",\"type\":\"function\",\"function\":{\"name\":\"metta\",\"arguments\":\"{\\\"program\\\":\\\"((nop))\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}")
!(nt:require census-provider-answer (nt:equal (llm:command "fixture-model" 42 "low" "S_cache_boundary_U") "((nop))"))
!(nt:require census-provider-single-post (nt:equal (json:parse (str:strip-python (fs:read-text "count"))) 1))
!(println! CENSUS_PROVIDER_OK)
