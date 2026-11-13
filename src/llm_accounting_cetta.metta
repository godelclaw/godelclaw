; The native call owner records its policy and lifecycle in the native LLM
; ledger. Host calls here only publish existing host effects.
!(import! &self ./native_lifetime_cetta.metta)
!(import! &self ./native_llm_host.py)
!(import! &self ./interop_flags_cetta.metta)
!(import! &self ./native_call_cetta.metta)
!(import! &self ./completion_effects_cetta.metta)
!(import! &self ./rejection_gate_cetta.metta)
!(import! &self ./turn_health_cetta.metta)
!(import! &self ./display_cetta.metta)

(: account:ready (-> %Undefined% %Undefined%))
(= (account:ready $plan) (call:ready? $plan))

(: account:metadata (-> %Undefined% %Undefined%))
(= (account:metadata $plan) (call:policy-metadata $plan))
(: account:reject (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:reject $plan $reason $retry)
   (let* (($native (call:refused $plan))
          ($ignored0 (py-call (native_llm_host.policy_rejected (call:host $plan) $reason $retry)))) False))
(: account:cleanup-unsent (-> %Undefined% %Undefined% %Undefined%))
(= (account:cleanup-unsent $plan $metadata)
   (let $ignored1 (nl:fail $metadata "submit-error" JsonNull JsonNull
                       (lp:field $metadata "soft_elapsed" 0) (system:wall-time))
     (account:reject $plan "native-admission-error" -1)))
(: account:claimed (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:claimed $plan $metadata $answer)
   (case $answer
     (((LedgerChanged True)
       (if (account:shown $plan)
         (let $_ (call:claimed $plan) True)
         (account:cleanup-unsent $plan $metadata)))
      ((LedgerChanged False)
       (case (lp:fail (lp:field $metadata "id" "") False JsonNull JsonNull (system:wall-time))
         (((LedgerError $ignored2) (account:reject $plan "native-claim-error" -1))
          ($settled (account:reject $plan "native-call-capacity" -1)))))
      ($error (account:cleanup-unsent $plan $metadata)))))
; A claimed call opens its display (src/display_cetta.metta), and the
; commentary observer on the host when commentary is on. A call nothing
; observes has no host effect here.
(: account:shown (-> %Undefined% %Undefined%))
(= (account:shown $plan)
   (let $opened (display:call-begin (call:correlation $plan) (call:compaction? $plan)
                  (call:fact $plan "provider" "") (call:fact $plan "purpose" "turn"))
     (if (== (call:fact $plan "commentary" false) true)
       (host:flag (py-call (native_llm_host.claim_effect (call:host $plan)
                             (if (== (call:fact $plan "prepared_stream" false) true) "true" "false"))))
       True)))
(: account:admitted (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:admitted $plan $metadata $answer)
   (case $answer
     (((LedgerAdmitted $ignored3) (account:claimed $plan $metadata (nl:claim $metadata (system:wall-time))))
      ((LedgerDenied $reason $retry) (account:reject $plan $reason $retry))
      ($error (account:reject $plan "native-admission-error" -1)))))
(: account:claim (-> %Undefined% %Undefined%))
(= (account:claim $plan)
   (if (not (call:begin-claim $plan)) False
     (let $metadata (account:metadata $plan)
       (account:admitted $plan $metadata
         (lp:admit (lp:field $metadata "id" "") (lp:field $metadata "provider" "")
                   (lp:field $metadata "maximum" 0) (lp:field $metadata "fingerprint" "") (system:wall-time))))))
; An empty origin is described as null, as the retired host route did.
(: account:request-origin (-> %Undefined% %Undefined%))
(= (account:request-origin $metadata)
   (let $origin (lp:field $metadata "origin" JsonNull)
     (if (ue:truthy $origin) $origin JsonNull)))
(: account:describe (-> %Undefined% %Undefined%))
(= (account:describe $plan)
   (let $metadata (call:request-metadata $plan)
     (case (lp:describe-origin (lp:field $metadata "id" "") (lp:field $metadata "model" "")
                        (lp:field $metadata "protocol" "") (lp:field $metadata "purpose" "turn")
                        "native" (account:request-origin $metadata) (system:wall-time))
       (((LedgerChanged $ignored4) True)
        ($error (let $ignored5 (py-call (native_llm_host.accounting_observation_error (call:host $plan) "metadata")) False))))))
