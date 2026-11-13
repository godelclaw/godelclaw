; One framing cursor per owned provider attempt. Display has no effect authority.
!(import! &self ./src/stream_sse)
!(import! &self ./src/stream_cursors_cetta.metta)
!(import! &self ./src/stream_events)
!(import! &self ./src/display_cetta.metta)

; $id is the attempt (the owner's correlation): it keys the native cursors and
; the call's display (src/display_cetta.metta).
(= (stream:sequence-field $id $field $text)
   (case $field
     ((JsonNull 1)
      ((JsonNumber $digits)
       (if (str:is-all-in digit $digits)
         (stream:sequence $id (str:string->number $digits) $text) -1))
      ($other -1))))
(= (stream:emit-all $id $items)
   (if (== $items ()) 1
     (let $_ (display:call-projected $id (car-atom $items))
       (stream:emit-all $id (cdr-atom $items)))))
(= (stream:frame-value $id $protocol $event $value)
   (let $kind (stream:field $value "type")
     (if (or (== $event "") (== $event $kind))
       (stream:emit-all $id (stream:project $protocol $value))
       (display:call-unavailable $id "conflicting SSE type"))))
(= (stream:frame $id $protocol $event $text)
   (let $value (catch (json-parse $text))
     (case $value
       (((JsonObject $fields)
          (let* (($field (stream:get $value "sequence_number"))
                 ($admit (stream:sequence-field $id $field $text)))
            (if (== $admit 1) (stream:frame-value $id $protocol $event $value)
              (if (== $admit 0) 1 (display:call-unavailable $id "conflicting provider sequence")))))
        ($bad (display:call-unavailable $id "invalid SSE JSON"))))))
(= (stream:frames $id $protocol $frames)
   (if (== $frames ()) 1
     (let (SSEEvent $event $text) (car-atom $frames)
       (if (== $text "[DONE]") 1
         (progn (stream:frame $id $protocol $event $text)
                (stream:frames $id $protocol (cdr-atom $frames)))))))
(= (stream:chunk $owner $next $text)
   (let $id (call:correlation $owner)
    (if (== (display:call-advance $id $next $text) 1)
     (let* (($current (stream:current $id))
            ($batch (sse:feed $current $text 16777216)))
       (case $batch
         (((SSEBatch $state $frames)
           (let $remembered (stream:remember $id $state)
             (stream:frames $id (call:protocol $owner) $frames)))
          ((SSEError $why) (display:call-unavailable $id $why)))))
     0)))
; Without a configured display, or for a compaction, nothing is readable.
(= (stream:observed? $owner)
   (if (not (display:configured?)) False (not (call:compaction? $owner))))
(= (stream:progress $owner $handle)
   (if (not (stream:observed? $owner)) 1
   (let $id (call:correlation $owner)
    (if (display:call-readable? $id)
     (case (llm:commentary-read $handle (display:call-offset $id))
       (((http:chunk $next $text $phase) (stream:chunk $owner $next $text))
        ($other (display:call-unavailable $id "incremental observation unavailable"))))
     1))))
