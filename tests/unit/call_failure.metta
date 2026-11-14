; Unit tests of src/call_failure_cetta.metta; no Python. The expected facts
; and lines are what the retired host report (native_llm_host.failed and its
; _record_failure) gave for the same failure.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/call_failure_cetta.metta)

(= (cf:no-statement) (CallStated "" ""))
(= (cf:line $cause $provider $kind $status $sent $elapsed $deadline)
   (failure:line $cause $provider $kind $status $sent $elapsed $deadline (cf:no-statement)))

; A failure's facts from its cause, kind, status and the owner's retry-after.
!(let* (($_ (nt:require transport-error-without-cause-is-no-timeout
              (nt:equal (failure:of none transport-error 0 -1) (CallFailureV1 "OSError" "OSError" 0 False -1))))
        ($_ (nt:require other-failure-without-cause
              (nt:equal (failure:of none decode-error 0 -1) (CallFailureV1 "RuntimeError" "RuntimeError" 0 False -1))))
        ($_ (nt:require timeout-is-transient
              (nt:equal (failure:of (CallTransport "timeout" 28) transport-error 0 -1)
                        (CallFailureV1 "TimeoutError" "TimeoutError" 0 True -1))))
        ($_ (nt:require transport-failure-is-not-transient
              (nt:equal (failure:of (CallTransport "transport" 7) transport-error 0 -1)
                        (CallFailureV1 "OSError" "OSError" 0 False -1))))
        ($_ (nt:require denial-keeps-its-retry
              (nt:equal (failure:of (CallDenied "provider-cooldown" 30) admission-error 0 -1)
                        (CallFailureV1 "AdmissionDenied" "AdmissionDenied" 0 False 30))))
        ($_ (nt:require denial-retry-never-below-zero
              (nt:equal (failure:of (CallDenied "native-call-capacity" -1) admission-error 0 -1)
                        (CallFailureV1 "AdmissionDenied" "AdmissionDenied" 0 False 0))))
        ($_ (nt:require owner-retry-replaces-the-cause
              (nt:equal (failure:of (CallDenied "provider-cooldown" 30) admission-error 0 5)
                        (CallFailureV1 "AdmissionDenied" "AdmissionDenied" 0 False 5))))
        ($_ (nt:require deterministic-rejection-named-by-status
              (nt:equal (failure:of none http-status 400 -1) (CallFailureV1 "provider-rejected-400" "RuntimeError" 400 False -1))))
        ($_ (nt:require timeout-status-is-not-a-rejection
              (nt:equal (failure:of none http-status 408 -1) (CallFailureV1 "RuntimeError" "RuntimeError" 408 False -1))))
        ($_ (nt:require rate-limit-is-transient
              (nt:equal (failure:of none http-status 429 12) (CallFailureV1 "RuntimeError" "RuntimeError" 429 True 12))))
        ($_ (nt:require server-error-is-transient
              (nt:equal (failure:of none http-status 503 -1) (CallFailureV1 "RuntimeError" "RuntimeError" 503 True -1))))
        ($_ (nt:require refused-answer-named-by-its-reason
              (nt:equal (failure:of (CallRefusedResponse "response-too-large" 8388608) response-too-large 0 -1)
                        (CallFailureV1 "response-too-large" "ResponseRefused" 0 False -1))))
        ($_ (nt:require refused-preparation-is-a-value-error
              (nt:equal (failure:of (CallPreparation "invalid-prompt-utf8") admission-error 0 -1)
                        (CallFailureV1 "ValueError" "ValueError" 0 False -1))))
        ($_ (nt:require completion-conflict
              (nt:equal (failure:of CallConflict local-completion-conflict 0 -1)
                        (CallFailureV1 "CompletionConflict" "CompletionConflict" 0 False -1))))
        ($_ (nt:require refused-seed-keeps-its-class
              (nt:equal (failure:of (CallError "KeyError") admission-error 0 -1) (CallFailureV1 "KeyError" "KeyError" 0 False -1)))))
   (println! UNIT_CALL_FAILURE_FACTS_OK))

