; Provider transport, command preparation and policy transactions run in CeTTa.
; Host lifecycle observations and credential configuration remain boundaries.
!(import! &self ./src/native_llm_host.py)
!(import! &self ./src/interop_flags_cetta.metta)
!(import! &self ./src/model_selection_cetta.metta)
; A flag consumes a computed value. Atom would treat an inline host call as
; quoted syntax and silently classify it as false.
(: llm:host-flag (-> %Undefined% Bool))
(= (llm:host-flag $value)
   (host:flag $value))
(: llm:command-kind? (-> Atom Bool))
(= (llm:command-kind? $value)
   (case $value ((command True) ("command" True) ($other False))))
(: llm:messages-encoding? (-> Atom Bool))
(= (llm:messages-encoding? $value)
   (case $value ((messages True) ("messages" True) ($other False))))
; Janus and CeTTa both expose one-level standard-library callables. Avoid a
; dotted builtin method name, which Janus treats as a missing Python builtin.
(= (llm:subscription-model? $selected)
   (let* (($method (py-call (builtins.getattr $selected "startswith")))
          ($value (py-call (operator.call $method "chatgpt:"))))
     (llm:host-flag $value)))
(= (llm:native?)
   (let $value (py-call (native_llm_host.enabled)) (llm:host-flag $value)))
(= (llm:plan-ready $plan)
   (let $value (py-call (native_llm_host.field $plan "ready")) (llm:host-flag $value)))
(= (llm:uses-native-ledger $plan)
   (llm:host-flag (py-call (native_llm_host.native_accounting $plan))))
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

(= (llm:plan-claim $plan)
   (if (llm:uses-native-ledger $plan) (account:claim $plan)
     (let $value (py-call (native_llm_host.claim $plan)) (llm:host-flag $value))))

; Providers report each model's window. The host reads Synthetic's catalog and
; Anthropic's model metadata; the ChatGPT list needs this runtime's sign-in.
; The host's answer crosses as a Symbol: compare its text, so that no other
; provider's model ever reaches the ChatGPT list.
(= (llm:observe-window)
   (if (llm:native?)
     (let $model (format-args "{}" ((py-call (synthetic_llm.model_window_due))))
       (if (== $model "") True (chatgpt:observe-window $model)))
     True))

(= (llm:command $model $tokens $effort $prompt)
   (if (not (llm:host-flag (py-call (native_llm_host.request_allowed)))) "()"
    (if (llm:native?)
     (let $selected (model:current $model)
       (if (str:starts-with $selected "chatgpt:")
         (chatgpt:command $selected $tokens $effort $prompt)
         (let $plan (pcall:command-plan $selected $tokens $effort $prompt) (llm:run $plan))))
     (let* (($selected (model:current $model))
            ($subscription (llm:subscription-model? $selected)))
       (if $subscription
         (py-call (synthetic_llm.unavailable_action chatgpt-requires-native-cetta))
         (py-call (synthetic_llm.chat $model $tokens $effort $prompt)))))))

(= (llm:structured $request)
   (let* (($selected (model:current syn:large:text))
          ($subscription (llm:subscription-model? $selected)))
     (if $subscription (Error llm:structured chatgpt-command-mode-only)
       (let $plan (py-call (native_llm_host.native_structured_plan $request)) (llm:run $plan)))))

(= (llm:structured-json-plan $text)
   (let $selected (model:current syn:large:text)
     (if (str:starts-with $selected "chatgpt:")
       (let $session (chatgpt:session)
         (case $session
           (((ChatGPTUnavailable $why) (Error llm:structured-json-plan $why))
            ($ready (chatgpt:structured-plan $selected (json-parse $text) $ready)))))
       (py-call (native_llm_host.native_structured_json_plan $text)))))

(: llm:owner (-> Atom %Undefined%))
(= (llm:owner $plan)
   (case $plan (((PreparedProviderCallV1 $owner $transport) $owner) ($legacy $legacy))))

(= (llm:run $plan)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport)
       (let $state (pcall:begin $owner $transport) (llm:after-submit $owner $state)))
      ($legacy (let $state (llm:begin $plan) (llm:after-submit $plan $state))))))

; Programs may retain this owned pending value, poll it later, or wait for a
; bounded interval. A soft wait returns the same call; it never cancels it.
(= (llm:begin $plan)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (pcall:begin $owner $transport))
      ($legacy (llm:begin-owned $plan)))))
(= (llm:begin-owned $plan)
   (if (llm:plan-ready $plan)
    (if (llm:plan-claim $plan)
     (let* (($_ (py-call (native_llm_host.gate)))
            ($config (llm:configuration $plan)))
       (llm:configured-begin $plan $config))
     (if (llm:plan-ready $plan) (llm:v1:failed duplicate duplicate duplicate-plan not-started)
       (llm:v1:failed unsubmitted unsubmitted admission-error not-started)))
     (llm:v1:failed unsubmitted unsubmitted admission-error not-started)))

