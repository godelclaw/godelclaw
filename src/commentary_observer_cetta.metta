; The commentary observer of a command turn: the model's authored commentary,
; published as a note of the worklog (src/worklog_sink_cetta.metta), once a
; call at most.
;
; - A call with commentary on reads the worklog's state at its claim. A call
;   that begins while sharing is off publishes nothing; its note is accepted
;   only in the sharing epoch the call began in (the worklog checks the epoch
;   under its own lock).
; - The note comes from the stream, once the answer's text opens with
;   {"commentary": "<text>", and that first field is complete; else from the
;   accepted output at the end (observer:decision). Blank commentary is no
;   note, and a note the worklog refused or failed is not offered again.
; - A Responses call, and a chat-completions call that streams, is read from
;   the observer's own offset while it waits. The body is framed as SSE and
;   only the answer's text deltas are taken, in order: reasoning, tool calls,
;   refusals and other output items are never sources. The observer stops at
;   its first malformed, mixed or failed event, and at a bound: the call's
;   response bound, 64 KiB a line or an event, 65536 events, or 16 KiB of
;   answer text before the field ends. A stopped observer changes nothing
;   else: the answer, its program and the call go on.
; - Nothing here fails a call.
;
; Entries: observer:begin at the claim; observer:offset, observer:feed and
; observer:stop while the call waits; observer:decision when the output is
; accepted; observer:end when the call is delivered.
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library re))
!(import! &self (library system))
!(import! &self ./stream_sse.metta)
; The worklog, and the Python values its events parse to (erv:*), are loaded
; only where commentary is on: no call without commentary reaches them.
!(if (== (system:getenv-or-default "METTACLAW_MODEL_COMMENTARY" "0") "1")
     (import! &self ./worklog_sink_cetta.metta) True)

; A call's publication: the sharing epoch it began in, and whether it has
; offered its note. A call's stream observer: its protocol, the response
; bound, the body offset read, the SSE framing, the events taken, whether the
; answer finished, the answer's identity (none before its first event), and
; the commentary prefix. One space for each kind of record.
!(bind! &observer-calls (new-space))
!(bind! &observer-streams (new-space))
; The bytes that end a field's prefix or change its JSON string state.
!(bind! &observer-delimiters (new-state (re:compile "[\",\\\\]")))
!(bind! &observer-string-delimiters (new-state (re:compile "[\"\\\\]")))

;; ------------------------------------------------------------ the call

; At the claim of a call with commentary on. $attempt is the call's
; correlation, $stream whether its prepared request streams, $cap its
; response bound. True, whatever the worklog answers.
(= (observer:begin $attempt $protocol $stream $cap)
   (let $_ (observer:end $attempt)
     (case (catch (worklog:status))
       (((ErSinkStatus True $epoch) (observer:open $attempt $epoch $protocol $stream $cap))
        ($off True)))))
; add-atom keeps its argument as written: each field is computed first.
(= (observer:open $attempt $epoch $protocol $stream $cap)
   (let $_ (add-atom &observer-calls (ObserverCallV1 $attempt $epoch False))
     (if (or (== $protocol "responses") (and (== $protocol "chat-completions") $stream))
       (let* (($framing (sse:new)) ($prefix (observer:prefix-new))
              ($_ (add-atom &observer-streams (ObserverStreamV1 $attempt $protocol $cap 0 $framing 0 False none $prefix))))
         True)
       True)))

; The call's note, if it has offered none: 1 when the worklog kept it, else 0.
(= (observer:publish $attempt $text)
   (case (collapse (match &observer-calls (ObserverCallV1 $attempt $epoch $offered) ($epoch $offered)))
     (((($open-epoch False)) (observer:offer $attempt $open-epoch $text))
      ($none-open 0))))
(= (observer:offer $attempt $epoch $text)
   (if (== (str:strip-python $text) "") 0
     (let* (($_ (remove-atom &observer-calls (ObserverCallV1 $attempt $epoch False)))
            ($_ (add-atom &observer-calls (ObserverCallV1 $attempt $epoch True))))
       (case (catch (worklog:add $text $epoch)) ((1 1) ($refused 0))))))

; An accepted output's commentary, when the stream offered none.
(= (observer:decision $attempt $decision)
   (case $decision
     (((CommentaryAcceptedV1 $program (CommentaryPublishV1 $text)) (observer:publish $attempt $text))
      ($other 0))))

