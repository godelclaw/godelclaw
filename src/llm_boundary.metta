; Common request/result boundary. Provider JSON is data; completion consumes
; the pending phase exactly once. A foreign transport event cannot complete it.
!(import! &self io)
!(import! &self json)
!(import! &self str)

(= (llm:get $object $key)
   (case $object (((JsonObject $pairs) (json-object-get $object $key)) ($other JsonNull))))
(= (llm:string $value)
   (case $value (((JsonString $text) $text) ($other ""))))
(= (llm:array $value)
   (case $value (((JsonArray $items) $items) ($other ()))))
(= (llm:first $items)
   (if (== $items ()) JsonNull (car-atom $items)))
(= (llm:put $pairs $key $value)
   (cons-atom (JsonPair $key $value) $pairs))
(= (llm:snoc $items $item)
   (if (== $items ()) (cons-atom $item ())
     (let $tail (llm:snoc (cdr-atom $items) $item)
       (cons-atom (car-atom $items) $tail))))

; A canonical assistant message retains native tool IDs and opaque reasoning.
(= (llm:message $text $calls $reasoning)
   (let* (($base ((JsonPair "role" (JsonString "assistant"))
                 (JsonPair "content" (JsonString $text))))
          ($with-calls (if (== $calls ()) $base
                         (llm:put $base "tool_calls" (JsonArray $calls))))
          ($with-reasoning (if (== $reasoning ()) $with-calls
                             (llm:put $with-calls "reasoning_details" (JsonArray $reasoning)))))
     (JsonObject $with-reasoning)))

(= (llm:usage $usage)
   (case $usage
     (((JsonObject $pairs)
       (if (== (llm:get $usage "output_tokens") JsonNull) $usage
         (JsonObject (llm:put (llm:put $pairs "prompt_tokens"
           (llm:get $usage "input_tokens")) "completion_tokens" (llm:get $usage "output_tokens")))))
      ($other (JsonObject ())))))

(= (llm:result $message $finish $stop $usage $id)
   (JsonObject
     ((JsonPair "choices" (JsonArray
        ((JsonObject ((JsonPair "message" $message)
                      (JsonPair "finish_reason" (JsonString $finish)))))))
      (JsonPair "provider_stop" (JsonString $stop))
      (JsonPair "usage" (llm:usage $usage))
      (JsonPair "response_id" $id))))

(= (llm:call-json $id $name $arguments)
   (JsonObject ((JsonPair "id" $id) (JsonPair "type" (JsonString "function"))
     (JsonPair "function" (JsonObject
       ((JsonPair "name" $name) (JsonPair "arguments" $arguments)))))))

(= (llm:anthropic-blocks $blocks $text $calls $reasoning)
   (if (== $blocks ()) (llm:parts $text $calls $reasoning)
     (let* (($head (car-atom $blocks)) ($tail (cdr-atom $blocks))
            ($kind (llm:string (llm:get $head "type"))))
       (case $kind
         (("text" (llm:anthropic-blocks $tail
                     (str:concat $text (llm:string (llm:get $head "text"))) $calls $reasoning))
          ("tool_use" (let $call (llm:call-json (llm:get $head "id") (llm:get $head "name")
                                     (JsonString (json-stringify (llm:get $head "input"))))
                        (llm:anthropic-blocks $tail $text (llm:snoc $calls $call) $reasoning)))
          ("thinking" (llm:anthropic-blocks $tail $text $calls
                         (llm:snoc $reasoning $head)))
          ("redacted_thinking" (llm:anthropic-blocks $tail $text $calls
                                  (llm:snoc $reasoning $head)))
          ($other (Error llm:decode unsupported-provider-block)))))))

(= (llm:anthropic-finish "max_tokens") "length")
(= (llm:anthropic-finish "tool_use") "tool_calls")
(= (llm:anthropic-finish "end_turn") "stop")
(= (llm:anthropic-finish "stop_sequence") "stop")
(= (llm:anthropic-finish $other)
   (if (or (== $other "max_tokens") (or (== $other "tool_use")
       (or (== $other "end_turn") (== $other "stop_sequence")))) (empty) $other))

(= (llm:anthropic-decode $wire)
   (let* (((llm:parts $text $calls $reasoning)
            (llm:anthropic-blocks (llm:array (llm:get $wire "content")) "" () ()))
          ($stop (llm:string (llm:get $wire "stop_reason"))))
     (llm:result (llm:message $text $calls $reasoning)
       (llm:anthropic-finish $stop) $stop (llm:get $wire "usage") (llm:get $wire "id"))))

(= (llm:response-text $blocks $text $refusal)
   (if (== $blocks ()) (llm:text $text $refusal)
     (let* (($head (car-atom $blocks)) ($tail (cdr-atom $blocks))
            ($kind (llm:string (llm:get $head "type"))))
       (case $kind
         (("output_text" (llm:response-text $tail
                            (str:concat $text (llm:string (llm:get $head "text"))) $refusal))
          ("refusal" (llm:response-text $tail $text
            (str:concat (if (== $refusal "") "" (str:concat $refusal "\n"))
                        (llm:string (llm:get $head "refusal")))))
          ($other (Error llm:decode unsupported-provider-block)))))))