; Configuration is ordinary native data. It is not a claim or an I/O owner.
; Consume the request/encoding fields here; later phases carry only the
; prepared wire and native timeout description they actually need.
(= (llm:configuration $plan)
   (let ($epoch $correlation $protocol $base $headers $request $kind $encoding
         $timeout $cap $connect $first $idle)
        (py-call (native_llm_host.transport_config $plan))
     (NativeCallConfigV1 $epoch $correlation $protocol $base $headers $request
                         $kind $encoding $timeout $cap $connect $first $idle)))
(: llm:configured-begin (-> Atom Atom %Undefined%))
(= (llm:configured-begin $plan
     (NativeCallConfigV1 $epoch $correlation $protocol $base $headers-json $request
                         $kind $encoding $timeout $cap $connect $first $idle))
   (let* (($headers-outcome (llm:v1:headers-json $headers-json))
          ($encoded (if (and (llm:command-kind? $kind) (not (llm:messages-encoding? $encoding)))
                       (llm:v1:encode-command $protocol $request)
                       (llm:v1:encode $protocol $request)))
          ($name (llm:v1:protocol $protocol))
          ($waits (NativeCallWaitsV1 $connect $first $idle)))
     (llm:start-plan $plan $epoch $correlation $name $base $headers-outcome $encoded $timeout $cap $waits)))

(: llm:start-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:start-plan $plan $epoch $correlation $protocol $base $headers-outcome $encoded $timeout $cap $waits)
   (case $headers-outcome
     (((llm:v1:ok $headers)
       (llm:encode-plan $plan $epoch $correlation $protocol $base $headers $encoded $timeout $cap $waits))
      ((llm:v1:error $code) (llm:v1:failed $epoch $correlation $code not-started)))))

(: llm:encode-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:encode-plan $plan $epoch $correlation $protocol $base $headers $encoded $timeout $cap $waits)
   (case $encoded
     (((llm:v1:ok (llm:v1:encoded $path $body))
       (let $url (str:concat $base $path)
         (llm:network-plan $plan $epoch $correlation $protocol $url $headers $body $timeout $cap $waits)))
      ((llm:v1:error $code) (llm:v1:failed $epoch $correlation $code not-started)))))

(: llm:network-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:network-plan $plan $epoch $correlation $protocol $url $headers $body $timeout $cap
                     (NativeCallWaitsV1 $connect $first $idle))
   (let* (($legacy-wire (py-call (native_llm_host.wire_body $plan $body)))
          ($profiled (if (chatgpt:plan? $plan) (chatgpt:wire $legacy-wire) $legacy-wire))
          ($wire (llm:observe-wire $plan $profiled)))
     (llm:network-ready $plan $epoch $correlation $protocol $url $headers $wire $timeout $cap
                        (NativeCallWaitsV1 $connect $first $idle))))
(: llm:network-ready (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:network-ready $plan $epoch $correlation $protocol $url $headers $wire $timeout $cap
                     (NativeCallWaitsV1 $connect $first $idle))
   (let $described (if (llm:uses-native-ledger $plan) (account:describe $plan) True)
     (if $described
      (let $submitted (llm:submit-http $url $headers $wire $timeout $cap $connect $first $idle)
     (case $submitted
       (((io:pending $id)
         (let $_ (if (llm:uses-native-ledger $plan) (account:submitted $plan $id)
                   (py-call (native_llm_host.submitted $plan $id)))
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

(: llm:submit-headers (-> Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:submit-headers $epoch $correlation $protocol $base $headers-outcome $encoded $timeout $cap)
   (case $headers-outcome
     (((llm:v1:ok $headers)
       (llm:v1:submit-encoded $epoch $correlation $protocol $base $headers $encoded $timeout $cap))
      ((llm:v1:error $code) (llm:v1:failed $epoch $correlation $code not-started)))))

(: llm:after-submit (-> Atom Atom %Undefined%))
(= (llm:after-submit $plan $state)
   (case $plan
     (((PreparedProviderCallV1 $owner $transport) (llm:after-owned $owner $state))
      ($legacy (llm:after-owned $plan $state)))))
(: llm:after-owned (-> Atom Atom %Undefined%))
(= (llm:after-owned $plan $state)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id)
        (llm:await $plan $state))
      ((llm:v1:failed duplicate duplicate duplicate-plan not-started)
        (py-call (native_llm_host.duplicate_plan $plan)))
      ((llm:v1:rejected $reason) (py-call (native_llm_host.duplicate_plan $plan)))
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
      ($legacy (llm:wait-owned $plan $state $milliseconds)))))
(: llm:wait-owned (-> Atom Atom Number %Undefined%))
(= (llm:wait-owned $plan $state $milliseconds)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id)
       (let $owned (py-call (native_llm_host.owns $plan $epoch $correlation $id))
        (if (llm:host-flag $owned)
        (let* (($status (llm:status-http $id))
              ($_ (llm:progress $plan $status))
              ($_ (stream:progress $plan $id))
              ($_ (llm:commentary-progress $plan $id))
              ($event (llm:wait-http $id $milliseconds)))
         (llm:observe-plan $plan $state $epoch $correlation $protocol $id $event))
        (llm:v1:rejected ownership-error))))
      ($terminal $terminal))))