; The call was delivered: its records are gone.
(= (observer:end $attempt)
   (let* (($streams (observer:stop $attempt))
          ($calls (collapse (match &observer-calls (ObserverCallV1 $attempt $epoch $offered)
                    (remove-atom &observer-calls (ObserverCallV1 $attempt $epoch $offered))))))
     True))

;; ------------------------------------------------------------ the stream

; The body offset the call's stream observer reads from, or none: the call
; has none, or it stopped.
(= (observer:offset $attempt)
   (case (collapse (match &observer-streams (ObserverStreamV1 $attempt $p $c $offset $f $e $d $i $x) $offset))
     ((() none) ($offsets (car-atom $offsets)))))
(= (observer:stop $attempt)
   (let $_ (collapse (match &observer-streams (ObserverStreamV1 $attempt $p $c $o $f $e $d $i $x)
             (remove-atom &observer-streams (ObserverStreamV1 $attempt $p $c $o $f $e $d $i $x))))
     1))

; The body was read on to $next: $text must continue where the last read
; ended, while the call is waiting or complete. Always 1.
(= (observer:feed $attempt $next $text $phase)
   (case (collapse (match &observer-streams (ObserverStreamV1 $attempt $p $c $o $f $e $d $i $x)
                     (ObserverStreamV1 $attempt $p $c $o $f $e $d $i $x)))
     ((($stream) (observer:read $stream $next $text $phase))
      ($none 1))))
(= (observer:read (ObserverStreamV1 $attempt $protocol $cap $offset $framing $events $finished $identity $prefix)
                  $next $text $phase)
   (if (not (and (or (== $phase waiting) (== $phase complete))
                 (== $next (+ $offset (str:byte-length $text)))))
     (observer:stop $attempt)
     (if (> $next $cap) (observer:stop $attempt)
       (case (sse:feed $framing $text 65536)
         (((SSEBatch $framed $frames)
           (observer:frames $attempt $protocol $cap $next $framed $frames $events $finished $identity $prefix))
          ($unframed (observer:stop $attempt)))))))

; Each event in order: the observer stops at the first it cannot take, and
; when the commentary's first field ends; else it keeps its new state.
(= (observer:frames $attempt $protocol $cap $offset $framing $frames $events $finished $identity $prefix)
   (if (== $frames ())
     (let* (($_ (observer:stop $attempt))
            ($_ (add-atom &observer-streams
                  (ObserverStreamV1 $attempt $protocol $cap $offset $framing $events $finished $identity $prefix))))
       1)
     (if (or (>= $events 65536) $finished) (observer:stop $attempt)
       (let (SSEEvent $name $data) (car-atom $frames)
         (observer:step $attempt $protocol $cap $offset $framing (cdr-atom $frames) (+ $events 1) $prefix
           (observer:event $protocol $name $data $identity))))))
(= (observer:step $attempt $protocol $cap $offset $framing $frames $events $prefix $event)
   (case $event
     (((ObserverEventV1 $identity none $finished)
       (observer:frames $attempt $protocol $cap $offset $framing $frames $events $finished $identity $prefix))
      ((ObserverEventV1 $text-identity $text $text-finished)
       (observer:prefix-step $attempt $protocol $cap $offset $framing $frames $events $text-finished $text-identity
         (observer:prefix-feed $prefix $text)))
      ($rejected (observer:stop $attempt)))))
(= (observer:prefix-step $attempt $protocol $cap $offset $framing $frames $events $finished $identity $outcome)
   (case $outcome
     (((PrefixGoingV1 $going)
       (observer:frames $attempt $protocol $cap $offset $framing $frames $events $finished $identity $going))
      ((PrefixCutV1 $cut)
       (let $_ (observer:publish-field $attempt (observer:field $cut)) (observer:stop $attempt)))
      ($over (observer:stop $attempt)))))
(= (observer:publish-field $attempt $field)
   (if (== $field none) 0 (observer:publish $attempt $field)))

;; ------------------------------------------------------------ one event

; (ObserverEventV1 identity text finished), where text is none or the
; answer's text delta; or ObserverRejectedV1.
(= (observer:event $protocol $name $data $identity)
   (if (== $data "[DONE]") (ObserverEventV1 $identity none True)
     (observer:parsed $protocol $name (json:parse-python $data) $identity)))
