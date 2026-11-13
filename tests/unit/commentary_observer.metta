; Unit tests of src/commentary_observer_cetta.metta; no Python. The cases are
; those the host observer had (model_commentary.Publication and
; commentary_stream.Stream), on the worklog under the unit's scratch
; directory. Run with commentary on.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/commentary_observer_cetta.metta)

(= (ot:root) (system:getenv-or-default "METTACLAW_PUBLIC_WORK_DIR" ""))
(= (ot:sharing $action) (pw:transact (ot:root) $action "" JsonNull))
(= (ot:notes)
   (case (pw:transact (ot:root) "read" "" JsonNull)
     (((PublicWorkRead $state (JsonArray $entries)) (map-atom $entries $entry (erv:get $entry "text" "")))
      ($other ()))))
(= (ot:after $count $list) (if (<= $count 0) $list (ot:after (- $count 1) (cdr-atom $list))))
; The notes added since $before notes existed.
(= (ot:since $before) (ot:after $before (ot:notes)))
(= (ot:count) (size-atom (ot:notes)))

; Frames as the providers write them (json.dumps, as the host tests did).
(= (ot:json $value) (json:stringify-python-with $value ((ensure-ascii False))))
(= (ot:chat-as $id $delta $finish)
   (str:concat "data: " (str:concat (ot:json (JsonObject ((JsonPair "id" $id)
     (JsonPair "choices" (JsonArray ((JsonObject ((JsonPair "index" 0) (JsonPair "delta" $delta)
                                                   (JsonPair "finish_reason" $finish))))))))) "\n\n")))
(= (ot:chat $delta) (ot:chat-as "reply" $delta JsonNull))
(= (ot:content $text) (ot:chat (JsonObject ((JsonPair "content" $text)))))
(= (ot:event $value)
   (str:concat "event: " (str:concat (erv:get $value "type" "") (str:concat "\ndata: " (str:concat (ot:json $value) "\n\n")))))
(= (ot:delta $text)
   (ot:event (JsonObject ((JsonPair "type" "response.output_text.delta") (JsonPair "item_id" "msg")
                          (JsonPair "output_index" 1) (JsonPair "content_index" 0) (JsonPair "delta" $text)))))

; A call with commentary on: a chat-completions call that streams, unless named.
(= (ot:call $attempt) (observer:begin $attempt "chat-completions" True 1048576))
(= (ot:call $attempt $protocol $stream $cap) (observer:begin $attempt $protocol $stream $cap))
; Feeds $body in pieces of $step characters while the observer reads.
(= (ot:feed $attempt $body $step) (ot:feed-from $attempt $body $step 0 (str:char-length $body)))
(= (ot:feed-from $attempt $body $step $at $count)
   (if (>= $at $count) True
     (case (observer:offset $attempt)
       ((none True)
        ($offset
         (let* (($piece (str:char-slice $body $at (+ $at $step)))
                ($_ (observer:feed $attempt (+ $offset (str:byte-length $piece)) $piece waiting)))
           (ot:feed-from $attempt $body $step (+ $at $step) $count)))))))
(= (ot:stopped? $attempt) (== (observer:offset $attempt) none))
(= (ot:join $texts) (str:join "" $texts))
; $count copies of $text, built by doubling.
(= (ot:copies $text $count)
   (if (<= $count 0) ""
     (if (== (% $count 2) 0) (let $half (ot:copies $text (/ $count 2)) (str:concat $half $half))
       (str:concat $text (ot:copies $text (- $count 1))))))
(= (ot:chars $text) (ot:chars-from $text 0 (str:char-length $text)))
(= (ot:chars-from $text $at $count)
   (if (>= $at $count) () (cons-atom (str:char-slice $text $at (+ $at 1)) (ot:chars-from $text (+ $at 1) $count))))

; The envelope of the host test, as ASCII-escaped and as UTF-8 JSON.
(= (ot:envelope $ascii)
   (json:stringify-python-with
     (JsonObject ((JsonPair "commentary" "Check ž 🐈, newline\nnext.") (JsonPair "commands" "((nop))")))
     ((ensure-ascii $ascii))))
