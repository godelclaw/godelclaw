; Provider transport, command preparation and policy transactions run in CeTTa.
; Every call is a native call owner (src/native_call_cetta.metta): its facts,
; prepared transport and phase are native. The host plan is only the handle of
; the remaining host observers (display, health, completion effects).
!(import! &self ./src/native_llm_host.py)
!(import! &self ./src/interop_flags_cetta.metta)
!(import! &self ./src/model_selection_cetta.metta)
!(import! &self ./src/native_call_cetta.metta)
!(import! &self ./src/rejection_gate_cetta.metta)
!(import! &self ./src/turn_health_cetta.metta)
; A flag consumes a computed value. Atom would treat an inline host call as
; quoted syntax and silently classify it as false.
(: llm:host-flag (-> %Undefined% Bool))
(= (llm:host-flag $value)
   (host:flag $value))
(= (llm:native?)
   (== (system:getenv-or-default "METTACLAW_LLM_TRANSPORT" "python") "native"))
!(if (llm:native?)
     (let* (($command-module (import! &self llm:command))
            ($compat-module (import! &self llm:compat))
            ($io-module (import! &self llm:io))
            ($transport-module (import! &self ./src/native_transport_cetta.metta))
            ($policy-module (import! &self ./src/llm_accounting_cetta.metta))
            ($commentary-module (import! &self ./src/model_commentary_cetta.metta))
            ($responses-module (import! &self ./src/responses_stream_cetta.metta))
            ($chatgpt-module (import! &self ./src/chatgpt_agent_cetta.metta)))
       (let $_ (import! &self ./src/provider_call_cetta.metta)
         (import! &self ./src/stream_runtime.metta))) True)

; Providers report each model's window. The host reads Synthetic's catalog and
; Anthropic's model metadata; the ChatGPT list needs this runtime's sign-in, so
; only a selected chatgpt: model asks the host whether its window is due.
; The host's answer crosses as a Symbol: compare its text, so that no other
; provider's model ever reaches the ChatGPT list.
(= (llm:observe-window)
   (if (and (llm:native?) (str:starts-with (model:current syn:large:text) "chatgpt:"))
     (let $model (format-args "{}" ((py-call (synthetic_llm.model_window_due))))
       (if (== $model "") True (chatgpt:observe-window $model)))
     True))

; A retained rejection is the host's to release; the fast path asks it only
; when a block is possible (src/rejection_gate_cetta.metta).
(= (llm:request-allowed?)
   (if (gate:possible?)
     (if (llm:host-flag (py-call (native_llm_host.request_allowed))) (gate:release) False)
     True))
(= (llm:command $model $tokens $effort $prompt)
   (if (not (llm:request-allowed?)) "()"
    (if (llm:native?)
     (let $selected (model:current $model)
       (if (str:starts-with $selected "chatgpt:")
         (chatgpt:command $selected $tokens $effort $prompt)
         (let $plan (pcall:command-plan $selected $tokens $effort $prompt) (llm:run $plan))))
     (let $selected (model:current $model)
       (if (str:starts-with $selected "chatgpt:")
         (py-call (synthetic_llm.unavailable_action chatgpt-requires-native-cetta))
         (llm:host-command $model $tokens $effort $prompt))))))

; The Python transport keeps the health record itself. Its turn completed
; exactly when the record counted one more completion: the durable receipt,
; not the host's word, ends the phase.
(= (llm:host-command $model $tokens $effort $prompt)
   (let* (($before (health:completions))
          ($answer (py-call (synthetic_llm.chat $model $tokens $effort $prompt)))
          ($phase (if (> (health:completions) $before) (health:turn-completed) (health:turn-failed))))
     $answer))

