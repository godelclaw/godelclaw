; Provider transport and policy transactions run in CeTTa. Model selection,
; credential packaging and upstream transformations remain host boundaries.
!(import! &self ./src/native_llm_host.py)
!(import! &self ./src/interop_flags_cetta.metta)
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
            ($policy-module (import! &self ./src/llm_accounting_cetta.metta))
            ($responses-module (import! &self ./src/responses_stream_cetta.metta))
            ($chatgpt-module (import! &self ./src/chatgpt_agent_cetta.metta)))
       (import! &self ./src/stream_runtime.metta)) True)

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
     (let $selected (py-call (synthetic_llm.current_model $model))
       (if (str:starts-with $selected "chatgpt:")
         (chatgpt:command $selected $tokens $effort $prompt)
         (let $plan (py-call (native_llm_host.native_command_plan $model $tokens $effort $prompt)) (llm:run $plan))))
     (let* (($selected (py-call (synthetic_llm.current_model $model)))
            ($subscription (llm:subscription-model? $selected)))
       (if $subscription
         (py-call (synthetic_llm._empty_action chatgpt-requires-native-cetta))
         (py-call (synthetic_llm.chat $model $tokens $effort $prompt)))))))

(= (llm:structured $request)
   (let* (($selected (py-call (synthetic_llm.current_model)))
          ($subscription (llm:subscription-model? $selected)))
     (if $subscription (Error llm:structured chatgpt-command-mode-only)
       (let $plan (py-call (native_llm_host.native_structured_plan $request)) (llm:run $plan)))))

(= (llm:structured-json-plan $text)
   (let $selected (py-call (synthetic_llm.current_model))
     (if (str:starts-with $selected "chatgpt:")
       (let $session (chatgpt:session)
         (case $session
           (((ChatGPTUnavailable $why) (Error llm:structured-json-plan $why))
            ($ready (chatgpt:structured-plan $selected (json-parse $text) $ready)))))
       (py-call (native_llm_host.native_structured_json_plan $text)))))

(= (llm:run $plan)
   (let $state (llm:begin $plan) (llm:after-submit $plan $state)))

; Programs may retain this owned pending value, poll it later, or wait for a
; bounded interval. A soft wait returns the same call; it never cancels it.
(= (llm:begin $plan)
   (if (llm:plan-ready $plan)
    (if (llm:plan-claim $plan)
     (let* (($_ (py-call (native_llm_host.gate)))
            ($epoch (py-call (native_llm_host.field $plan "epoch")))
            ($correlation (py-call (native_llm_host.field $plan "correlation")))
            ($protocol (py-call (native_llm_host.field $plan "protocol")))
            ($base (py-call (native_llm_host.field $plan "base")))
            ($headers-json (py-call (native_llm_host.field $plan "headers")))
            ($headers-outcome (llm:v1:headers-json $headers-json))
            ($request (py-call (native_llm_host.field $plan "request")))
            ($kind (py-call (native_llm_host.field $plan "kind")))
            ($encoding (py-call (native_llm_host.field $plan "encoding")))
            ($encoded (if (and (llm:command-kind? $kind) (not (llm:messages-encoding? $encoding)))
                         (llm:v1:encode-command $protocol $request)
                         (llm:v1:encode $protocol $request)))
            ($timeout (py-call (native_llm_host.field $plan "timeout")))
            ($cap (py-call (native_llm_host.field $plan "cap")))
            ($name (llm:v1:protocol $protocol))
            ($submitted (llm:start-plan $plan $epoch $correlation $name $base $headers-outcome $encoded $timeout $cap)))
       $submitted)
     (if (llm:plan-ready $plan) (llm:v1:failed duplicate duplicate duplicate-plan not-started)
       (llm:v1:failed unsubmitted unsubmitted admission-error not-started)))
     (llm:v1:failed unsubmitted unsubmitted admission-error not-started)))

(: llm:start-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:start-plan $plan $epoch $correlation $protocol $base $headers-outcome $encoded $timeout $cap)
   (case $headers-outcome
     (((llm:v1:ok $headers)
       (llm:encode-plan $plan $epoch $correlation $protocol $base $headers $encoded $timeout $cap))
      ((llm:v1:error $code) (llm:v1:failed $epoch $correlation $code not-started)))))

(: llm:encode-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:encode-plan $plan $epoch $correlation $protocol $base $headers $encoded $timeout $cap)
   (case $encoded
     (((llm:v1:ok (llm:v1:encoded $path $body))
       (let $url (str:concat $base $path)
         (llm:network-plan $plan $epoch $correlation $protocol $url $headers $body $timeout $cap)))
      ((llm:v1:error $code) (llm:v1:failed $epoch $correlation $code not-started)))))

(: llm:network-plan (-> Atom Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:network-plan $plan $epoch $correlation $protocol $url $headers $body $timeout $cap)
   (let* (($legacy-wire (py-call (native_llm_host.wire_body $plan $body)))
          ($profiled (if (chatgpt:plan? $plan) (chatgpt:wire $legacy-wire) $legacy-wire))
          ($wire (llm:observe-wire $plan $profiled))
          ($connect (py-call (native_llm_host.field $plan "connect")))
          ($first (py-call (native_llm_host.field $plan "first")))
          ($idle (py-call (native_llm_host.field $plan "idle")))
          ($described (if (llm:uses-native-ledger $plan) (account:describe $plan) True)))
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
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $id)
        (llm:await $plan $state))
      ((llm:v1:failed duplicate duplicate duplicate-plan not-started)
        (py-call (native_llm_host.duplicate_plan $plan)))
      ($other (llm:deliver $plan $state)))))

(: llm:await (-> Atom Atom %Undefined%))
(= (llm:await $plan $state)
   (let $next (llm:wait-once $plan $state 250)
     (llm:continue $plan $next)))

(: llm:poll-once (-> Atom Atom %Undefined%))
(= (llm:poll-once $plan $state) (llm:wait-once $plan $state 0))

(: llm:wait-once (-> Atom Atom Number %Undefined%))
(= (llm:wait-once $plan $state $milliseconds)
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
   (let* (($wrapper (py-call (native_llm_host.decoded_body $plan $body)))
          ($json (llm:v1:parse $wrapper))
          ($ok (llm:v1:get $json "ok")))
     (if (== $ok (JsonBool True))
       (let* (($value (llm:v1:get $json "body"))
              ($text (llm:v1:text $value)))
         (llm:v1:io-outcome $epoch $correlation $protocol (http:response 200 $text)))
       (llm:v1:failed $epoch $correlation incomplete-stream response-received))))

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
           (if (llm:uses-native-ledger $plan)
             (account:complete $plan $epoch $correlation $legacy-text $canonical-text)
             (py-call (native_llm_host.finish $plan $epoch $correlation $legacy-text $canonical-text))))
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
(= (llm:wait-http $id $milliseconds) (io:try-wait $id $milliseconds))

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