(= (llm:response-items $items $text $calls $reasoning $refusal)
   (if (== $items ()) (llm:parts $text $calls $reasoning $refusal)
     (let* (($head (car-atom $items)) ($tail (cdr-atom $items))
            ($kind (llm:string (llm:get $head "type"))))
       (case $kind
         (("message" (let (llm:text $next $rejected)
                         (llm:response-text (llm:array (llm:get $head "content")) $text $refusal)
                       (llm:response-items $tail $next $calls $reasoning $rejected)))
          ("function_call" (let $call (llm:call-json (llm:get $head "call_id")
                                             (llm:get $head "name") (llm:get $head "arguments"))
                             (llm:response-items $tail $text (llm:snoc $calls $call) $reasoning $refusal)))
          ("reasoning" (llm:response-items $tail $text $calls
                          (llm:snoc $reasoning $head) $refusal))
          ($other (Error llm:decode unsupported-provider-block)))))))

(= (llm:responses-decode $wire)
   (let* (((llm:parts $text $calls $reasoning $refusal)
           (llm:response-items (llm:array (llm:get $wire "output")) "" () () ""))
          ($status (llm:string (llm:get $wire "status")))
          ($incomplete (llm:string (llm:get (llm:get $wire "incomplete_details") "reason")))
          ($stop (if (== $incomplete "") $status $incomplete))
          ($finish (if (and (== $status "incomplete") (== $incomplete "max_output_tokens"))
                     "length" (if (not (== $refusal "")) "refusal"
                       (if (== $status "completed")
                         (if (== $calls ()) "stop" "tool_calls") $status))))
          ($message (llm:message $text $calls $reasoning))
          ($with-refusal (if (== $refusal "") $message
            (case $message (((JsonObject $pairs)
              (JsonObject (llm:put $pairs "refusal" (JsonString $refusal)))))))))
     (llm:result $with-refusal $finish $stop (llm:get $wire "usage") (llm:get $wire "id"))))

(= (llm:chat-decode $wire)
   (let* (($choice (llm:first (llm:array (llm:get $wire "choices"))))
          ($message (llm:get $choice "message"))
          ($finish (llm:string (llm:get $choice "finish_reason"))))
     (if (== $message JsonNull) (Error llm:decode missing-message)
       (llm:result $message $finish $finish (llm:get $wire "usage") (llm:get $wire "id")))))

(= (llm:decode $protocol $body)
   (let $wire (json-parse $body)
     (case $wire
       (((JsonObject $pairs)
          (case $protocol
            (("anthropic" (llm:anthropic-decode $wire))
             ("responses" (llm:responses-decode $wire))
             ("chat-completions" (llm:chat-decode $wire))
             ($other (Error llm:decode unsupported-protocol)))))
        ($other (Error llm:decode invalid-json))))))

; Evaluate the submission before passing it to a held-data matcher.
; Python/Janus text is a Symbol in the PeTTa dialect, not a String.
(= (llm:protocol $value)
   (case $value ((anthropic "anthropic") (responses "responses")
                (chat-completions "chat-completions") ($other $value))))
(= (llm:submit $correlation $protocol $url $headers $body $timeout $cap)
   (let* (($wire-protocol (llm:protocol $protocol))
          ($request (http:request "POST" $url $headers $body $timeout $cap))
          ($outcome (catch (io:submit $request))))
     (llm:submitted $correlation $wire-protocol $outcome)))
(: llm:submitted (-> Atom Atom Atom Expression))
(= (llm:submitted $correlation $protocol $outcome)
   (case $outcome
     (((io:pending $id) (llm:pending $correlation $protocol $id))
      ($other (llm:failed $correlation submit-error)))))

(: llm:observe (-> Atom Atom Expression))
(= (llm:observe $state $event)
   (case $state
     (((llm:pending $correlation $protocol $id)
        (case $event
          (((io:idle) $state)
           ((io:event $event-id $outcome)
             (if (== $event-id $id)
               (llm:outcome $correlation $protocol $outcome)
               (llm:foreign $state $event)))
           ($other (llm:foreign $state $event)))))
      ((llm:completed $correlation $result) $state)
      ((llm:failed $correlation $reason) $state)
      ($other (llm:failed unknown invalid-phase)))))

(: llm:outcome (-> Atom Atom Atom Expression))
(= (llm:outcome $correlation $protocol $outcome)
   (case $outcome
     (((http:response $status $body)
       (if (and (>= $status 200) (< $status 300))
         (let $decoded (catch (llm:decode $protocol $body))
           (case $decoded
             (((Error $source $reason) (llm:failed $correlation decode-error))
              ($result (llm:completed $correlation $result)))))
         (llm:failed $correlation (http-status $status))))
      ($other (llm:failed $correlation transport-error)))))