; A structured (coding or iter) request is a tagged JSON value; its purpose is
; the ledger's: "turn" (iter), "coding" or "compaction". Preparation captures
; both, so a plan is never retargeted after it exists.
(= (llm:structured-plan $request $purpose)
   (let $selected (model:current syn:large:text)
     (if (str:starts-with $selected "chatgpt:")
       (if (== $purpose "turn") (Error llm:structured-plan chatgpt-command-mode-only)
         (let $session (chatgpt:session)
           (case $session
             (((ChatGPTUnavailable $why) (Error llm:structured-plan $why))
              ($ready (chatgpt:structured-plan-purpose $selected $request $ready $purpose))))))
       (pcall:structured-plan $selected $request $purpose))))

; The reply of a structured call, written as JSON where its caller keeps it
; (atomically) and returned as a value. It never crosses the host.
(= (llm:reply-file (ProviderReplyV1 $reply) $path) (call:write-reply $reply $path))

(: llm:owner (-> %Undefined% %Undefined%))
(= (llm:owner $plan)
   (case $plan (((PreparedProviderCallV1 $owner $transport) $owner)
                ((RefusedProviderCallV1 $host) $host))))
(= (llm:plan-ready $plan)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (call:ready? $owner))
      ((RefusedProviderCallV1 $host) False)
      ($owner (call:ready? $owner)))))

; A refused preparation never claims, admits or submits; it settles as the
; failure the host reports for an unadmitted call.
(= (llm:run $plan)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport)
       (let $state (pcall:begin $owner $transport) (llm:after-submit $owner $state)))
      ((RefusedProviderCallV1 $host) (llm:refused $host)))))
(= (llm:refused $host)
   (let $phase (health:turn-failed)
     (py-call (native_llm_host.failed $host "unsubmitted" admission-error 0))))

; Programs may retain this owned pending value, poll it later, or wait for a
; bounded interval. A soft wait returns the same call; it never cancels it.
(= (llm:begin $plan)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (pcall:begin $owner $transport))
      ((RefusedProviderCallV1 $host) (llm:v1:failed unsubmitted unsubmitted admission-error not-started)))))

(: llm:network-ready (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:network-ready $plan $epoch $correlation $protocol $url $headers $wire $timeout $cap
                     (NativeCallWaitsV1 $connect $first $idle))
   (let $described (account:describe $plan)
     (if $described
      (let $submitted (llm:submit-http $url $headers $wire $timeout $cap $connect $first $idle)
     (case $submitted
       (((io:pending $id)
         (let $_ (account:submitted $plan $id)
           (llm:v1:pending $epoch $correlation $protocol $id)))
        ($other (llm:v1:failed $epoch $correlation submit-error not-started)))))
      (llm:v1:failed $epoch $correlation native-accounting-error not-started))))

; Without capture control, the final body stays in CeTTa.
(= (llm:observe-wire $plan $body)
   (let $health (system:getenv-or-default "METTACLAW_COGNITIVE_HEALTH_PATH" "")
     (if (== $health "") $body
       (let $control (path:join (path:dirname $health) "request-capture/control.json")
         (case (catch (fs:is-file $control))
           ((True (py-call (native_llm_host.observe_wire $plan $body)))
            ($disabled $body)))))))

; Callers hold the prepared call, its owner, or a refused preparation.
(: llm:after-submit (-> Atom Atom %Undefined%))
(= (llm:after-submit $plan $state)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (llm:after-owned $owner $state))
      ((RefusedProviderCallV1 $host) (llm:refused $host))
      ($owner (llm:after-owned $owner $state)))))
(: llm:after-owned (-> Atom Atom %Undefined%))
(= (llm:after-owned $plan $state)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id)
        (llm:await $plan $state))
      ((llm:v1:failed duplicate duplicate duplicate-plan not-started)
        (py-call (native_llm_host.duplicate_plan (call:host $plan))))
      ((llm:v1:rejected $reason) (py-call (native_llm_host.duplicate_plan (call:host $plan))))
      ($other (llm:deliver $plan $state)))))

(: llm:await (-> Atom Atom %Undefined%))
(= (llm:await $plan $state)
   (let $next (llm:wait-once $plan $state 250)
     (llm:continue $plan $next)))

(: llm:poll-once (-> Atom Atom %Undefined%))
(= (llm:poll-once $plan $state) (llm:wait-once $plan $state 0))

