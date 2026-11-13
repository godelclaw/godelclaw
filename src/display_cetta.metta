; The local work display, written natively: each process appends presentation
; records, one JSON object a line, to <stream dir>/<session>.<producer>.jsonl,
; and the viewers read them (stream_sink.Projection, the console). The format
; is stream_sink.Writer's, so both writers' files read alike.
;
; - Best effort: an unconfigured display writes nothing, and a record that
;   cannot be written is dropped. Nothing here fails a call or a command.
; - The console's control.json switches the display off or starts a new
;   epoch; a record of another epoch is dropped. A tool's records keep the
;   epoch its dispatch or start saw until the tool finishes.
; - Each record carries what the Python writer adds: the version, the session,
;   the instance, the parent session, this process's producer and sequence,
;   the process and its start, and the time.
; - Records are appended as they come, without the Python writer's queue and
;   thread: nothing is dropped for want of room, so no record carries a gap.
;
; Entries: display:emit, display:event and display:tool-finished for the
; executor; display:call-begin, -wire, -readable?, -offset, -advance,
; -projected, -unavailable and -final for a provider call.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library system))

!(bind! &display-sequence (new-state 0))
!(bind! &display-unsafe (new-state (re:compile "[^A-Za-z0-9_.-]")))
; The producer of this process, made at its first record, and the epochs of
; the tools in flight.
!(bind! &display-producer (new-space))
!(bind! &display-items (new-space))

(= (display:root) (str:strip-python (system:getenv-or-default "METTACLAW_STREAM_DIR" "")))
(= (display:configured?) (not (== (display:root) "")))
(= (display:session)
   (let $scope (system:getenv-or-default "METTACLAW_ACTIVITY_CALL_SCOPE" "")
     (if (== $scope "") (system:getenv-or-default "METTACLAW_INSTANCE" "agent") $scope)))
; A file name's part: other characters become "_", at most 120 of them.
(= (display:safe $text)
   (let $cut (str:char-slice (re:replace-literal (get-state &display-unsafe) $text "_") 0 120)
     (if (== $cut "") "agent" $cut)))

; (enabled epoch) of control.json: no file is (True "initial"); one that
; cannot be read, or lacks a field, is (False "invalid").
(= (display:control $root)
   (let $path (path:join $root "control.json")
     (if (not (fs:exists $path)) (True "initial")
       (case (catch (json-parse (fs:read-text $path)))
         (((JsonObject $pairs)
           (case ((display:last $pairs "enabled" none) (display:last $pairs "epoch" none))
             (((none $epoch) (False "invalid")) (($enabled none) (False "invalid"))
              (($enabled $epoch) ((display:truthy? $enabled) (display:epoch-text $epoch))))))
          ($other (False "invalid")))))))
(= (display:last $pairs $name $found)
   (if (== $pairs ()) $found
     (case (car-atom $pairs)
       (((JsonPair $name $value) (display:last (cdr-atom $pairs) $name $value))
        ($other (display:last (cdr-atom $pairs) $name $found))))))
(= (display:truthy? $value)
   (case $value
     ((JsonNull False) ((JsonBool false) False) ((JsonString "") False) ((JsonArray ()) False)
      ((JsonObject ()) False)
      ((JsonNumber $raw) (case (catch (str:string->number $raw)) (((Error $w $y) True) ($n (not (== $n 0))))))
      ($other True))))
; Python's str() of the epoch value.
(= (display:epoch-text $value)
   (case $value
     (((JsonString $text) $text) ((JsonNumber $raw) $raw) ((JsonBool true) "True") ((JsonBool false) "False")
      (JsonNull "None") ($other (json-stringify $other)))))

; The process's start time in clock ticks, field 22 of /proc/<pid>/stat, or
; JsonNull when it cannot be read.
(= (display:process-start)
   (case (catch (fs:read-text (str:concat "/proc/" (str:concat (repr (system:pid)) "/stat"))))
     (((Error $w $y) JsonNull)
      ($stat
       (let $fields (str:split-whitespace (display:after-last (str:split ")" $stat)))
         (if (< (size-atom $fields) 20) JsonNull (JsonString (index-atom $fields 19))))))))
(= (display:after-last $parts) (if (== (cdr-atom $parts) ()) (car-atom $parts) (display:after-last (cdr-atom $parts))))
(= (display:producer)
   (case (collapse (match &display-producer (DisplayProducerV1 $id $start) ($id $start)))
     ((() (let* (($id (system:uuid4-hex)) ($start (display:process-start))
                 ($_ (add-atom &display-producer (DisplayProducerV1 $id $start))))
            ($id $start)))
      ($found (car-atom $found)))))

