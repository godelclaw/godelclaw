; Compatibility adapter for the historical single-turn boundary. New clients
; use CeTTa's versioned ordered result and handle-specific lifecycle directly.
!(import! &self llm:compat)

(= (llm:decode $protocol $body)
   (let $decoded (llm:v1:decode $protocol $body)
     (llm:decoded $decoded)))
(: llm:decoded (-> Atom %Undefined%))
(= (llm:decoded $decoded)
   (case $decoded
     (((llm:v1:ok $canonical)
       (let* (($text (llm:v1:render $canonical)) ($projection (llm:v1:project-chat $text 0)))
         (llm:projected $projection)))
      ((llm:v1:error $code) (Error llm:decode $code)))))
(: llm:projected (-> Atom %Undefined%))
(= (llm:projected $projection)
   (case $projection
     (((llm:v1:ok $legacy) $legacy)
      ((llm:v1:error $code) (Error llm:decode $code)))))

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