(= (observer:parsed $protocol $name $value $identity)
   (case $value
     (((JsonObject $pairs)
       (if (or (== $name "error") (erv:has $value "error")) ObserverRejectedV1
         (if (== $protocol "chat-completions") (observer:chat $value $identity)
           (observer:response $name $value $identity))))
      ($other ObserverRejectedV1))))

; A chat-completions chunk: one identity throughout, at most one choice, at
; index 0, whose delta is the assistant's, with no refusal or tool call.
(= (observer:chat $value $identity)
   (let $id (erv:get $value "id" JsonNull)
     (if (not (observer:named? $id)) ObserverRejectedV1
       (if (and (not (== $identity none)) (not (== $identity $id))) ObserverRejectedV1
         (observer:choices (erv:get $value "choices" ObserverMissingV1) $id)))))
(= (observer:choices $choices $id)
   (case $choices
     (((JsonArray ()) (ObserverEventV1 $id none False))
      ((JsonArray ($choice)) (observer:choice $choice $id))
      ($other ObserverRejectedV1))))
(= (observer:choice $choice $id)
   (case $choice
     (((JsonObject $pairs)
       (let $index (erv:get $choice "index" JsonNull)
         (if (not (and (observer:int? $index) (== $index 0))) ObserverRejectedV1
           (observer:delta (erv:get $choice "delta" ObserverMissingV1)
                           (not (== (erv:get $choice "finish_reason" JsonNull) JsonNull)) $id))))
      ($other ObserverRejectedV1))))
(= (observer:delta $delta $finished $id)
   (case $delta
     (((JsonObject $pairs)
       (let* (($role (erv:get $delta "role" JsonNull)) ($content (erv:get $delta "content" JsonNull)))
         (if (not (or (== $role JsonNull) (== $role "assistant"))) ObserverRejectedV1
           (if (or (erv:truth (erv:get $delta "refusal" JsonNull)) (erv:truth (erv:get $delta "tool_calls" JsonNull)))
             ObserverRejectedV1
             (if (== $content JsonNull) (ObserverEventV1 $id none $finished)
               (if (== (get-type $content) String) (ObserverEventV1 $id $content $finished) ObserverRejectedV1))))))
      ($other ObserverRejectedV1))))

; A Responses event: its type, or the SSE event's name without one; a named
; event must agree with its type. Only output text deltas are text, all of
; one output part.
(= (observer:response $name $value $identity)
   (let $kind (erv:get $value "type" $name)
     (if (and (not (== $name "")) (not (== $name $kind))) ObserverRejectedV1
       (if (or (== $kind "response.failed") (or (== $kind "response.incomplete") (== $kind "error"))) ObserverRejectedV1
         (if (== $kind "response.output_text.delta") (observer:output-delta $value $identity)
           (ObserverEventV1 $identity none (== $kind "response.completed")))))))
(= (observer:output-delta $value $identity)
   (let* (($item (erv:get $value "item_id" JsonNull)) ($output (erv:get $value "output_index" JsonNull))
          ($part (erv:get $value "content_index" JsonNull)))
     (if (not (and (observer:named? $item) (and (observer:index? $output) (observer:index? $part)))) ObserverRejectedV1
       (let $current ($item $output $part)
         (if (and (not (== $identity none)) (not (== $identity $current))) ObserverRejectedV1
           (let $delta (erv:get $value "delta" ObserverMissingV1)
             (if (== (get-type $delta) String) (ObserverEventV1 $current $delta False) ObserverRejectedV1)))))))

(= (observer:named? $value) (if (== (get-type $value) String) (not (== $value "")) False))
(= (observer:int? $value) (if (== (get-type $value) Number) (== (erv:typename $value) "int") False))
(= (observer:index? $value) (if (observer:int? $value) (>= $value 0) False))

;; ------------------------------------------------------------ the prefix