(: account:submitted (-> %Undefined% %Undefined% %Undefined%))
; A submitted turn or structured call starts its health record; a compaction
; is maintenance and leaves it alone.
(= (account:submitted $plan $id)
   (let* (($started (if (call:compaction? $plan) 1
                      (health:start (call:fact $plan "provider" "unknown") (call:fact $plan "wire_model" ""))))
          ($ignored6 (py-call (native_llm_host.submitted (call:host $plan) $id)))
          ($native (call:submitted $plan $id))
          ($metadata (account:metadata $plan)))
     (case (nl:submitted $metadata $id (system:wall-time))
       (
        ((LedgerChanged $changed) 1)
        ($error (py-call (native_llm_host.accounting_observation_error (call:host $plan) "submitted")))))))
(: account:progress (-> %Undefined% %Undefined% %Undefined% %Undefined%))
; The owner paces its own records; host observers hear only due records.
(= (account:progress $plan $bytes $quiet)
   (case (call:progress-due $plan)
     (((True $soft)
       (let* (($health (if (call:compaction? $plan) 1
                         (health:progress $bytes $quiet $soft (call:fact $plan "timeout" 0))))
              ($_ (py-call (native_llm_host.progress_observed (call:host $plan) $bytes $quiet $soft)))
              ($metadata (account:metadata $plan)))
         (case (nl:progress $metadata $bytes $quiet (lp:field $metadata "progress_soft" 0) (system:wall-time))
           (((LedgerChanged $changed) 1)
            ($error (py-call (native_llm_host.accounting_observation_error (call:host $plan) "progress")))))))
      ((False $soft) 1))))