; One record: 1 when written, 0 when the display is off or unconfigured, the
; record is of another epoch, or it cannot be written. $pairs are the event's
; JsonPairs, with its "kind" first.
(= (display:emit $pairs)
   (let $root (display:root)
     (if (== $root "") 0
       (case (catch (display:write $root $pairs)) ((1 1) ($other 0))))))
(= (display:write $root $pairs)
   (let* ((($producer $start) (display:producer))
          ($sequence (let $_ (change-state! &display-sequence (+ (get-state &display-sequence) 1))
                       (get-state &display-sequence)))
          ($session (display:session))
          (($enabled $epoch) (display:control $root))
          ($own (display:last $pairs "epoch" none)))
     (if (or (not $enabled) (not (or (== $own none) (== $own (JsonString $epoch))))) 0
       (let* (($record (JsonObject (display:append $pairs
                         ((JsonPair "version" (JsonNumber "1")) (JsonPair "session" (JsonString $session))
                          (JsonPair "instance" (JsonString (system:getenv-or-default "METTACLAW_INSTANCE" "agent")))
                          (JsonPair "parent_session" (JsonString (system:getenv-or-default "METTACLAW_STREAM_PARENT" "")))
                          (JsonPair "producer" (JsonString $producer)) (JsonPair "sequence" (JsonNumber (repr $sequence)))
                          (JsonPair "pid" (JsonNumber (repr (system:pid)))) (JsonPair "process_start" $start)
                          (JsonPair "time" (JsonNumber (repr (system:wall-time))))
                          (if (== $own none) (JsonPair "epoch" (JsonString $epoch)) ())))))
              ($_ (fs:make-directories $root 511))
              ($path (path:join $root (str:concat (display:safe $session) (str:concat "." (str:concat $producer ".jsonl"))))))
         (case (fs:append-text $path (str:concat (json-stringify $record) "\n"))
           (((Error $w $y) 0) ($written 1)))))))
; The record's pairs: the event's, then the writer's, without the absent ones.
(= (display:append $front $back)
   (if (== $front ()) (display:drop-empty $back)
     (if (== (car-atom $front) ()) (display:append (cdr-atom $front) $back)
       (cons-atom (car-atom $front) (display:append (cdr-atom $front) $back)))))
(= (display:drop-empty $pairs)
   (if (== $pairs ()) ()
     (let $rest (display:drop-empty (cdr-atom $pairs))
       (if (== (car-atom $pairs) ()) $rest (cons-atom (car-atom $pairs) $rest)))))

;; ------------------------------------------------------------ tool records

; The epoch a tool's records keep, from its dispatch or start to its end.
(= (display:item-epoch $item)
   (case (collapse (match &display-items (DisplayItemV1 $item $epoch) $epoch)) ((() none) ($epochs (car-atom $epochs)))))
; add-atom keeps its argument as written: the epoch is computed first.
(= (display:hold-epoch $item)
   (if (== (display:item-epoch $item) none)
     (let $epoch (display:current-epoch) (add-atom &display-items (DisplayItemV1 $item $epoch)))
     ()))
(= (display:release-epoch $item)
   (case (display:item-epoch $item)
     ((none (display:current-epoch))
      ($epoch (let $_ (remove-atom &display-items (DisplayItemV1 $item $epoch)) $epoch)))))
(= (display:current-epoch) (let ($enabled $epoch) (display:control (display:root)) $epoch))

; Python's str() of a value that is not text.
(= (display:text $value) (if (== (get-type $value) String) $value (repr $value)))

; An executor's record of a tool, a plan, a patch, the steering, maintenance
; or the session. A dispatch or a start names the
; command, which the record carries instead of text.
(= (display:event $kind $item $text) (display:event $kind $item $text "" ""))
(= (display:event $kind $item $text $status) (display:event $kind $item $text $status ""))
(= (display:event $kind $item $text $status $name)
   (if (not (display:configured?)) 0
     (let* (($command (or (== $kind "tool.dispatching") (== $kind "tool.started")))
            ($_ (if $command (display:hold-epoch $item) ()))
            ($epoch (display:item-epoch $item)))
       (display:emit
         ((JsonPair "kind" (JsonString $kind)) (JsonPair "item" (JsonString (display:text $item)))
          (JsonPair "text" (JsonString (if $command "" (display:text $text))))
          (JsonPair "status" (JsonString (display:text $status)))
          (JsonPair "name" (JsonString (display:text $name))) (JsonPair "attempt" (JsonString ""))
          (JsonPair "source" (JsonString "executor"))
          (if $command (JsonPair "command" (JsonString (display:text $text))) ())
          (if (== $epoch none) () (JsonPair "epoch" (JsonString $epoch))))))))