; Each character of the envelope as its own frame, after a byte-order mark, a
; comment and a reasoning frame, with CRLF line ends.
(= (ot:split-body $ascii)
   (let* (($envelope (ot:envelope $ascii))
          ($frames (ot:frames-of $envelope 0 (str:char-length $envelope)))
          ($body (str:concat "\u{feff}:ping\n\n"
                   (str:concat (ot:chat (JsonObject ((JsonPair "reasoning_content" "PRIVATE")))) $frames))))
     (str:join "\r\n" (str:split "\n" $body))))
(= (ot:frames-of $text $at $count)
   (if (>= $at $count) ""
     (str:concat (ot:content (str:char-slice $text $at (+ $at 1))) (ot:frames-of $text (+ $at 1) $count))))
(= (ot:split-case $name $ascii $step)
   (let* (($before (ot:count)) ($_ (ot:call $name))
          ($_ (ot:feed $name (ot:split-body $ascii) $step)))
     (nt:equal (ot:since $before) ("Check ž 🐈, newline\nnext."))))

; A stream that must stop the observer, then a commentary frame: no note.
(= (ot:stops? $name $protocol $body)
   (let* (($before (ot:count)) ($_ (ot:call $name $protocol True 1048576))
          ($_ (ot:feed $name $body 100000)) ($stopped (ot:stopped? $name))
          ($_ (observer:feed $name 0 (ot:content "{\"commentary\":\"not shown\",") waiting)))
     (and $stopped (nt:equal (ot:since $before) ()))))
; A stream that publishes nothing, the observer stopped or not.
(= (ot:silent? $name $protocol $cap $body)
   (let* (($before (ot:count)) ($_ (ot:call $name $protocol True $cap)) ($_ (ot:feed $name $body 100000)))
     (nt:equal (ot:since $before) ())))
; A Responses text delta, then $second: the observer stops, with no note.
(= (ot:mixed-output $name $second)
   (let* (($before (ot:count)) ($_ (ot:call $name "responses" True 1048576))
          ($_ (ot:feed $name (ot:delta "{\"commentary\":") 100000))
          ($_ (ot:feed $name (ot:event $second) 100000)))
     (and (ot:stopped? $name) (nt:equal (ot:since $before) ()))))
; A body past a bound: the observer stops, with no note.
(= (ot:bounded? $name $cap $body)
   (let* (($before (ot:count)) ($_ (ot:call $name "chat-completions" True $cap))
          ($_ (ot:feed $name $body 100000)))
     (and (ot:stopped? $name) (nt:equal (ot:since $before) ()))))