(: account:failed (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
; The host reports the failure with the owner's retry-after (-1 for none).
; A deterministic rejection of a command turn latches the rejection gate first:
; the host may hold its block in memory only.
; A failed command turn comes back as its facts, which the native owner paces
; and records (src/turn_health_cetta.metta).
(= (account:failed $plan $correlation $kind $status)
   (let* (($latched (if (account:rejection? $plan $kind $status) (gate:latch) False))
          ($ended (display:call-final (call:correlation $plan) none (lp:text $kind))))
     (health:paced (account:failure $plan $correlation $kind $status))))
(= (account:rejection? $plan $kind $status)
   (if (== $kind http-status)
     (if (call:command? $plan) (if (call:compaction? $plan) False (gate:invalid-request? $status)) False)
     False))
(= (account:failure $plan $correlation $kind $status)
   (if (account:ready $plan)
     (let* (($metadata (account:metadata $plan))
            ($retry (lp:field $metadata "retry_after" JsonNull))
            ($seconds (if (== $retry JsonNull) -1 $retry)))
       (case (nl:fail $metadata (lp:text $kind) (if (ue:equal $status 0) JsonNull $status)
                      $retry (lp:field $metadata "soft_elapsed" 0) (system:wall-time))
         (((LedgerError $ignored10)
           (let $ignored11 (py-call (native_llm_host.accounting_observation_error (call:host $plan) "failure"))
             (py-call (native_llm_host.failed (call:host $plan) $correlation $kind $status $seconds))))
          ($settled (py-call (native_llm_host.failed (call:host $plan) $correlation $kind $status $seconds))))))
     (py-call (native_llm_host.failed (call:host $plan) $correlation $kind $status -1))))
; A command whose output the contract rejected: the ledger records the code
; the native classification gave, after the completion it belongs to.
(: account:note-rejection (-> %Undefined% %Undefined% %Undefined%))
(= (account:note-rejection $plan $decision)
   (case $decision
     (((CommentaryRejectedV1 $code)
       (let $logged (system:write stderr (account:rejection-line $plan (lp:text $code)))
         (case (nl:command-output-rejected (account:metadata $plan) (lp:text $code) (system:wall-time))
           (((LedgerError $ignored12) (py-call (native_llm_host.accounting_observation_error (call:host $plan) "rejection")))
            ($recorded 1)))))
      ($other 1))))
; The operator's line for a rejected command output: its code and whether the
; prompt showed the rejection feedback and the required-output footer.
(: account:rejection-line (-> %Undefined% %Undefined% %Undefined%))
(= (account:rejection-line $plan $code)
   (let* (($known (or (== $code "incomplete-output") (or (== $code "invalid-native-tool")
                                                         (== $code "refused-or-structured-output"))))
          ($name (if $known $code "invalid-envelope"))
          ($feedback (if (== (call:fact $plan "feedback_visible" false) true) "true" "false"))
          ($footer (if (== (call:fact $plan "footer_visible" false) true) "true" "false")))
     (str:join "" ("[command_contract] rejected; code=" $name "; feedback_visible=" $feedback
                   "; footer_visible=" $footer "\n"))))
; The completion's effects, recorded once: its observation, the calibration
; of the model the call captured, and an Anthropic call's usage tally. A
; compaction is maintenance: it is tallied only. No effect can fail delivery.
(: account:effects (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:effects $plan $legacy $usage)
   (let* (($maintenance (call:compaction? $plan))
          ($observed (if $maintenance True
                       (catch (effects:completion (json:parse $legacy) (call:fact $plan "prompt_layout" JsonNull)
                                                  (call:context $plan) (ue:evidence $usage)))))
          ($bytes (call:fact $plan "prompt_bytes" 0))
          ($calibrated (if (or $maintenance (not (> $bytes 0))) True
                         (catch (effects:calibrate $bytes (effects:object-or-empty $usage) (call:fact $plan "selected_model" "")))))
          ($tallied (if (== (call:fact $plan "provider" "") "anthropic")
                      (catch (effects:tally (effects:object-or-empty $usage))) True)))
     True))
; The committed completion is published once: its effects, the host's program
; release, the display's end, the turn's health, then the answer. A replayed
; completion publishes nothing. The host's decision ends the turn's phase: a
; released program completes it; a rejected or empty output comes back as
; failure facts, paced and recorded natively.
(: account:publish (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:publish $plan $epoch $correlation $legacy $canonical $usage $decision)
   (if (call:publication-ready $plan)
    (let* (($effects (account:effects $plan $legacy $usage))
           ($released (py-call (native_llm_host.finish (call:host $plan) $legacy)))
           ($ended (account:displayed $plan $legacy $decision $released))
           ($answer (account:health $released))
           ($ignored14 (account:note-rejection $plan $decision)))
      (if (call:replies? $plan) (call:reply $plan $legacy) $answer))
    (py-call (native_llm_host.completion_duplicate (call:host $plan)))))
; The display's end of a published completion: a compaction's, a structured
; call's, a released command's or a rejected command output's. A command
; whose output was empty leaves its attempt as the host's finish did.
(= (account:displayed $plan $legacy $decision $released)
   (let $attempt (call:correlation $plan)
     (if (call:compaction? $plan) (display:call-final $attempt none (display:result-reason $legacy))
       (case $decision
         (((CommentaryRejectedV1 $code) (display:call-final $attempt (display:result-message $legacy) "rejected"))
          ($accepted
           (case $released
             ((("completed" $program $bytes)
               (display:call-final $attempt (display:result-message $legacy) (display:result-reason $legacy)))
              (("completed" $bytes)
               (display:call-final $attempt (display:result-message $legacy) (display:result-reason $legacy)))
              ($failure (display:call-drop $attempt))))))))))
(= (account:health $decision)
   (case $decision
     ((("completed" $program $bytes)
       (let* (($recorded (health:complete $bytes)) ($answered (health:answered)) ($phase (health:turn-completed)))
         $program))
      (("completed" $bytes) (let $recorded (health:complete $bytes) 1))
      ($other (health:paced $other)))))
(: account:finished (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:finished $plan $metadata $epoch $correlation $legacy $canonical $usage $decision $answer)
   (account:finished-step $plan $metadata $epoch $correlation $legacy $canonical $usage $decision 0 $answer))
(: account:finished-step (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:finished-step $plan $metadata $epoch $correlation $legacy $canonical $usage $decision $attempt $answer)
   (case $answer
     (((LedgerChanged True) (account:publish $plan $epoch $correlation $legacy $canonical $usage $decision))
      ((LedgerChanged False) (account:reconcile $plan $metadata $epoch $correlation $legacy $canonical $usage $decision $attempt))
      ((LedgerInvalid $invalid) (account:failed $plan $correlation invalid-native-evidence 0))
      ((LedgerError $storage) (account:reconcile $plan $metadata $epoch $correlation $legacy $canonical $usage $decision $attempt))
      ($invalid (health:paced (py-call (native_llm_host.completion_conflict (call:host $plan))))))))

; Keep the received answer in this owned continuation. Local write/read errors
; never escape into a caller's provider-retry loop, and never release the lease.
(: account:reconcile (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:reconcile $plan $metadata $epoch $correlation $legacy $canonical $usage $decision $attempt)
   (case (nl:completion-state $metadata (lp:text $canonical))
     (((LedgerCompletion committed) (account:publish $plan $epoch $correlation $legacy $canonical $usage $decision))
      ((LedgerCompletion pending)
       (let* (($wait (account:recording-wait $plan $attempt))
              ($reason (lp:field (account:first-choice (json:parse $legacy)) "finish_reason" "unknown")))
         (account:finished-step $plan $metadata $epoch $correlation $legacy $canonical $usage $decision (+ $attempt 1)
           (nl:complete $metadata $usage $reason (lp:text $canonical)
                        (lp:field $metadata "soft_elapsed" 0) (system:wall-time)))))
      ((LedgerError $unreadable)
       (let $wait (account:recording-wait $plan $attempt)
         (account:reconcile $plan $metadata $epoch $correlation $legacy $canonical $usage $decision (+ $attempt 1))))
      ($conflict (health:paced (py-call (native_llm_host.completion_conflict (call:host $plan))))))))
; A received answer waits for local storage: the health record says so, and
; the host paces the retry.
(= (account:recording-wait $plan $attempt)
   (let $recorded (if (call:compaction? $plan) 1 (health:record-wait $attempt))
     (py-call (native_llm_host.completion_wait (call:host $plan) $attempt))))
; $decision is the command contract's classification of this output, or
; CommentaryNotAppliedV1 when the contract does not apply to the call.
(: account:complete (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:complete $plan $epoch $correlation $legacy $canonical)
   (account:complete-classified $plan $epoch $correlation $legacy $canonical CommentaryNotAppliedV1))
(: account:complete-classified (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (account:complete-classified $plan $epoch $correlation $legacy $canonical $decision)
   (let* (($metadata (account:metadata $plan)) ($result (json:parse $legacy))
          ($choice (account:first-choice $result))
          ($reason (lp:field $choice "finish_reason" "unknown"))
          ($usage (lp:field $result "usage" (JsonObject ())))
          ($message (lp:field $choice "message" (JsonObject ()))))
     (if (or (not (ue:equal (lp:text $epoch) (lp:field $metadata "epoch" "")))
             (not (ue:equal (lp:text $correlation) (lp:field $metadata "id" ""))))
       (account:failed $plan $correlation correlation-error 0)
       (if (not (or (ue:equal $reason "stop") (or (ue:equal $reason "tool_calls")
                     (or (ue:equal $reason "length") (or (ue:equal $reason "refusal") (ue:equal $reason "content_filter"))))))
         (account:failed $plan $correlation unfinished-provider-response 0)
         (if (and (or (ue:equal $reason "refusal") (ue:equal $reason "content_filter"))
                  (ue:truthy (lp:field $message "tool_calls" JsonNull)))
           (account:failed $plan $correlation refused-tool-call 0)
           (account:finished $plan $metadata $epoch $correlation $legacy $canonical $usage $decision
             (nl:complete $metadata $usage $reason (lp:text $canonical)
                          (lp:field $metadata "soft_elapsed" 0) (system:wall-time))))))))
(: account:first-choice (-> %Undefined% %Undefined%))
(= (account:first-choice $result)
   (case (lp:field $result "choices" (JsonArray ()))
     (((JsonArray $items) (if (ue:equal $items ()) (JsonObject ()) (car-atom $items)))
      ($other (JsonObject ())))))