; The answer's text up to its first comma outside a JSON string: the bytes
; seen, whether a string is open, whether its next byte is escaped, and the
; text so far, last first. A delimiter is ASCII, so a byte scan finds the
; same ones a character scan does.
(= (observer:prefix-new) (PrefixV1 0 False False ()))
; (PrefixGoingV1 prefix), (PrefixCutV1 text) at the comma, or PrefixOverV1
; past 16 KiB without one.
(= (observer:prefix-feed (PrefixV1 $size $quoted $escaped $parts) $text)
   (let* (($length (str:byte-length $text))
          ($found (re:find-all (get-state &observer-delimiters) $text))
          ($scan (observer:scan $text (- 16384 $size) $quoted (if $escaped 0 -1) (observer:starts $found))))
     (case $scan
       (((ScanCutV1 $at)
         (PrefixCutV1 (str:concat (str:join "" (observer:reverse $parts ())) (str:byte-slice $text 0 (+ $at 1)))))
        ((ScanOpenV1 $open $escape-at)
         (if (> (+ $size $length) 16384) PrefixOverV1
           (PrefixGoingV1 (PrefixV1 (+ $size $length) $open (== $escape-at $length) (cons-atom $text $parts)))))
        ($beyond PrefixOverV1)))))
(= (observer:starts $found)
   (case $found (((re:matches $list) $list) ($none LNil))))
; The delimiters' offsets in order; $escape-at is the offset of a byte an
; open string's backslash escapes (-1 for none), $budget the bytes this text
; may add. A comma at or past the budget is past 16 KiB.
(= (observer:scan $text $budget $quoted $escape-at $matches)
   (case $matches
     ((LNil (ScanOpenV1 $quoted $escape-at))
      ((LCons (re:match $at $end $captures) $rest)
       (if (>= $at $budget) ScanBeyondV1
         (if (== $at $escape-at) (observer:scan $text $budget $quoted -1 $rest)
           (observer:scan-byte $text $budget $quoted $at (str:byte-slice $text $at $end) $rest)))))))
(= (observer:scan-byte $text $budget $quoted $at $byte $rest)
   (if $quoted
     (if (== $byte "\\") (observer:scan $text $budget True (+ $at 1) $rest)
       (if (== $byte "\"") (observer:scan $text $budget False -1 $rest)
         (observer:scan $text $budget True -1 $rest)))
     (if (== $byte ",") (ScanCutV1 $at)
       (if (== $byte "\"") (observer:scan $text $budget True -1 $rest)
         (observer:scan $text $budget False -1 $rest)))))
(= (observer:reverse $items $out)
   (if (== $items ()) $out (observer:reverse (cdr-atom $items) (cons-atom (car-atom $items) $out))))

; The commentary of a cut prefix, or none: {"commentary": "<text>", with
; Python's str.lstrip() whitespace around each part, as the host observer's
; json.JSONDecoder.raw_decode read it.
(= (observer:field $cut)
   (let $source (observer:lstrip $cut)
     (if (str:starts-with $source "{") (observer:key (observer:lstrip (observer:after $source 1))) none)))
(= (observer:key $text)
   (case (observer:string $text)
     (((StringTokenV1 $token $rest)
       (if (== (json:parse-python $token) "commentary")
         (let $colon (observer:lstrip $rest)
           (if (str:starts-with $colon ":") (observer:value (observer:lstrip (observer:after $colon 1))) none))
         none))
      ($no-key none))))
(= (observer:value $text)
   (case (observer:string $text)
     (((StringTokenV1 $token $rest)
       (let $value (json:parse-python $token)
         (if (and (== (get-type $value) String) (str:starts-with (observer:lstrip $rest) ",")) $value none)))
      ($no-value none))))
; A JSON string token at the start of $text, to its closing quote, and the
; text after it; or none.
(= (observer:string $text)
   (if (not (str:starts-with $text "\"")) none
     (let $found (re:find-all (get-state &observer-string-delimiters) $text)
       (observer:closing $text -1 (observer:after-opening (observer:starts $found))))))
(= (observer:after-opening $matches) (case $matches (((LCons $opening $rest) $rest) ($empty LNil))))
(= (observer:closing $text $escape-at $matches)
   (case $matches
     ((LNil none)
      ((LCons (re:match $at $end $captures) $rest)
       (if (== $at $escape-at) (observer:closing $text -1 $rest)
         (if (== (str:byte-slice $text $at $end) "\\") (observer:closing $text (+ $at 1) $rest)
           (StringTokenV1 (str:byte-slice $text 0 $end) (observer:after $text $end))))))))
(= (observer:after $text $start) (str:byte-slice $text $start (str:byte-length $text)))
; Python's str.lstrip(): the text after its leading whitespace.
(= (observer:lstrip $text)
   (let $stripped (str:strip-python (str:concat $text "x"))
     (str:byte-slice $stripped 0 (- (str:byte-length $stripped) 1))))