; A tool's end, with the last 12000 bytes of its output artifact.
(= (display:tool-finished $item $state $code $artifact)
   (if (not (display:configured?)) 0
     (let* (($epoch (display:release-epoch $item))
            ($tail (case (catch (fs:tail-text $artifact 12000))
                     (((fs:tail $size $cut $text) $text) ($other "Output artifact unavailable.")))))
       (display:emit
         ((JsonPair "kind" (JsonString "tool.finished")) (JsonPair "item" (JsonString $item))
          (JsonPair "source" (JsonString "executor")) (JsonPair "epoch" (JsonString $epoch))
          (JsonPair "status" (JsonString $state)) (JsonPair "exit_code" (JsonNumber (repr $code)))
          (JsonPair "artifact" (JsonString $artifact)) (JsonPair "text" (JsonString $tail)))))))

;; ------------------------------------------------------------ provider calls

; A provider call's display, kept from its claim to its end under its attempt
; (the owner's correlation): the epoch it began in, the bytes of its body
; observed, whether observation stopped, and whether it is a compaction,
; which is maintenance: nothing of it is readable.
!(bind! &display-calls (new-space))
(= (display:call $attempt)
   (case (collapse (match &display-calls (DisplayCallV1 $attempt $epoch $offset $stopped $compaction)
                     ($epoch $offset $stopped $compaction)))
     ((() none) ($calls (car-atom $calls)))))
(= (display:call-keep $attempt $epoch $offset $stopped $compaction)
   (let $_ (display:call-drop $attempt)
     (add-atom &display-calls (DisplayCallV1 $attempt $epoch $offset $stopped $compaction))))
(= (display:call-drop $attempt)
   (case (display:call $attempt)
     ((none ())
      (($epoch $offset $stopped $compaction)
       (remove-atom &display-calls (DisplayCallV1 $attempt $epoch $offset $stopped $compaction))))))

; A claimed call opens its attempt, a compaction its maintenance record.
(= (display:call-begin $attempt $compaction $provider $purpose)
   (if (not (display:configured?)) 0
     (let* (($epoch (display:current-epoch))
            ($kept (display:call-keep $attempt $epoch 0 False $compaction)))
       (display:emit
         (if $compaction
           ((JsonPair "kind" (JsonString "maintenance")) (JsonPair "attempt" (JsonString $attempt))
            (JsonPair "item" (JsonString $attempt)) (JsonPair "text" (JsonString "Compacting task history"))
            (JsonPair "status" (JsonString "running")))
           ((JsonPair "kind" (JsonString "attempt.open")) (JsonPair "attempt" (JsonString $attempt))
            (JsonPair "item" (JsonString $attempt)) (JsonPair "purpose" (JsonString $purpose))
            (JsonPair "provider" (JsonString $provider)) (JsonPair "model" (JsonString ""))
            (JsonPair "epoch" (JsonString $epoch)) (JsonPair "status" (JsonString "awaiting"))))))))

; A streamed call on the wire shows its model.
(= (display:call-wire $attempt $provider $model)
   (case (display:call $attempt)
     ((none 0)
      (($epoch $offset $stopped $compaction)
       (display:emit ((JsonPair "kind" (JsonString "attempt.open")) (JsonPair "attempt" (JsonString $attempt))
                      (JsonPair "item" (JsonString $attempt)) (JsonPair "model" (JsonString $model))
                      (JsonPair "provider" (JsonString $provider)) (JsonPair "status" (JsonString "awaiting"))
                      (JsonPair "epoch" (JsonString $epoch))))))))

; Whether the call's streamed text is shown: it began, is no compaction, its
; observation has not stopped, and the console still shows its epoch.
(= (display:call-readable? $attempt)
   (case (display:call $attempt)
     ((none False)
      (($epoch $offset $stopped $compaction)
       (and (not $stopped) (and (not $compaction) (== (display:control (display:root)) (True $epoch))))))))
(= (display:call-offset $attempt)
   (case (display:call $attempt) ((none 0) (($epoch $offset $stopped $compaction) $offset))))
; The body was read on to $next: it must continue where the last read ended.
(= (display:call-advance $attempt $next $text)
   (case (display:call $attempt)
     ((none 0)
      (($epoch $offset $stopped $compaction)
       (let $expected (+ $offset (str:byte-length $text))
         (if (== $next $expected)
           (let $kept (display:call-keep $attempt $epoch $expected $stopped $compaction) 1)
           (let $gap (display:call-unavailable $attempt "incremental observation unavailable") 0)))))))
; One projected value of the provider's stream: readable text of the
; commentary or the reasoning summary, under a named item.
(= (display:call-projected $attempt $value)
   (if (not (display:call-readable? $attempt)) 0
     (case $value
       (((JsonObject $pairs)
         (case ((display:last $pairs "channel" JsonNull) (display:last $pairs "item" JsonNull))
           (((JsonNull (JsonString $item)) (display:call-shown $attempt $pairs $item))
            (((JsonString "commentary") (JsonString $item)) (display:call-shown $attempt $pairs $item))
            (((JsonString "reasoning-summary") (JsonString $item)) (display:call-shown $attempt $pairs $item))
            ((JsonNull $item) (display:call-unavailable $attempt "incremental observation unavailable"))
            (((JsonString "commentary") $item) (display:call-unavailable $attempt "incremental observation unavailable"))
            (((JsonString "reasoning-summary") $item) (display:call-unavailable $attempt "incremental observation unavailable"))
            ($other 0))))
        ($other (display:call-unavailable $attempt "incremental observation unavailable"))))))
(= (display:call-shown $attempt $pairs $item)
   (if (== $item "") (display:call-unavailable $attempt "incremental observation unavailable")
     (case (display:call $attempt)
       ((none 0)
        (($epoch $offset $stopped $compaction)
         (display:emit (display:append $pairs ((JsonPair "attempt" (JsonString $attempt))
                                               (JsonPair "source" (JsonString "provider"))
                                               (JsonPair "epoch" (JsonString $epoch))))))))))
; Observation stops at its first gap, which the display shows once.
(= (display:call-unavailable $attempt $why)
   (case (display:call $attempt)
     ((none 0)
      (($epoch $offset $stopped $compaction)
       (if $stopped 0
         (let $kept (display:call-keep $attempt $epoch $offset True $compaction)
           (display:emit ((JsonPair "kind" (JsonString "gap")) (JsonPair "attempt" (JsonString $attempt))
                          (JsonPair "text" (JsonString $why)) (JsonPair "source" (JsonString "observer"))
                          (JsonPair "epoch" (JsonString $epoch))))))))))

; The call's end, once. A compaction closes its maintenance
; record; another call, while the console shows its epoch, reconciles the
; answer and the reasoning summaries of $message (none for a failure) and
; ends the attempt, completed for "stop" or "tool_calls", else interrupted.
(= (display:call-final $attempt $message $reason)
   (case (display:call $attempt)
     ((none 0)
      (($epoch $offset $stopped $compaction)
       (let $dropped (display:call-drop $attempt)
         (if $compaction
           (display:emit ((JsonPair "kind" (JsonString "maintenance")) (JsonPair "attempt" (JsonString $attempt))
                          (JsonPair "item" (JsonString $attempt)) (JsonPair "text" (JsonString "Task history compaction"))
                          (JsonPair "status" (JsonString $reason))))
           (if (not (== (display:control (display:root)) (True $epoch))) 0
             (display:call-texts $attempt $epoch $message $reason))))))))
; Each case is a function of its own: a case's catch-all variable belongs to
; the whole body it is written in, so a second case in the same body would
; find it bound by the first.
(= (display:call-texts $attempt $epoch $message $reason)
   (let* (($pairs (display:pairs-of $message))
          ($answer (display:answer $attempt $epoch (display:last $pairs "content" JsonNull)))
          ($blocks (display:items-of (display:last $pairs "reasoning_details" JsonNull)))
          ($texts (display:join $answer (display:blocks $attempt $epoch $blocks 0)))
          ($completed (or (== $reason "stop") (== $reason "tool_calls"))))
     (display:emit ((JsonPair "kind" (JsonString "attempt.finished")) (JsonPair "attempt" (JsonString $attempt))
                    (JsonPair "status" (JsonString (if $completed "completed" "interrupted")))
                    (JsonPair "reason" (JsonString $reason)) (JsonPair "source" (JsonString "terminal"))
                    (JsonPair "epoch" (JsonString $epoch))
                    (JsonPair "texts" (if $completed (JsonArray $texts) JsonNull))))))
(= (display:pairs-of $value) (case $value (((JsonObject $pairs) $pairs) ($other ()))))
(= (display:items-of $value) (case $value (((JsonArray $items) $items) ($other ()))))
; The answer's text: its reconciled record, and its entry of the final texts.
(= (display:answer $attempt $epoch $content)
   (case $content
     (((JsonString $text)
       (let $reconciled
            (display:emit ((JsonPair "kind" (JsonString "text.reconcile")) (JsonPair "attempt" (JsonString $attempt))
                           (JsonPair "item" (JsonString "answer")) (JsonPair "channel" (JsonString "commentary"))
                           (JsonPair "text" (JsonString $text)) (JsonPair "source" (JsonString "terminal"))
                           (JsonPair "epoch" (JsonString $epoch))))
         ((JsonObject ((JsonPair "item" (JsonString "answer")) (JsonPair "channel" (JsonString "commentary"))
                       (JsonPair "text" (JsonString $text)))))))
      ($other ()))))
(= (display:join $front $back)
   (if (== $front ()) $back (cons-atom (car-atom $front) (display:join (cdr-atom $front) $back))))
; The readable reasoning of the message's blocks: a reasoning block's summary
; texts, a thinking block's text. Signatures and encrypted content stay out.
(= (display:blocks $attempt $epoch $blocks $index)
   (if (== $blocks ()) ()
     (display:join (display:block $attempt $epoch (car-atom $blocks) $index)
                   (display:blocks $attempt $epoch (cdr-atom $blocks) (+ $index 1)))))
(= (display:block $attempt $epoch $block $index)
   (case $block
     (((JsonObject $pairs)
       (case (display:last $pairs "type" JsonNull)
         (((JsonString "reasoning")
           (display:summaries $attempt $epoch (display:block-item $pairs $index)
             (display:items-of (display:last $pairs "summary" JsonNull)) 0))
          ((JsonString "thinking")
           (case (display:last $pairs "thinking" JsonNull)
             (((JsonString $text) (display:snapshot $attempt $epoch (repr $index) $index $text))
              ($other ()))))
          ($other ()))))
      ($other ()))))
; A reasoning block's item: its id, or "reasoning-<index>" without one.
(= (display:block-item $pairs $index)
   (case (display:last $pairs "id" JsonNull)
     (((JsonString "") (str:concat "reasoning-" (repr $index))) ((JsonString $id) $id)
      ($other (str:concat "reasoning-" (repr $index))))))
(= (display:summaries $attempt $epoch $item $summaries $part)
   (if (== $summaries ()) ()
     (display:join
       (case (car-atom $summaries)
         (((JsonObject $pairs)
           (if (== (display:last $pairs "type" JsonNull) (JsonString "summary_text"))
             (display:snapshot $attempt $epoch $item $part
               (case (display:last $pairs "text" (JsonString "")) (((JsonString $text) $text) ($other ""))))
             ()))
          ($other ())))
       (display:summaries $attempt $epoch $item (cdr-atom $summaries) (+ $part 1)))))
; One reasoning text: its snapshot record, and its entry of the final texts.
(= (display:snapshot $attempt $epoch $item $part $text)
   (let $shown (display:emit ((JsonPair "kind" (JsonString "text.snapshot")) (JsonPair "attempt" (JsonString $attempt))
                              (JsonPair "item" (JsonString $item)) (JsonPair "part" (JsonNumber (repr $part)))
                              (JsonPair "channel" (JsonString "reasoning-summary")) (JsonPair "text" (JsonString $text))
                              (JsonPair "source" (JsonString "terminal")) (JsonPair "epoch" (JsonString $epoch))))
     ((JsonObject ((JsonPair "item" (JsonString $item)) (JsonPair "part" (JsonNumber (repr $part)))
                   (JsonPair "channel" (JsonString "reasoning-summary")) (JsonPair "text" (JsonString $text)))))))

; The message and finish reason of an encoded chat-completions result.
(= (display:result-choice $encoded)
   (case (catch (json-parse $encoded))
     (((JsonObject $pairs)
       (case (display:last $pairs "choices" JsonNull)
         (((JsonArray $choices) (if (== $choices ()) none (car-atom $choices))) ($other none))))
      ($other none))))
(= (display:result-message $encoded)
   (case (display:result-choice $encoded)
     (((JsonObject $pairs) (display:last $pairs "message" none)) ($other none))))
(= (display:result-reason $encoded)
   (case (display:result-choice $encoded)
     (((JsonObject $pairs)
       (case (display:last $pairs "finish_reason" JsonNull) (((JsonString $reason) $reason) ($other "unknown"))))
      ($other "unknown"))))
