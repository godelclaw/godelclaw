; Provider transport runs in CeTTa; model selection, operator admission and
; upstream Python transformations stay at explicit host boundaries.
!(import! &self ./src/native_llm_host.py)
(: llm:host-flag (-> Atom Bool))
(= (llm:host-flag $value)
   (case $value ((True True) (true True) ("true" True) ($other False))))
(: llm:command-kind? (-> Atom Bool))
(= (llm:command-kind? $value)
   (case $value ((command True) ("command" True) ($other False))))
(= (llm:native?)
   (let $value (py-call (native_llm_host.enabled)) (llm:host-flag $value)))
(= (llm:plan-ready $plan)
   (let $value (py-call (native_llm_host.field $plan "ready")) (llm:host-flag $value)))
(= (llm:plan-claim $plan)
   (let $value (py-call (native_llm_host.claim $plan)) (llm:host-flag $value)))
!(if (llm:native?)
     (let $_ (import! &self llm:command)
       (let $_ (import! &self llm:compat) (import! &self llm:io))) True)

(= (llm:command $model $tokens $effort $prompt)
   (if (llm:native?)
     (let $plan (py-call (native_llm_host.command_plan $model $tokens $effort $prompt))
       (llm:run $plan))
     (py-call (synthetic_llm.chat $model $tokens $effort $prompt))))

(= (llm:structured $request)
   (let $plan (py-call (native_llm_host.structured_plan $request)) (llm:run $plan)))

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
            ($encoded (if (llm:command-kind? $kind)
                         (llm:v1:encode-command $protocol $request)
                         (llm:v1:encode $protocol $request)))
            ($timeout (py-call (native_llm_host.field $plan "timeout")))
            ($cap (py-call (native_llm_host.field $plan "cap")))
            ($name (llm:v1:protocol $protocol))
            ($submitted (llm:start-plan $plan $epoch $correlation $name $base $headers-outcome $encoded $timeout $cap)))
       $submitted)
     (llm:v1:failed duplicate duplicate duplicate-plan not-started))
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
   (let* (($wire (py-call (native_llm_host.wire_body $plan $body)))
          ($connect (py-call (native_llm_host.field $plan "connect")))
          ($first (py-call (native_llm_host.field $plan "first")))
          ($idle (py-call (native_llm_host.field $plan "idle")))
          ($submitted (llm:submit-http $url $headers $wire $timeout $cap $connect $first $idle)))
     (case $submitted
       (((io:pending $id)
         (let $_ (py-call (native_llm_host.submitted $plan $id))
           (llm:v1:pending $epoch $correlation $protocol $id)))
        ($other (llm:v1:failed $epoch $correlation submit-error not-started))))))

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
              ($event (llm:wait-http $id $milliseconds)))
         (llm:observe-plan $plan $state $epoch $correlation $protocol $id $event))
        (llm:v1:rejected ownership-error))))
      ($terminal $terminal))))

(: llm:progress (-> Atom Atom %Undefined%))
(= (llm:progress $plan $status)
   (case $status
     (((io:progress $id $phase $age $bytes $quiet)
       (py-call (native_llm_host.waiting $plan $bytes $quiet)))
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
           (llm:v1:failed $epoch $correlation (http-status $status) response-received))
         $state))
      ($other (llm:v1:failed $epoch $correlation transport-error remote-outcome-unknown)))))

(: llm:decode-body (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:decode-body $plan $epoch $correlation $protocol $body)
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
   (case $state
     (((llm:v1:completed $epoch $correlation $result)
        (let $projected (llm:v1:project-chat-value $result 0)
          (case $projected
            (((llm:v1:ok $legacy)
              (let* (($canonical (llm:v1:render-outcome $result))
                     ($encoded (llm:v1:render-outcome $legacy)))
                (llm:finish-rendered $plan $epoch $correlation $encoded $canonical)))
             ((llm:v1:error $code) (py-call (native_llm_host.failed $plan $correlation $code)))))))
      ((llm:v1:failed $epoch $correlation (http-status $status) $certainty)
        (py-call (native_llm_host.failed $plan $correlation http-status $status)))
      ((llm:v1:failed $epoch $correlation $reason $certainty)
        (py-call (native_llm_host.failed $plan $correlation $reason)))
      ($other (py-call (native_llm_host.failed $plan unknown invalid-phase))))))

(: llm:finish-rendered (-> Atom Atom Atom Atom Atom %Undefined%))
(= (llm:finish-rendered $plan $epoch $correlation $encoded $canonical)
   (case $encoded
     (((llm:v1:ok $legacy-text)
       (case $canonical
         (((llm:v1:ok $canonical-text)
           (py-call (native_llm_host.finish $plan $epoch $correlation $legacy-text $canonical-text)))
          ((llm:v1:error $code) (py-call (native_llm_host.failed $plan $correlation $code))))))
      ((llm:v1:error $other-code) (py-call (native_llm_host.failed $plan $correlation $other-code))))))

 ; Native error boundaries use CeTTa's generated language spellings.
(: llm:submit-http (-> Atom Atom Atom Atom Atom Atom Atom Atom %Undefined%))
(= (llm:submit-http $url $headers $body $hard $cap $connect $first $idle)
   (io:try-submit (http:request "POST" $url $headers $body $hard $cap False
                              (http:timeouts $connect $first $idle retry-after))))
(: llm:status-http (-> Atom %Undefined%))
(= (llm:status-http $id) (io:try-status $id))
(: llm:wait-http (-> Atom Number %Undefined%))
(= (llm:wait-http $id $milliseconds) (io:try-wait $id $milliseconds))