; The operator's line: never a kind or provider it does not know, never bytes.
!(let* (($_ (nt:require unknown-kind-and-provider-not-printed
              (nt:equal (cf:line none "fixture-secret" "fixture-secret https://private.invalid" 0 False 0 0)
                        "[native_llm] failed request: provider-error; provider=unknown; status=0; sent=false; elapsed_ms=0; deadline_ms=0\n")))
        ($_ (nt:require kimi-keeps-its-identity
              (nt:equal (cf:line none "kimi" transport-error 0 True 1200 600000)
                        "[native_llm] failed request: transport-error; provider=kimi; status=0; sent=true; elapsed_ms=1200; deadline_ms=600000\n")))
        ($_ (nt:require subscription-shown-as-openai
              (nt:equal (cf:line none "chatgpt" http-status 404 True 3 10)
                        "[native_llm] failed request: http-status; provider=openai; status=404; sent=true; elapsed_ms=3; deadline_ms=10\n")))
        ($_ (nt:require status-outside-http-is-zero-and-elapsed-never-negative
              (nt:equal (cf:line none "anthropic" http-status 999 False -5 0)
                        "[native_llm] failed request: http-status; provider=anthropic; status=0; sent=false; elapsed_ms=0; deadline_ms=0\n")))
        ($_ (nt:require denial-reason-shown
              (nt:equal (cf:line (CallDenied "provider-cooldown" 30) "synthetic" admission-error 0 False 0 0)
                        "[native_llm] failed request: admission-error; provider=synthetic; status=0; sent=false; elapsed_ms=0; deadline_ms=0; reason=provider-cooldown\n")))
        ($_ (nt:require transport-category-shown
              (nt:equal (failure:detail (CallTransport "timeout" 28)) "; transport_code=28; transport_kind=timeout")))
        ($_ (nt:require refused-bound-shown
              (nt:equal (failure:detail (CallRefusedResponse "response-too-large" 8388608)) "; response_bound=8388608")))
        ($_ (nt:require refused-without-bound
              (nt:equal (failure:detail (CallRefusedResponse "non-text-response" 0)) "")))
        ($_ (nt:require preparation-named-by-code
              (nt:equal (failure:detail (CallPreparation "invalid-prompt-utf8")) "; preparation=invalid-prompt-utf8")))
        ($_ (nt:require preparation-reason-quoting-the-request-withheld
              (nt:equal (failure:detail (CallPreparation "(llm:v1:error bad request text)")) "")))
        ($_ (nt:require provider-statement-shown
              (nt:equal (failure:line none "openai" http-status 400 True 0 0 (CallStated "string_above_max_length" "too long"))
                        "[native_llm] failed request: http-status; provider=openai; status=400; sent=true; elapsed_ms=0; deadline_ms=0; code=string_above_max_length; message=too long\n"))))
   (println! UNIT_CALL_FAILURE_LINE_OK))

; Engine observations become causes only when consistent; a compaction's
; caller learns whether a later try can pass.
!(let* (($_ (nt:require inconsistent-transport-observations-are-no-cause
              (nt:equal (collapse (map-atom ((timeout 7) (transport 28) (transport True) (transport -1) (transport 7.5) (secret 7) (transport 70000)) $case
                                     (let ($kind $code) $case (failure:transport-cause $kind $code))))
                        ((none none none none none none none)))))
        ($_ (nt:require consistent-transport-observations
              (nt:equal ((failure:transport-cause timeout 28) (failure:transport-cause "transport" 7))
                        ((CallTransport "timeout" 28) (CallTransport "transport" 7)))))
        ($_ (nt:require refused-answers-by-known-reason
              (nt:equal ((failure:refused-cause response-too-large 4096) (failure:refused-cause "credential-in-response" x)
                         (failure:refused-cause "unexpected-reason" 4096))
                        ((CallRefusedResponse "response-too-large" 4096) (CallRefusedResponse "credential-in-response" 0) none))))
        ($_ (nt:require compaction-rate-limit-retryable
              (nt:equal (failure:compaction (failure:of none http-status 429 7)) (CompactionFailedV1 True 7))))
        ($_ (nt:require compaction-server-error-retryable
              (nt:equal (failure:compaction (failure:of none http-status 502 -1)) (CompactionFailedV1 True -1))))
        ($_ (nt:require compaction-rejection-not-retryable
              (nt:equal (failure:compaction (failure:of none http-status 400 -1)) (CompactionFailedV1 False -1))))
        ($_ (nt:require compaction-denial-with-retry-retryable
              (nt:equal (failure:compaction (failure:of (CallDenied "provider-cooldown" 30) admission-error 0 -1)) (CompactionFailedV1 True 30))))
        ($_ (nt:require compaction-denial-without-retry-not-retryable
              (nt:equal (failure:compaction (failure:of (CallDenied "native-call-capacity" -1) admission-error 0 -1)) (CompactionFailedV1 False 0)))))
   (println! UNIT_CALL_FAILURE_CAUSES_OK))
