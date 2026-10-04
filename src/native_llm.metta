; Provider transport runs in CeTTa; model selection, operator admission and
; upstream Python transformations stay at explicit host boundaries.
!(import! &self ./native_llm_host.py)
!(if (py-call (native_llm_host.enabled))
     (import! &self ./llm_boundary.metta) True)

(= (llm:native?) (py-call (native_llm_host.enabled)))

(= (llm:command $model $tokens $effort $prompt)
   (if (llm:native?)
     (let $plan (py-call (native_llm_host.command_plan $model $tokens $effort $prompt))
       (llm:run $plan))
     (py-call (synthetic_llm.chat $model $tokens $effort $prompt))))

(= (llm:structured $request)
   (let $plan (py-call (native_llm_host.structured_plan $request)) (llm:run $plan)))

(= (llm:run $plan)
   (if (py-call (native_llm_host.field $plan "ready"))
     (let* (($_ (py-call (native_llm_host.gate)))
            ($correlation (py-call (native_llm_host.field $plan "correlation")))
            ($protocol (py-call (native_llm_host.field $plan "protocol")))
            ($url (py-call (native_llm_host.field $plan "url")))
            ($headers (py-call (native_llm_host.field $plan "headers")))
            ($body (py-call (native_llm_host.field $plan "body")))
            ($timeout (py-call (native_llm_host.field $plan "timeout")))
            ($cap (py-call (native_llm_host.field $plan "cap")))
            ($submitted (llm:submit $correlation $protocol $url $headers $body $timeout $cap)))
       (llm:after-submit $plan $submitted))
     (py-call (native_llm_host.failed $plan unsubmitted admission-error))))

(: llm:after-submit (-> Atom Atom %Undefined%))
(= (llm:after-submit $plan $state)
   (case $state
     (((llm:pending $correlation $protocol $id)
        (progn (py-call (native_llm_host.submitted $plan $id)) (llm:await $plan $state)))
      ($other (llm:deliver $plan $state)))))

(: llm:await (-> Atom Atom %Undefined%))
(= (llm:await $plan $state)
   (let* (($_ (py-call (native_llm_host.gate)))
          ($event (io:wait 250))
          ($next (llm:observe $state $event)))
     (llm:continue $plan $next)))

(: llm:continue (-> Atom Atom %Undefined%))
(= (llm:continue $plan $state)
   (case $state
     (((llm:pending $correlation $protocol $id) (llm:await $plan $state))
      ((llm:foreign $pending $event)
        (progn (py-call (native_llm_host.park $event)) (llm:await $plan $pending)))
      ($terminal (llm:deliver $plan $terminal)))))

(: llm:deliver (-> Atom Atom %Undefined%))
(= (llm:deliver $plan $state)
   (case $state
     (((llm:completed $correlation $result)
        (py-call (native_llm_host.finish $plan $correlation (json-stringify $result))))
      ((llm:failed $correlation (http-status $status))
        (py-call (native_llm_host.failed $plan $correlation http-status $status)))
      ((llm:failed $correlation $reason)
        (py-call (native_llm_host.failed $plan $correlation $reason)))
      ($other (py-call (native_llm_host.failed $plan unknown invalid-phase))))))