(: llm:wait-once (-> Atom Atom Number %Undefined%))
(= (llm:wait-once $plan $state $milliseconds)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (llm:wait-owned $owner $state $milliseconds))
      ((RefusedProviderCallV1 $host) $state)
      ($owner (llm:wait-owned $owner $state $milliseconds)))))
(: llm:wait-owned (-> Atom Atom Number %Undefined%))
(= (llm:wait-owned $plan $state $milliseconds)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id)
       (if (call:owns? $plan $epoch $correlation $id)
        (let* (($status (llm:status-http $id))
              ($progressed (llm:progress $plan $status))
              ($displayed (stream:progress $plan $id))
              ($observed (llm:commentary-progress $plan $id))
              ($event (llm:wait-http $id $milliseconds)))
         (llm:observe-plan $plan $state $epoch $correlation $protocol $id $event))
        (llm:v1:rejected ownership-error)))
      ($terminal $terminal))))

(: llm:progress (-> Atom Atom %Undefined%))
(= (llm:progress $plan $status)
   (case $status
     (((io:progress $id $phase $age $bytes $quiet)
       (account:progress $plan $bytes $quiet))
      ($other 1))))

(: llm:observe-plan (-> Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:observe-plan $plan $state $epoch $correlation $protocol $id $event)
   (case $event
     (((io:idle) $state)
      ((io:event $received (http:response-with-metadata $status $body (http:retry-after $seconds)))
       (if (== $id $received)
         (let $native-retry (call:retry-after $plan $seconds)
           (llm:observe-plan $plan $state $epoch $correlation $protocol $id
             (io:event $received (http:response $status $body))))
         $state))
      ((io:event $received (http:response $status $body))
       (if (== $id $received)
         (if (and (>= $status 200) (< $status 300))
           (llm:decode-body $plan $epoch $correlation $protocol $body)
           ; Keep the provider's explanation, as Codex shows it.
           (let $_ (py-call (native_llm_host.provider_error (call:host $plan) $body))
             (llm:v1:failed $epoch $correlation (http-status $status) response-received)))
         $state))
      ((io:event $received (NativeHttpFailureV1 $kind $code))
       (if (== $id $received)
         (let $_ (py-call (native_llm_host.transport_failure (call:host $plan) $kind $code))
           (llm:v1:failed $epoch $correlation transport-error remote-outcome-unknown))
         $state))
      ($other (llm:v1:failed $epoch $correlation transport-error remote-outcome-unknown)))))

(: llm:decode-body (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:decode-body $plan $epoch $correlation $protocol $body)
   (if (== $protocol "responses")
     (case (responses:body $body)
       (((ResponsesComplete $response)
         (let $rendered (llm:v1:render $response)
           (llm:v1:io-outcome $epoch $correlation $protocol (http:response 200 $rendered))))
        ((ResponsesStreamError $why) (llm:v1:failed $epoch $correlation $why response-received))))
     (let ($stream $limit) (call:decode-config $plan)
       (llm:decode-received-body $plan $epoch $correlation $protocol $body (llm:host-flag $stream) $limit 0))))

; A local decoder allocation refusal retains this body. Re-entering transport
; here would confuse a local resource failure with an unknown remote outcome.
(: llm:decode-received-body (-> Atom Atom Atom Atom Atom Bool Number Number %Undefined%))
(= (llm:decode-received-body $plan $epoch $correlation $protocol $body $stream $limit $attempt)
   (case (llm:v1:response-body $protocol $stream $body $limit)
     (((llm:v1:ok $text)
       (llm:v1:io-outcome $epoch $correlation $protocol (http:response 200 $text)))
      ((llm:v1:error stream-capacity)
       (let* (($recorded (if (call:compaction? $plan) 1 (health:record-wait $attempt)))
              ($_ (py-call (native_llm_host.decoding_wait (call:host $plan) $attempt))))
         (llm:decode-received-body $plan $epoch $correlation $protocol $body $stream $limit (+ $attempt 1))))
      ((llm:v1:error $why)
       (llm:v1:failed $epoch $correlation incomplete-stream response-received))
      ($other (llm:v1:failed $epoch $correlation invalid-decode-result response-received)))))

