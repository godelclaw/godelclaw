; One framing cursor per owned provider attempt. Display has no effect authority.
!(import! &self ./src/stream_sse)
!(import! &self ./src/stream_cursors_cetta.metta)
!(import! &self ./src/stream_events)
!(import! &self ./src/stream_sink.py)

(= (stream:sequence-field $plan $field $text)
   (case $field
     ((JsonNull 1)
      ((JsonNumber $digits)
       (if (str:is-all-in digit $digits)
         (let* (($id (py-call (native_llm_host.field $plan "correlation")))
                ($n (str:string->number $digits))) (stream:sequence $id $n $text)) -1))
      ($other -1))))
(= (stream:emit-all $plan $items)
   (if (== $items ()) 1
     (let $_ (py-call (stream_sink.projected $plan (json-stringify (car-atom $items))))
       (stream:emit-all $plan (cdr-atom $items)))))
(= (stream:frame-value $plan $protocol $event $value)
   (let $kind (stream:field $value "type")
     (if (or (== $event "") (== $event $kind))
       (stream:emit-all $plan (stream:project $protocol $value))
       (py-call (stream_sink.unavailable $plan "conflicting SSE type")))))
(= (stream:frame $plan $protocol $event $text)
   (let $value (catch (json-parse $text))
     (case $value
       (((JsonObject $fields)
          (let* (($field (stream:get $value "sequence_number"))
                 ($admit (stream:sequence-field $plan $field $text)))
            (if (== $admit 1) (stream:frame-value $plan $protocol $event $value)
              (if (== $admit 0) 1 (py-call (stream_sink.unavailable $plan "conflicting provider sequence"))))))
        ($bad (py-call (stream_sink.unavailable $plan "invalid SSE JSON")))))))
(= (stream:frames $plan $protocol $frames)
   (if (== $frames ()) 1
     (let (SSEEvent $event $text) (car-atom $frames)
       (if (== $text "[DONE]") 1
         (progn (stream:frame $plan $protocol $event $text)
                (stream:frames $plan $protocol (cdr-atom $frames)))))))
(= (stream:chunk $plan $next $text)
   (let $advanced (py-call (stream_sink.advance $plan $next $text))
    (if (== $advanced 1)
     (let* (($id (py-call (native_llm_host.field $plan "correlation")))
            ($current (stream:current $id))
            ($batch (sse:feed $current $text 16777216)))
       (case $batch
         (((SSEBatch $state $frames)
           (let* (($remembered (stream:remember $id $state))
                  ($protocol (py-call (native_llm_host.field $plan "protocol"))))
             (stream:frames $plan (str:concat "" $protocol) $frames)))
          ((SSEError $why) (py-call (stream_sink.unavailable $plan $why))))))
     0)))
; The display observer keeps its state on the host plan of a native owner.
; Without a configured display, or for a native compaction, nothing is readable.
; `and` evaluates both sides: only a native owner may be asked its purpose.
(= (stream:observed? $owner)
   (if (== (str:strip-python (system:getenv-or-default "METTACLAW_STREAM_DIR" "")) "") False
     (if (call:native? $owner) (not (call:compaction? $owner)) True)))
(= (stream:progress $owner $handle)
   (if (not (stream:observed? $owner)) 1
   (let* (($plan (call:host $owner)) ($readable (py-call (stream_sink.readable $plan))))
    (if (llm:host-flag $readable)
     (let* (($offset (py-call (stream_sink.offset $plan)))
            ($chunk (llm:commentary-read $handle $offset)))
       (case $chunk
         (((http:chunk $next $text $phase) (stream:chunk $plan $next $text))
          ($other (py-call (stream_sink.unavailable $plan))))))
     1))))