; Each block records its end; the marker needs both, so a block that ends
; without a result cannot pass.
!(bind! &ot-ended (new-state 0))
(= (ot:ended) (change-state! &ot-ended (+ (get-state &ot-ended) 1)))
!(ot:sharing "on")
!(let* (; Every character in its own frame, read whole, in sevens and one by one.
        ($_ (nt:require split-ascii-whole (ot:split-case "s1" True 100000)))
        ($_ (nt:require split-ascii-sevens (ot:split-case "s2" True 7)))
        ($_ (nt:require split-ascii-ones (ot:split-case "s3" True 1)))
        ($_ (nt:require split-utf8-whole (ot:split-case "s4" False 100000)))
        ($_ (nt:require split-utf8-ones (ot:split-case "s5" False 1)))
        ; Nothing before the first string is complete and its separator seen.
        ($before-p (ot:count)) ($_ (ot:call "p1"))
        ($_ (ot:feed "p1" (ot:content "{\"commentary\":") 1000))
        ($_ (nt:require nothing-before-the-string (nt:equal (ot:since $before-p) ())))
        ($_ (ot:feed "p1" (ot:content "\"ready\"") 1000))
        ($_ (nt:require nothing-before-the-separator (nt:equal (ot:since $before-p) ())))
        ($_ (ot:feed "p1" (ot:content ", \"commands\":\"((shell") 1000))
        ($_ (nt:require published-at-the-separator (nt:equal (ot:since $before-p) ("ready"))))
        ($_ (nt:require stops-after-the-field (ot:stopped? "p1")))
        ; Once a call: the accepted output's commentary is not a second note.
        ($_ (nt:require one-note-a-call
              (nt:equal (observer:decision "p1" (CommentaryAcceptedV1 "((shell))" (CommentaryPublishV1 "ready again"))) 0)))
        ($_ (nt:require still-one-note (nt:equal (ot:since $before-p) ("ready"))))
        ; Reasoning, tools, refusals and a later commentary field are no sources.
        ($_ (nt:require reasoning-content-no-source
              (ot:silent? "r1" "chat-completions" 1048576 (ot:chat (JsonObject ((JsonPair "reasoning_content" "{\"commentary\":\"private\",")))))))
        ($_ (nt:require reasoning-no-source
              (ot:silent? "r2" "chat-completions" 1048576 (ot:chat (JsonObject ((JsonPair "reasoning" "{\"commentary\":\"private\",")))))))
        ($_ (nt:require tool-calls-stop
              (ot:stops? "r3" "chat-completions"
                (ot:chat (JsonObject ((JsonPair "tool_calls" (JsonArray ((JsonObject ((JsonPair "arguments" "{\"commentary\":\"private\","))))))))))))
        ($_ (nt:require refusal-stops
              (ot:stops? "r4" "chat-completions" (ot:chat (JsonObject ((JsonPair "refusal" "{\"commentary\":\"private\",")))))))
        ($_ (nt:require later-field-no-source
              (ot:silent? "r5" "chat-completions" 1048576 (ot:content "{\"commands\":\"((nop))\", \"commentary\":\"later\","))))
        ($_ (nt:require later-field-stops (ot:stopped? "r5")))
        ; Responses: only output text deltas are sources.
        ($before-o (ot:count)) ($_ (ot:call "o1" "responses" False 1048576))
        ($_ (ot:feed "o1" (ot:join (map-atom ("response.reasoning_text.delta" "response.reasoning_summary_text.delta"
                                              "response.function_call_arguments.delta" "response.output_item.done") $kind
              (ot:event (JsonObject ((JsonPair "type" $kind) (JsonPair "delta" "{\"commentary\":\"private\",")
                                     (JsonPair "item" (JsonObject ((JsonPair "text" "{\"commentary\":\"private\","))))))))) 100000))
        ($source (json:stringify-python-with (JsonObject ((JsonPair "commentary" "Responses ž 🐈") (JsonPair "commands" "((nop))"))) ((ensure-ascii True))))
        ($_ (ot:feed "o1" (ot:join (map-atom (ot:chars $source) $c (ot:delta $c))) 100000))
        ($_ (nt:require responses-output-text-only (nt:equal (ot:since $before-o) ("Responses ž 🐈"))))
        ; Malformed and mixed streams stop the observer.
        ($_ (nt:require invalid-json-stops (ot:stops? "m1" "chat-completions" "data: {\n\n")))
        ($_ (nt:require mixed-identity-stops
              (ot:stops? "m2" "chat-completions" (str:concat (ot:content "{") (ot:chat-as "foreign" (JsonObject ((JsonPair "content" "x"))) JsonNull)))))
        ($_ (nt:require null-choice-stops (ot:stops? "m3" "chat-completions" "data: {\"id\":\"x\",\"choices\":[null]}\n\n")))
        ($_ (nt:require non-text-content-stops (ot:stops? "m4" "chat-completions" (ot:chat (JsonObject ((JsonPair "content" 3)))))))
        ($_ (nt:require event-after-the-end-stops
              (ot:stops? "m5" "chat-completions" (str:concat (ot:chat-as "reply" (JsonObject ()) "stop") (ot:content "x")))))
        ($_ (nt:require error-event-stops (ot:stops? "m6" "responses" "event: error\ndata: {\"type\":\"error\"}\n\n")))
        ($_ (nt:require chat-error-event-stops (ot:stops? "m12" "chat-completions" "event: error\ndata: {\"id\":\"x\",\"choices\":[]}\n\n")))
        ($_ (nt:require chat-error-member-stops (ot:stops? "m13" "chat-completions" "data: {\"id\":\"x\",\"choices\":[],\"error\":{}}\n\n")))
        ($_ (nt:require failed-response-stops (ot:stops? "m7" "responses" (ot:event (JsonObject ((JsonPair "type" "response.failed")))))))
        ($_ (nt:require conflicting-event-name-stops
              (ot:stops? "m8" "responses" "event: response.completed\ndata: {\"type\":\"response.output_text.delta\"}\n\n")))
        ($_ (nt:require nonzero-index-stops
              (ot:stops? "m9" "chat-completions" "data: {\"id\":\"x\",\"choices\":[{\"index\":1,\"delta\":{}}]}\n\n")))
        ($_ (nt:require float-index-stops
              (ot:stops? "m10" "chat-completions" "data: {\"id\":\"x\",\"choices\":[{\"index\":0.0,\"delta\":{}}]}\n\n")))
        ($_ (nt:require other-role-stops
              (ot:stops? "m11" "chat-completions" (ot:chat (JsonObject ((JsonPair "role" "user") (JsonPair "content" "{")))))))
        ; A Responses delta of another output part, or without one, stops it.
        ($_ (nt:require missing-output-stops (ot:mixed-output "x1" (JsonObject ((JsonPair "type" "response.output_text.delta") (JsonPair "delta" "\"bad\","))))))
        ($_ (nt:require boolean-index-stops
              (ot:mixed-output "x2" (JsonObject ((JsonPair "type" "response.output_text.delta") (JsonPair "item_id" "msg")
                                                 (JsonPair "output_index" True) (JsonPair "content_index" 0) (JsonPair "delta" "\"bad\","))))))
        ($_ (nt:require other-output-stops
              (ot:mixed-output "x3" (JsonObject ((JsonPair "type" "response.output_text.delta") (JsonPair "item_id" "other")
                                                 (JsonPair "output_index" 1) (JsonPair "content_index" 0) (JsonPair "delta" "\"bad\","))))))
        ; Bounds: a line, an event, the field's text and the response bound.
        ($_ (nt:require long-line-stops (ot:bounded? "b1" 100000 (ot:copies "x" 65537))))
        ($_ (nt:require long-event-stops
              (ot:bounded? "b2" 100000 (str:concat "data: " (str:concat (ot:copies "x" 65530) "\ndata: xxxxx\n")))))
        ($_ (nt:require long-field-stops (ot:bounded? "b3" 100000 (ot:content (ot:copies "x" 17000)))))
        ; A field that ends past 16 KiB is no note, though its comma arrives.
        ($_ (nt:require late-comma-no-note
              (ot:bounded? "b5" 100000 (ot:content (str:concat "{\"commentary\": \"" (str:concat (ot:copies "x" 16380) "\", \"c"))))))
        ($_ (nt:require response-bound-stops (ot:bounded? "b4" 4 (ot:content "x"))))
        ; An event's data on two lines.
        ($before-l (ot:count)) ($_ (ot:call "l1"))
        ($_ (ot:feed "l1" "data: {\"id\":\"reply\",\ndata: \"choices\":[{\"index\":0,\"delta\":{\"content\":\"{\\\"commentary\\\":\\\"ready\\\",\"}}]}\n\n" 100000))
        ($_ (nt:require multiline-data (nt:equal (ot:since $before-l) ("ready"))))
        ; A read that does not continue the last, or of a failed call, stops it.
        ($_ (ot:call "c1"))
        ($_ (observer:feed "c1" 5 "abc" waiting))
        ($_ (nt:require gap-stops (ot:stopped? "c1")))
        ($_ (ot:call "c2"))
        ($_ (observer:feed "c2" 3 "abc" failed))
        ($_ (nt:require failed-read-stops (ot:stopped? "c2")))
        ($_ (ot:call "c3"))
        ($_ (observer:feed "c3" 3 "abc" complete))
        ($_ (nt:require complete-read-continues (nt:equal (observer:offset "c3") 3)))
        ; Only Responses and streamed chat completions are read.
        ($_ (ot:call "n1" "anthropic" True 1048576))
        ($_ (nt:require anthropic-not-read (ot:stopped? "n1")))
        ($_ (ot:call "n2" "chat-completions" False 1048576))
        ($_ (nt:require unstreamed-chat-not-read (ot:stopped? "n2")))
        ; ... but publish their accepted commentary, once.
        ($before-n (ot:count))
        ($_ (nt:require accepted-published
              (nt:equal (observer:decision "n2" (CommentaryAcceptedV1 "((nop))" (CommentaryPublishV1 "Checked."))) 1)))
        ($_ (nt:require accepted-once
              (nt:equal (observer:decision "n2" (CommentaryAcceptedV1 "((nop))" (CommentaryPublishV1 "Again."))) 0)))
        ($_ (nt:require accepted-note (nt:equal (ot:since $before-n) ("Checked."))))
        ; No note for a rejection, an output without one, or blank commentary;
        ; blank commentary is not an offer.
        ($_ (ot:call "d1"))
        ($_ (nt:require rejected-no-note (nt:equal (observer:decision "d1" (CommentaryRejectedV1 incomplete-output)) 0)))
        ($_ (nt:require unpublished-no-note (nt:equal (observer:decision "d1" (CommentaryAcceptedV1 "((nop))" CommentaryNoPublicationV1)) 0)))
        ($_ (nt:require blank-no-note (nt:equal (observer:publish "d1" " \n\t\u{3000}") 0)))
        ($_ (nt:require after-blank-offered (nt:equal (observer:publish "d1" "Now.") 1)))
        ; Delivered: the call's records are gone.
        ($_ (ot:call "e1"))
        ($_ (observer:end "e1"))
        ($_ (nt:require ended-not-read (ot:stopped? "e1")))
        ($_ (nt:require ended-no-note (nt:equal (observer:publish "e1" "Late.") 0)))
        ; A new sharing epoch refuses the old call's note, which is not offered again.
        ($_ (ot:call "q1"))
        ($_ (ot:sharing "off")) ($_ (ot:sharing "on"))
        ($before-q (ot:count))
        ($_ (nt:require old-epoch-refused (nt:equal (observer:publish "q1" "old call") 0)))
        ($_ (nt:require refused-not-retried (nt:equal (observer:publish "q1" "old call") 0)))
        ($_ (nt:require nothing-in-the-new-epoch (nt:equal (ot:since $before-q) ())))
        ; A call that began with sharing off publishes nothing, even once it is on.
        ($_ (ot:sharing "off"))
        ($_ (ot:call "q2"))
        ($_ (ot:sharing "on"))
        ($_ (nt:require off-at-claim-drops (nt:equal (observer:publish "q2" "old call") 0)))
        ($_ (nt:require off-at-claim-not-read (ot:stopped? "q2"))))
   (ot:ended))