(: llm:continue (-> Atom Atom %Undefined%))
(= (llm:continue $plan $state)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id) (llm:await $plan $state))
      ($terminal (llm:deliver $plan $terminal)))))

(: llm:deliver (-> Atom Atom %Undefined%))
(= (llm:deliver $plan $state)
   (let* (($closed (stream:close (call:correlation $plan)))
          ($answer (llm:deliver-final $plan $state))
          ($released (call:release $plan)))
     $answer))

(: llm:deliver-final (-> Atom Atom %Undefined%))
(= (llm:deliver-final $plan $state)
   (case $state
     (((llm:v1:completed $epoch $correlation $result)
        (let $projected (llm:v1:project-chat-value $result 0)
          (case $projected
            (((llm:v1:ok $legacy)
              (let* (($canonical (llm:v1:render-outcome $result))
                     ($encoded (llm:v1:render-outcome $legacy)))
                (llm:finish-rendered $plan $epoch $correlation $encoded $canonical)))
             ((llm:v1:error $code) (llm:failed-plan $plan $correlation $code 0))))))
      ((llm:v1:failed $epoch $correlation (http-status $status) $certainty)
        (llm:failed-plan $plan $correlation http-status $status))
      ((llm:v1:failed $epoch $correlation $reason $certainty)
        (llm:failed-plan $plan $correlation $reason 0))
      ($other (llm:failed-plan $plan unknown invalid-phase 0)))))
(= (llm:failed-plan $plan $correlation $kind $status)
   (account:failed $plan $correlation $kind $status))

(: llm:finish-rendered (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:finish-rendered $plan $epoch $correlation $encoded $canonical)
   (case $encoded
     (((llm:v1:ok $legacy-text)
       (case $canonical
         (((llm:v1:ok $canonical-text)
           (let $decision (commentary:prepare $plan $legacy-text)
             (account:complete-classified $plan $epoch $correlation $legacy-text $canonical-text $decision)))
          ((llm:v1:error $code) (llm:failed-plan $plan $correlation $code 0)))))
      ((llm:v1:error $other-code) (llm:failed-plan $plan $correlation $other-code 0)))))

 ; Native error boundaries use CeTTa's generated language spellings.
(: llm:submit-http (-> Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:submit-http $url $headers $body $hard $cap $connect $first $idle)
   (io:try-submit (http:request "POST" $url $headers $body $hard $cap False
                              (http:timeouts $connect $first $idle retry-after))))
(: llm:status-http (-> Atom %Undefined%))
(= (llm:status-http $id) (io:try-status $id))
(: llm:wait-http (-> Atom Number %Undefined%))
(= (llm:wait-http $id $milliseconds) (llm-transport:wait $id $milliseconds))

; Only a command turn with commentary on has an observer to feed.
(= (llm:commentary-observed? $owner)
   (and (commentary:enabled?) (and (call:command? $owner) (not (call:compaction? $owner)))))
; Optional owned text observation. Only inert chunk values cross the portable
; function/return error boundary. Host publication runs outside that boundary.
(= (llm:commentary-progress $owner $id)
   (if (not (llm:commentary-observed? $owner)) 1
   (let* (($plan (call:host $owner)) ($readable (py-call (native_llm_host.commentary_readable $plan))))
     (if (llm:host-flag $readable)
       (let* (($offset (py-call (native_llm_host.commentary_offset $plan)))
              ($chunk (llm:commentary-read $id $offset)))
         (case $chunk
           (((http:chunk $next $text $phase)
             (py-call (native_llm_host.commentary_chunk $plan $next $text $phase)))
            ($other (py-call (native_llm_host.commentary_unavailable $plan)))))) 1))))
(= (llm:commentary-read $id $offset)
   (function (case (io:http-read $id $offset 65536)
     (((http:chunk $next $text $phase) (return (http:chunk $next $text $phase)))
      ($other (return (io:no-prefix)))))))