(: llm:progress (-> Atom Atom %Undefined%))
(= (llm:progress $plan $status)
   (case $status
     (((io:progress $id $phase $age $bytes $quiet)
       (if (llm:uses-native-ledger $plan) (account:progress $plan $bytes $quiet)
         (py-call (native_llm_host.waiting $plan $bytes $quiet))))
      ($other 1))))

(: llm:observe-plan (-> Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:observe-plan $plan $state $epoch $correlation $protocol $id $event)
   (case $event
     (((io:idle) $state)
      ((io:event $received (http:response-with-metadata $status $body (http:retry-after $seconds)))
       (if (== $id $received)
         (let $_ (py-call (native_llm_host.response_retry_after $plan $seconds))
                (llm:observe-plan $plan $state $epoch $correlation $protocol $id
                  (io:event $received (http:response $status $body))))
         $state))
      ((io:event $received (http:response $status $body))
       (if (== $id $received)
         (if (and (>= $status 200) (< $status 300))
           (llm:decode-body $plan $epoch $correlation $protocol $body)
           ; Keep the provider's explanation, as Codex shows it.
           (let $_ (py-call (native_llm_host.provider_error $plan $body))
             (llm:v1:failed $epoch $correlation (http-status $status) response-received)))
         $state))
      ((io:event $received (NativeHttpFailureV1 $kind $code))
       (if (== $id $received)
         (let $_ (py-call (native_llm_host.transport_failure $plan $kind $code))
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
     (llm:decode-legacy-body $plan $epoch $correlation $protocol $body)))

(: llm:decode-legacy-body (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:decode-legacy-body $plan $epoch $correlation $protocol $body)
   (case (py-call (native_llm_host.response_decode_config $plan))
     ((($stream $limit)
       (let $enabled (llm:host-flag $stream)
         (llm:decode-received-body $plan $epoch $correlation $protocol $body $enabled $limit 0)))
      ($other (llm:v1:failed $epoch $correlation invalid-decode-config response-received)))))

; A local decoder allocation refusal retains this body. Re-entering transport
; here would confuse a local resource failure with an unknown remote outcome.
(: llm:decode-received-body (-> Atom Atom Atom Atom Atom Bool Number Number %Undefined%))
(= (llm:decode-received-body $plan $epoch $correlation $protocol $body $stream $limit $attempt)
   (case (llm:v1:response-body $protocol $stream $body $limit)
     (((llm:v1:ok $text)
       (llm:v1:io-outcome $epoch $correlation $protocol (http:response 200 $text)))
      ((llm:v1:error stream-capacity)
       (let $_ (py-call (native_llm_host.decoding_wait $plan $attempt))
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
   (let* (($display-id (py-call (native_llm_host.field $plan "correlation")))
          ($_ (stream:close $display-id)))
     (llm:deliver-final $plan $state)))

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
   (if (llm:uses-native-ledger $plan) (account:failed $plan $correlation $kind $status)
     (py-call (native_llm_host.failed $plan $correlation $kind $status))))

(: llm:finish-rendered (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:finish-rendered $plan $epoch $correlation $encoded $canonical)
   (case $encoded
     (((llm:v1:ok $legacy-text)
       (case $canonical
         (((llm:v1:ok $canonical-text)
           (let $_ (commentary:prepare $plan $legacy-text)
             (if (llm:uses-native-ledger $plan)
               (account:complete $plan $epoch $correlation $legacy-text $canonical-text)
               (py-call (native_llm_host.finish $plan $epoch $correlation $legacy-text $canonical-text)))))
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

 ; Optional owned text observation. Only inert chunk values cross the portable
; function/return error boundary. Host publication runs outside that boundary.
(= (llm:commentary-progress $plan $id)
   (let $readable (py-call (native_llm_host.commentary_readable $plan))
     (if (llm:host-flag $readable)
       (let* (($offset (py-call (native_llm_host.commentary_offset $plan)))
              ($chunk (llm:commentary-read $id $offset)))
         (case $chunk
           (((http:chunk $next $text $phase)
             (py-call (native_llm_host.commentary_chunk $plan $next $text $phase)))
            ($other (py-call (native_llm_host.commentary_unavailable $plan)))))) 1)))
(= (llm:commentary-read $id $offset)
   (function (case (io:http-read $id $offset 65536)
     (((http:chunk $next $text $phase) (return (http:chunk $next $text $phase)))
      ($other (return (io:no-prefix)))))))
