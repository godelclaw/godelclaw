; Edges of the native provider call, against the native HTTP fixture peer: a
; streamed reply cut off before its end is no answer and completes nothing;
; a local decoder that refuses for lack of capacity is retried on the body it
; already holds, without another request; a pending answer that is not the
; call's own is refused while the owned answer still arrives; input that
; arrives meanwhile does not discard the completed response; a detached wait
; answers pending and the same call is delivered later; a recycle request
; waits for the admitted call; a request whose extension contradicts its
; reasoning setting is refused before admission; a final "[DONE]" without
; the blank line completes the call once.
!(import! &self ./provider_native_qualification.metta)

(= (llm-cases:reply) "{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"(nop)\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}")
; src/native_llm.metta with its decoder refusing three times for capacity.
(= (llm-cases:capacity-module $root $path)
   (let* (($source (fs:read-text (nt:join $root "src/native_llm.metta")))
          ($absolute (str:join (str:concat (nt:join $root "src") "/") (str:split "./src/" $source)))
          ($decoder "(case (llm:v1:response-body $protocol $stream $body $limit)")
          ($parts (str:split $decoder $absolute))
          ($_ (nt:require capacity-fixture-has-one-decoder-call (nt:equal (size-atom $parts) 2)))
          ($patched (str:join "(case (decode-test:body $protocol $stream $body $limit)" $parts))
          ($_ (fs:write $path (str:concat $patched (str:join "\n" (
                ""
                "!(bind! &decode-test-attempts (new-state 0))"
                "(= (decode-test:refusals) (get-state &decode-test-attempts))"
                "(: decode-test:body (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))"
                "(= (decode-test:body $protocol $stream $body $limit)"
                "   (let $count (get-state &decode-test-attempts)"
                "     (if (< $count 3)"
                "       (let $_ (change-state! &decode-test-attempts (+ $count 1)) (llm:v1:error stream-capacity))"
                "       (llm:v1:response-body $protocol $stream $body $limit))))"
                ""))))))
     $path))

