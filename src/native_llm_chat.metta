; OpenAI-compatible chat boundary for CeTTa. This module has no loop state,
; provider credentials, model selection, or automatic retries. A caller owns
; the endpoint and routes io:event values by the io:pending transport ID.
;
; The endpoint is a local service or a host-authenticated relay. CeTTa's
; ephemeral io:submit currently has no credential-reference argument, so
; putting an Authorization value here would expose it as a MeTTa atom.

!(import! &self io)
!(import! &self json)
!(import! &self str)

; The framework inserts three layout markers ahead of turn-specific content.
; Python removes only those three; a fourth occurrence is ordinary prompt text.
(= (native-chat:strip-layout $text)
   (native-chat:strip-layout-n $text 3))

(= (native-chat:strip-layout-n $text $remaining)
   (if (== $remaining 0)
       $text
       (case (str:find $text "_cache_boundary_")
         (((TextNotFound $needle) $text)
          ($offset
            (let* (($before (str:byte-slice $text 0 $offset))
                   ($after (str:byte-slice $text
                             (+ $offset 16) (str:byte-length $text)))
                   ($tail (native-chat:strip-layout-n $after (- $remaining 1))))
              (str:concat $before $tail)))))))

; json-stringify is the supported compatibility codec for constructing JSON
; from MeTTa Strings. The V1 codec exposes scalar lists but has no direct
; String-to-JsonStringV1 constructor yet.
(= (native-chat:encode $model $max-tokens $effort $prompt)
   (let $content (native-chat:strip-layout $prompt)
     (json-stringify
       (JsonObject
         ((JsonPair "model" (JsonString $model))
          (JsonPair "messages"
            (JsonArray
              ((JsonObject
                 ((JsonPair "role" (JsonString "user"))
                  (JsonPair "content" (JsonString $content)))))))
          (JsonPair "max_tokens" (JsonNumber (repr $max-tokens)))
          (JsonPair "reasoning"
            (JsonObject
              ((JsonPair "effort" (JsonString $effort))))))))))

(= (native-chat:completion-url $base)
   (if (str:ends-with $base "/")
       (native-chat:completion-url
         (str:byte-slice $base 0 (- (str:byte-length $base) 1)))
       (str:concat $base "/chat/completions")))

; The response is capped at 1 MiB. No auth header or secret is accepted by
; this boundary; an endpoint requiring credentials needs a host-side relay.
(= (native-chat:request $base $model $max-tokens $effort $prompt)
   (let* (($url (native-chat:completion-url $base))
          ($body (native-chat:encode $model $max-tokens $effort $prompt)))
     (http:request "POST" $url
       ((http:header "Content-Type" "application/json"))
       $body 120000 1048576)))

; One call of this function contains exactly one io:submit. The caller's
; correlation value is retained in the returned handle; the host assigns ID.
(= (native-chat:submit $correlation $base $model $max-tokens $effort $prompt)
   (let* (($request (native-chat:request $base $model $max-tokens $effort $prompt))
          ($submitted (catch (io:submit $request))))
     (native-chat:submitted $correlation $submitted)))

(: native-chat:submitted (-> Atom Atom Expression))
(= (native-chat:submitted $correlation $result)
   (case $result
     (((io:pending $id) (native-chat:pending $correlation $id))
      ((Error $source $reason) (native-chat:failed $correlation submit-error))
      ($other (native-chat:failed $correlation submit-error)))))

; Decode just the first assistant message, as synthetic_llm.chat does. All
; parser errors are collapsed so malformed response text never enters an
; error term, log, or control-plane result.
(= (native-chat:decode $body)
   (case (json-parse $body)
     (((JsonObject $pairs)
        (native-chat:choices (json-object-get (JsonObject $pairs) "choices")))
      ($other (native-chat:failure invalid-json)))))

(= (native-chat:choices $value)
   (case $value
     (((JsonArray $items)
        (if (== $items ())
            (native-chat:failure missing-choice)
            (case (decons-atom $items)
              ((($first $rest) (native-chat:message $first))
               ($other (native-chat:failure malformed-choice))))))
      ($other (native-chat:failure missing-choice)))))

(= (native-chat:message $choice)
   (case $choice
     (((JsonObject $pairs)
        (native-chat:content
          (json-object-get (JsonObject $pairs) "message")))
      ($other (native-chat:failure malformed-choice)))))

(= (native-chat:content $message)
   (case $message
     (((JsonObject $pairs)
        (native-chat:content-value
          (json-object-get (JsonObject $pairs) "content")))
      ($other (native-chat:failure missing-message)))))

(= (native-chat:content-value $value)
   (case $value
     (((JsonString $text)
        (if (== (str:trim $text) "")
            (native-chat:failure empty-content)
            (native-chat:reply $text)))
      ($other (native-chat:failure missing-content)))))

; The caller routes a consumed io:event to the matching pending handle. These
; heads match the event as data: a nested Error must not be evaluated before
; its correlation ID is checked. A foreign event is left unchanged for the
; caller's dispatcher.
(: native-chat:event-id (-> Atom Number))
(= (native-chat:event-id (io:event $id $outcome)) $id)

(: native-chat:observe (-> Atom Atom Expression))
(= (native-chat:observe (native-chat:pending $correlation $id)
      (io:event $id (http:response $status $body)))
   (if (< $status 400)
       (native-chat:attach $correlation (native-chat:decode $body))
       (native-chat:failed $correlation (http-status $status))))
(= (native-chat:observe (native-chat:pending $correlation $id)
      (io:event $id (Error $source $reason)))
   (native-chat:failed $correlation transport-error))
(= (native-chat:observe (native-chat:pending $correlation $id) (io:idle))
   (native-chat:pending $correlation $id))

(= (native-chat:attach $correlation $decoded)
   (case $decoded
     (((native-chat:reply $text) (native-chat:completed $correlation $text))
      ((native-chat:failure $reason) (native-chat:failed $correlation $reason))
      ($other (native-chat:failed $correlation invalid-response)))))
