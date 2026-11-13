; Census scenario (world mode): one production-shaped native command turn, with
; native transport and ledger, model commentary shared to the worklog and a
; display stream, against the world's loopback provider. The working directory
; is the test world, so the provider's response body and request count, and
; the worklog, are files here.
!(import! &self ../src/native_llm.metta)
!(import! &self ./native_test_support.metta)
!(pw:transact "worklog" "on" "" JsonNull)
!(fs:write "response.body" "{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"Census note.\",\"tool_calls\":[{\"id\":\"t1\",\"type\":\"function\",\"function\":{\"name\":\"metta\",\"arguments\":\"{\\\"program\\\":\\\"((nop))\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}")
!(nt:require census-provider-answer (nt:equal (llm:command "fixture-model" 42 "low" "S_cache_boundary_U") "((nop))"))
!(nt:require census-provider-single-post (nt:equal (json:parse (str:strip-python (fs:read-text "count"))) 1))
; nt:contains answers True for an Error operand: the file must exist.
!(nt:require census-commentary-shared
   (if (fs:exists "worklog/notes.jsonl") (nt:contains (fs:read-text "worklog/notes.jsonl") "Census note.") False))
!(nt:require census-observer-released
   (nt:equal (collapse (match &observer-calls $call $call)) ()))
!(println! CENSUS_PROVIDER_OK)