(= (llm-cases:run $root $sdk $core $artifacts $fixture)
   (let* (($eof (nq:world $root $sdk $core $artifacts $fixture "eof"))
          ($_ (fs:write (nq:path $eof "response.body")
                "data: {\"id\":\"fixture\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"(no\"},\"finish_reason\":null}]}\n\n"))
          ($eof-out (nq:output (nq:guest $eof "fixture-model" "petta"
                      "!(nt:require a-cut-stream-is-no-answer (nt:equal (llm:command \"fixture-model\" 42 \"low\" \"input\") \"()\"))\n!(println! NATIVE_EOF_OK)"
                      (("METTACLAW_LLM_STREAM" "1") ("METTACLAW_STREAM_DIR" (nq:path $eof "display"))))))
          ($_ (nt:require a-cut-stream-finished (nt:contains $eof-out "NATIVE_EOF_OK")))
          ($_ (nt:require a-cut-stream-is-one-request-and-completes-nothing
                (and (nt:equal (nq:posts $eof) 1) (nt:equal (nq:rows $eof "results") ()))))
          ($_ (nq:close $eof))

          ($capacity (nq:world $root $sdk $core $artifacts $fixture "capacity"))
          ($_ (fs:write (nq:path $capacity "response.body") (llm-cases:reply)))
          ($module (llm-cases:capacity-module $root (nq:path $capacity "decode-capacity.metta")))
          ($capacity-out (nq:output (nq:guest-module $capacity $module "fixture-model" "petta"
                           "!(println! (RESULT (llm:command \"fixture-model\" 42 \"low\" \"input\")))\n!(println! (REFUSALS (decode-test:refusals)))"
                           () $root ())))
          ($_ (nt:require a-decoder-capacity-refusal-is-retried-on-the-held-body
                (and (nt:contains $capacity-out "(RESULT \"(nop)\")")
                (and (nt:contains $capacity-out "(REFUSALS 3)")
                (and (nt:equal (nq:posts $capacity) 1)
                     (not (nt:equal (nq:rows $capacity "results") ())))))))
          ($_ (nq:close $capacity))

          ($foreign (nq:world $root $sdk $core $artifacts $fixture "foreign"))
          ($_ (fs:write (nq:path $foreign "response.body") (llm-cases:reply)))
          ($foreign-out (nq:output (nq:guest $foreign "fixture-model" "petta"
                          "!(let $plan (pcall:command-plan \"fixture-model\" 42 \"low\" \"input\") (let $state (llm:begin $plan) (progn (println! (FOREIGN (llm:wait-once $plan (llm:v1:pending other other \"chat-completions\" 1) 20))) (println! (RESULT (llm:after-submit $plan $state))))))"
                          ())))
          ($_ (nt:require a-foreign-pending-answer-is-refused-and-the-owned-one-arrives
                (and (nt:contains $foreign-out "(FOREIGN (llm:v1:rejected ownership-error))")
                (and (nt:contains $foreign-out "(RESULT \"(nop)\")")
                     (nt:equal (nq:posts $foreign) 1)))))
          ($_ (nq:close $foreign))

          ($steered (nq:world $root $sdk $core $artifacts $fixture "steered"))
          ($_ (fs:write (nq:path $steered "response.body") (llm-cases:reply)))
          ($steered-out (nq:output (nq:guest $steered "fixture-model" "petta"
                          "!(let $plan (pcall:command-plan \"fixture-model\" 42 \"low\" \"input\") (let $state (llm:begin $plan) (progn (turn:put \"inputs\" (\"new-input\")) (println! (RESULT (llm:after-submit $plan $state))))))"
                          ())))
          ($_ (nt:require new-input-does-not-discard-the-completed-response
                (and (nt:contains $steered-out "(RESULT \"(nop)\")")
                (and (nt:equal (nq:posts $steered) 1)
                     (not (nt:equal (nq:rows $steered "results") ()))))))
          ($_ (nq:close $steered))

          ($detached (nq:world $root $sdk $core $artifacts $fixture "detached"))
          ($_ (fs:write (nq:path $detached "response.body") (llm-cases:reply)))
          ($_ (fs:write (nq:path $detached "delay-ms") "300"))
          ($detached-out (nq:output (nq:guest $detached "fixture-model" "petta"
                           "!(let $plan (pcall:command-plan \"fixture-model\" 42 \"low\" \"input\") (let $state (llm:begin $plan) (let $waiting (llm:wait-once $plan $state 20) (progn (println! (DETACHED $waiting)) (println! (RESULT (llm:after-submit $plan $waiting)))))))"
                           ())))
          ($_ (nt:require a-detached-wait-is-pending-and-the-same-call-arrives-later
                (and (nt:contains $detached-out "(DETACHED (llm:v1:pending")
                (and (nt:contains $detached-out "(RESULT \"(nop)\")")
                     (nt:equal (nq:posts $detached) 1)))))
          ($_ (nq:close $detached))

          ($recycle (nq:world $root $sdk $core $artifacts $fixture "recycle"))
          ($_ (fs:write (nq:path $recycle "response.body") (llm-cases:reply)))
          ($recycle-out (nq:output (nq:guest $recycle "fixture-model" "petta"
                          (str:join "" ("!(let $plan (pcall:command-plan \"fixture-model\" 42 \"low\" \"input\") (let $state (llm:begin $plan) (progn (fs:write "
                                        (repr (nq:path $recycle "recycle")) " \"fixture\") (println! (RESULT (llm:after-submit $plan $state))))))"))
                          ())))
          ($_ (nt:require a-recycle-request-waits-for-the-admitted-call
                (and (nt:contains $recycle-out "(RESULT \"(nop)\")")
                (and (nt:equal (nq:posts $recycle) 1)
                     (not (nt:equal (nq:rows $recycle "results") ()))))))
          ($_ (nq:close $recycle))

          ($conflict (nq:world $root $sdk $core $artifacts $fixture "conflict"))
          ($conflict-out (nq:output (nq:guest $conflict "fixture-model" "petta"
                           "!(let $plan (llm:structured-plan (json-parse \"{\\\"messages\\\": [{\\\"role\\\": \\\"user\\\", \\\"content\\\": \\\"input\\\"}], \\\"max_tokens\\\": 42, \\\"reasoning\\\": {\\\"effort\\\": \\\"low\\\"}, \\\"extensions\\\": {\\\"reasoning_effort\\\": \\\"high\\\"}}\") \"turn\") (println! (READY (llm:plan-ready $plan))))"
                           ())))
          ($_ (nt:require a-contradicting-extension-is-refused-before-admission
                (and (nt:contains $conflict-out "(READY false)")
                (and (nt:equal (nq:posts $conflict) 0)
                     (nt:equal (nq:rows $conflict "requests") ())))))
          ($_ (nq:close $conflict))

          ($terminal (nq:world $root $sdk $core $artifacts $fixture "terminal"))
          ($_ (fs:write (nq:path $terminal "response.body")
                "data: {\"id\":\"fixture\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"(nop)\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}\n\ndata: [DONE]"))
          ($terminal-out (nq:output (nq:guest $terminal "fixture-model" "petta"
                           "!(println! (RESULT (llm:command \"fixture-model\" 42 \"low\" \"input\")))"
                           (("METTACLAW_LLM_STREAM" "1") ("METTACLAW_STREAM_DIR" (nq:path $terminal "display"))))))
          ($_ (nt:require a-final-done-without-the-blank-line-completes-once
                (and (nt:contains $terminal-out "(RESULT \"(nop)\")")
                (and (nt:equal (nq:posts $terminal) 1)
                     (not (nt:equal (nq:rows $terminal "results") ()))))))
          ($_ (nq:close $terminal)))
     NativeLlmCasesPassedV1))