; The field of a cut prefix, as the host's Publication.prefix read it.
!(let* (($_ (nt:require field-plain (nt:equal (observer:field "{\"commentary\": \"x\",") "x")))
        ($_ (nt:require field-python-whitespace
              (nt:equal (observer:field " \u{a0}{\u{3000}\"commentary\"\u{2028}:\u{85}\"x\" \u{a0},") "x")))
        ($_ (nt:require field-escaped-key (nt:equal (observer:field "{\"comm\\u0065ntary\":\"y\",") "y")))
        ($_ (nt:require field-escapes (nt:equal (observer:field "{\"commentary\":\"a\\\"b\\\\\\n\\ud83d\\udc08\",") "a\"b\\\n🐈")))
        ($_ (nt:require field-other-key (nt:equal (observer:field "{\"reasoning\":\"private\", \"commentary\":\"ignored\",") none)))
        ($_ (nt:require field-commands-first (nt:equal (observer:field "{\"commands\":\"((send \\\"private\\\"))\",") none)))
        ($_ (nt:require field-prose (nt:equal (observer:field "private prose ((nop)),") none)))
        ($_ (nt:require field-number (nt:equal (observer:field "{\"commentary\": 5,") none)))
        ($_ (nt:require field-two-strings (nt:equal (observer:field "{\"commentary\": \"a\" \"b\",") none)))
        ($_ (nt:require field-control-character (nt:equal (observer:field "{\"commentary\": \"a\tb\",") none)))
        ($_ (nt:require field-no-colon (nt:equal (observer:field "{\"commentary\" \"x\",") none))))
   (ot:ended))

!(if (== (get-state &ot-ended) 2) (println! UNIT_COMMENTARY_OBSERVER_OK)
   (println! (FAIL blocks-ended (get-state &ot-ended))))
