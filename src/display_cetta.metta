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
; Entries: display:emit, display:event, display:tool-finished.
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

; stream_sink.event: an executor's record of a tool, a plan, a patch, the
; steering, maintenance or the session. A dispatch or a start names the
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

; stream_sink.tool_finished: the tool's end, with the last 12000 bytes of its
; output artifact.
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
