; A probe's scripted model. On the native transport the probe's provider is
; the test world's loopback peer (tests/fixtures/native_http_peer.c): it
; answers request N with response-N.body when that file exists, otherwise
; with response.body, and keeps each request's body as request-N.body and the
; number of requests in "count", all in NATIVE_TEST_WORLD.
(= (scripted:path $name) (str:concat (system:getenv-or-default "NATIVE_TEST_WORLD" "") $name))
; A Chat Completions answer whose message is $content.
(= (scripted:reply $content)
   (json:stringify
     (JsonObject ((JsonPair "id" (JsonString "fixture"))
                  (JsonPair "choices" (JsonArray ((JsonObject
                    ((JsonPair "message" (JsonObject ((JsonPair "role" (JsonString "assistant"))
                                                      (JsonPair "content" (JsonString $content)))))
                     (JsonPair "finish_reason" (JsonString "stop")))))))
                  (JsonPair "usage" (JsonObject ((JsonPair "prompt_tokens" 10) (JsonPair "completion_tokens" 2))))))))
; Request $n's answer, and the answer of every request without its own.
(= (scripted:answer $n $content)
   (fs:write (scripted:path (format-args "response-{}.body" ($n))) (scripted:reply $content)))
(= (scripted:default $content) (fs:write (scripted:path "response.body") (scripted:reply $content)))
; The number of requests the provider has received.
(= (scripted:calls) (json:parse (str:strip-python (fs:read-text (scripted:path "count")))))
; 1 when request $n's prompt holds $text, else 0. The prompt is JSON text in
; the request, so $text is looked for as JSON writes it.
(= (scripted:escaped $text)
   (let $quoted (json:stringify (JsonString $text))
     (str:byte-slice $quoted 1 (- (str:byte-length $quoted) 1))))
(= (scripted:prompt-has $n $text)
   (let $path (scripted:path (format-args "request-{}.body" ($n)))
     (if (fs:exists $path)
       (case (str:find (fs:read-text $path) (scripted:escaped $text)) (((TextNotFound $missing) 0) ($found 1)))
       0)))
(= (scripted:last-prompt-has $text) (scripted:prompt-has (scripted:calls) $text))
; True when request $n carries $text as a whole user message.
(= (scripted:user-message? $n $text)
   (let (JsonArray $messages)
        (json:get (json:parse (fs:read-text (scripted:path (format-args "request-{}.body" ($n))))) "messages")
     (scripted:member? (JsonObject ((JsonPair "role" "user") (JsonPair "content" $text))) $messages)))
(= (scripted:member? $item $items)
   (if (== $items ()) False
     (if (== (car-atom $items) $item) True (scripted:member? $item (cdr-atom $items)))))
