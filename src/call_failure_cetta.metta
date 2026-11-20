; A provider call's failure, classified natively from what its observers saw:
; its cause (src/native_call_cetta.metta), its kind of failure, its HTTP
; status (0 for none) and its owner's retry-after (-1 for none). The facts of
; a failed call, the operator's line for it, and what a compaction's caller
; learns. No host, no request or response bytes.
!(import! &self (library math))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self ./turn_health_cetta.metta)

(= (failure:text $value) (if (== (get-type $value) String) $value (format-args "{}" (cons-atom $value ()))))

; ------------------------------------------------------------ failure facts
; A failed call's facts, natively: (CallFailureV1 code type status transient
; retry), from its cause, its kind of failure, its HTTP status (0 for none)
; and its owner's retry-after (-1 for none).
; - code names the failure for the health record and the pacer, type the
;   error class its cause stands for; a deterministic rejection's code is its
;   status (provider-rejected-NNN);
; - a timeout, a rate limit (429) and a server error (5xx) are transient;
; - retry is the owner's retry-after, else an admission denial's, never below
;   0, else -1 (none).
(= (failure:of $cause $kind $status $retry)
   (let* (((CallClass $code $type $moving $own) (failure:class $cause (failure:text $kind)))
          ($named (if (health:rejected-status? $status) (str:concat "provider-rejected-" (repr $status)) $code))
          ($transient (or $moving (or (== $status 429) (>= $status 500))))
          ($after (if (>= $retry 0) $retry $own)))
     (CallFailureV1 $named $type $status $transient $after)))
; A cause's code, error class, whether it is transient by itself, and its own
; retry-after. Without a cause, the failure's kind names the class.
(= (failure:class none $kind)
   (if (== $kind "transport-error") (CallClass "OSError" "OSError" False -1)
     (CallClass "RuntimeError" "RuntimeError" False -1)))
(= (failure:class (CallDenied $reason $retry) $kind)
   (CallClass "AdmissionDenied" "AdmissionDenied" False (if (> $retry 0) $retry 0)))
(= (failure:class (CallTransport $transport $code) $kind)
   (if (== $transport "timeout") (CallClass "TimeoutError" "TimeoutError" True -1)
     (CallClass "OSError" "OSError" False -1)))
(= (failure:class (CallRefusedResponse $reason $bound) $kind) (CallClass (failure:text $reason) "ResponseRefused" False -1))
(= (failure:class (CallPreparation $code) $kind) (CallClass "ValueError" "ValueError" False -1))
(= (failure:class CallConflict $kind) (CallClass "CompletionConflict" "CompletionConflict" False -1))
(= (failure:class (CallError $type) $kind) (CallClass $type $type False -1))

; The operator's line for a failed call: its kind (or provider-error), the
; provider, the status (0 for none), whether it was sent, the milliseconds it
; waited and its deadline, then what its cause and the provider stated. Never
; request or response bytes beyond the provider's sanitized statement.
(= (failure:known-kinds)
   ("admission-error" "submit-error" "transport-error" "decode-error" "http-status" "correlation-error"
    "duplicate-completion" "invalid-phase" "retired-handle" "unfinished-provider-response"
    "refused-tool-call" "incomplete-stream" "ownership-error" "response-too-large"
    "response-budget-exceeded" "non-text-response" "credential-in-response" "submission-limit"))
(= (failure:line $cause $provider $kind $status $sent $elapsed $deadline $stated)
   (let* (($text (failure:text $kind))
          ($named (if (failure:member? $text (failure:known-kinds)) $text "provider-error"))
          ($shown (failure:provider (failure:text $provider)))
          ($code (if (and (>= $status 100) (<= $status 599)) $status 0))
          ($flag (if $sent "true" "false")))
     (str:join "" ("[native_llm] failed request: " $named "; provider=" $shown "; status=" (repr $code)
                   "; sent=" $flag "; elapsed_ms=" (repr (if (> $elapsed 0) $elapsed 0))
                   "; deadline_ms=" (repr $deadline) (failure:detail $cause) (failure:stated $stated) "\n"))))
; A subscription call is shown as the OpenAI call it is.
(= (failure:provider $name)
   (if (== $name "chatgpt") "openai"
     (if (failure:member? $name ("synthetic" "anthropic" "openai" "kimi")) $name "unknown")))
(= (failure:member? $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (failure:member? $item (cdr-atom $items)))))
(= (failure:detail none) "")
(= (failure:detail (CallDenied $reason $retry)) (str:concat "; reason=" (failure:text $reason)))
(= (failure:detail (CallTransport $transport $code))
   (str:join "" ("; transport_code=" (repr $code) "; transport_kind=" $transport)))
(= (failure:detail (CallRefusedResponse $reason $bound))
   (if (> $bound 0) (str:concat "; response_bound=" (repr $bound)) ""))
; A refused preparation is named by its code; any other reason may quote the
; request and stays out of the line.
(= (failure:detail (CallPreparation $code))
   (case (re:full-match (re:pattern "[A-Za-z0-9-]{1,64}") $code)
     ((re:no-match "") ($named (str:concat "; preparation=" $code)))))
(= (failure:detail CallConflict) "")
(= (failure:detail (CallError $type)) "")
(= (failure:stated (CallStated $code $message))
   (str:concat (if (== $code "") "" (str:concat "; code=" $code))
               (if (== $message "") "" (str:concat "; message=" $message))))

; What engine observations become a cause: a transport failure by its
; category, consistent with its code (28 is a timeout, for curl), and an
; answer the engine refused to keep by one of its known reasons, with the
; bound it passed (0 when none). Anything else is none: the failure's kind
; names it.
(= (failure:transport-cause $kind $code)
   (let $category (failure:text $kind)
     (if (failure:count? $code)
       (if (or (and (== $category "timeout") (== $code 28)) (and (== $category "transport") (not (== $code 28))))
         (CallTransport $category $code) none)
       none)))
(= (failure:refused-cause $reason $bound)
   (let $text (failure:text $reason)
     (if (failure:member? $text ("response-too-large" "response-budget-exceeded" "non-text-response" "credential-in-response"))
       (CallRefusedResponse $text (if (failure:whole? $bound) $bound 0))
       none)))
; A transport code is a whole number from 1 to 65535; a bound is any positive
; whole number.
(= (failure:count? $value) (if (failure:whole? $value) (<= $value 65535) False))
(= (failure:whole? $value)
   (if (== (get-type $value) Number) (if (== (math:floor $value) $value) (> $value 0) False) False))

; A compaction is maintenance: its caller learns whether a later try can pass
; (a rate limit, a server error, or an admission denial with a positive
; retry-after) and the retry-after.
(= (failure:compaction (CallFailureV1 $code $type $status $transient $retry))
   (CompactionFailedV1 (or (or (== $status 429) (>= $status 500)) (and (== $type "AdmissionDenied") (> $retry 0))) $retry))
