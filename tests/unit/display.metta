; Unit tests of src/display_cetta.metta; no Python. The expected records are
; those stream_sink.Writer writes for the same events, settings and console
; control. Run twice: with a display under the unit's scratch directory (the
; session "coding a/b", instance "unit", parent "parent-1"), and without one.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/display_cetta.metta)

(= (dt:control $text) (fs:write (path:join (display:root) "control.json") $text))
(= (dt:file) (let ($producer $start) (display:producer)
               (path:join (display:root) (str:concat "coding_a_b." (str:concat $producer ".jsonl")))))
(= (dt:records)
   (if (not (fs:exists (dt:file))) ()
     (dt:parse (str:split "\n" (fs:read-text (dt:file))))))
(= (dt:parse $lines)
   (if (== $lines ()) ()
     (let $rest (dt:parse (cdr-atom $lines))
       (if (== (car-atom $lines) "") $rest (cons-atom (json-parse (car-atom $lines)) $rest)))))
(= (dt:count) (size-atom (dt:records)))
(= (dt:last-record) (dt:final (dt:records)))
(= (dt:final $list) (if (== (cdr-atom $list) ()) (car-atom $list) (dt:final (cdr-atom $list))))
(= (dt:get (JsonObject $pairs) $name) (display:last $pairs $name none))
(= (dt:fields $record $names) (map-atom $names $name (dt:get $record $name)))
(= (dt:sequences $records) (map-atom $records $record (case (dt:get $record "sequence") (((JsonNumber $n) (str:string->number $n))))))
(= (dt:increasing? $numbers)
   (if (== (cdr-atom $numbers) ()) True
     (and (< (car-atom $numbers) (car-atom (cdr-atom $numbers))) (dt:increasing? (cdr-atom $numbers)))))
(= (dt:digits? $value)
   (case $value (((JsonString $text) (and (> (str:byte-length $text) 0) (str:is-all-in digit $text))) ($other False))))
(= (dt:string $value) (JsonString $value))
(= (dt:range $from $to) (if (>= $from $to) () (cons-atom $from (dt:range (+ $from 1) $to))))

!(if (not (display:configured?))
  (let* (($_ (nt:require off-event (nt:equal (display:event "plan" "plan" "text") 0)))
         ($_ (nt:require off-dispatch (nt:equal (display:event "tool.dispatching" "x" "ls" "dispatching" "exec_command") 0)))
         ($_ (nt:require off-holds-no-epoch (nt:equal (display:item-epoch "x") none)))
         ($_ (nt:require off-finish (nt:equal (display:tool-finished "x" "exited" 0 "/nonexistent") 0)))
         ($_ (nt:require off-emit (nt:equal (display:emit ((JsonPair "kind" (JsonString "plan")))) 0))))
    (println! UNIT_DISPLAY_OFF_OK))
  (let* (; The first record, without a control file: epoch "initial".
         ($_ (nt:require plan-written (nt:equal (display:event "plan" "plan" "Plan text") 1)))
         ($first (dt:last-record))
         ($_ (nt:require event-fields
               (nt:equal (dt:fields $first ("kind" "item" "text" "status" "name" "attempt" "source"))
                         ((JsonString "plan") (JsonString "plan") (JsonString "Plan text") (JsonString "")
                          (JsonString "") (JsonString "") (JsonString "executor")))))
         ($_ (nt:require writer-fields
               (nt:equal (dt:fields $first ("version" "session" "instance" "parent_session" "pid" "epoch"))
                         ((JsonNumber "1") (JsonString "coding a/b") (JsonString "unit") (JsonString "parent-1")
                          (JsonNumber (repr (system:pid))) (JsonString "initial")))))
         ($_ (nt:require producer-and-start
               (let ($producer $start) (display:producer)
                 (and (nt:equal (dt:get $first "producer") (JsonString $producer))
                      (and (== (str:byte-length $producer) 32) (dt:digits? (dt:get $first "process_start")))))))
         ($_ (nt:require time-is-a-number (case (dt:get $first "time") (((JsonNumber $t) (> (str:string->number $t) 1000000000)) ($o False)))))
         ; A dispatch names its command instead of text and holds the epoch.
         ($_ (nt:require dispatch-written (nt:equal (display:event "tool.dispatching" "x1" "ls -l" "dispatching" "exec_command") 1)))
         ($_ (nt:require dispatch-fields
               (nt:equal (dt:fields (dt:last-record) ("text" "command" "status" "name" "epoch"))
                         ((JsonString "") (JsonString "ls -l") (JsonString "dispatching") (JsonString "exec_command")
                          (JsonString "initial")))))
         ($_ (nt:require dispatch-holds-epoch (nt:equal (display:item-epoch "x1") "initial")))
         ($_ (nt:require started-keeps-the-held-epoch (nt:equal (display:event "tool.started" "x1" "ls -l" "running" "exec_command") 1)))
         ($_ (nt:require one-held-epoch-per-item (nt:equal (size-atom (collapse (match &display-items (DisplayItemV1 "x1" $e) $e))) 1)))
         ; The console switches the display off: nothing is written.
         ($_ (dt:control "{\"enabled\": false, \"epoch\": \"e1\"}"))
         ($before (dt:count))
         ($_ (nt:require off-by-control (nt:equal (display:event "plan" "plan" "hidden") 0)))
         ($_ (nt:require nothing-written-while-off (nt:equal (dt:count) $before)))
         ; A new epoch: new records carry it; the held item's records are dropped.
         ($_ (dt:control "{\"enabled\": true, \"epoch\": \"e2\"}"))
         ($_ (nt:require new-epoch-written (nt:equal (display:event "steering" "1" "Requester guidance applied" "applied") 1)))
         ($_ (nt:require new-epoch-carried (nt:equal (dt:get (dt:last-record) "epoch") (JsonString "e2"))))
         ($_ (nt:require old-item-output-dropped (nt:equal (display:event "tool.output" "x1" "late") 0)))
         ($_ (nt:require old-item-finish-dropped (nt:equal (display:tool-finished "x1" "exited" 0 "/nonexistent") 0)))
         ($_ (nt:require finish-releases-epoch (nt:equal (display:item-epoch "x1") none)))
         ; A tool of this epoch: output, then its end with the artifact's tail.
         ($artifact (path:join (nt:required-env "HOME") "x2.log"))
         ($_ (fs:write $artifact "first line\nout-tail\n"))
         ($_ (nt:require x2-dispatched (nt:equal (display:event "tool.dispatching" "x2" "make" "dispatching" "exec_command") 1)))
         ($_ (nt:require x2-output (nt:equal (display:event "tool.output" "x2" "out-tail\n") 1)))
         ($_ (nt:require output-fields
               (nt:equal (dt:fields (dt:last-record) ("kind" "text" "epoch"))
                         ((JsonString "tool.output") (JsonString "out-tail\n") (JsonString "e2")))))
         ($_ (nt:require x2-finished (nt:equal (display:tool-finished "x2" "exited" 3 $artifact) 1)))
         ($_ (nt:require finish-fields
               (nt:equal (dt:fields (dt:last-record) ("kind" "item" "source" "epoch" "status" "exit_code" "artifact" "text"))
                         ((JsonString "tool.finished") (JsonString "x2") (JsonString "executor") (JsonString "e2")
                          (JsonString "exited") (JsonNumber "3") (JsonString $artifact)
                          (JsonString "first line\nout-tail\n")))))
         ($_ (nt:require missing-artifact
               (and (nt:equal (display:tool-finished "x3" "not_started" 0 (path:join (nt:required-env "HOME") "none.log")) 1)
                    (nt:equal (dt:get (dt:last-record) "text") (JsonString "Output artifact unavailable.")))))
         ; Only the last 12000 bytes of a long artifact.
         ($long (path:join (nt:required-env "HOME") "long.log"))
         ($_ (fs:write $long (str:concat "HEAD" (str:join "" (map-atom (dt:range 0 1300) $k "abcdefghij")))))
         ($_ (display:tool-finished "x4" "exited" 0 $long))
         ($_ (nt:require artifact-tail-bound
               (case (dt:get (dt:last-record) "text")
                 (((JsonString $tail) (and (== (str:byte-length $tail) 12000) (not (str:starts-with $tail "HEAD"))))
                  ($o False)))))
         ; Control files that cannot be read switch the display off.
         ($_ (dt:control "not json"))
         ($_ (nt:require unreadable-control (nt:equal (display:event "plan" "plan" "x") 0)))
         ($_ (dt:control "{\"enabled\": true}"))
         ($_ (nt:require control-without-epoch (nt:equal (display:event "plan" "plan" "x") 0)))
         ($_ (dt:control "{\"enabled\": 1, \"epoch\": 7}"))
         ($_ (nt:require numeric-epoch
               (and (nt:equal (display:event "plan" "plan" "x") 1) (nt:equal (dt:get (dt:last-record) "epoch") (JsonString "7")))))
         ($_ (nt:require sequences-increase (dt:increasing? (dt:sequences (dt:records)))))
         ; File names: other characters become "_", at most 120, never empty.
         ($_ (nt:require safe-names
               (nt:equal (map-atom ("coding a/b" "x.y-z_1" "") $name (display:safe $name))
                         ("coding_a_b" "x.y-z_1" "agent"))))
         ($_ (nt:require safe-length (== (str:char-length (display:safe (str:join "" (map-atom (dt:range 0 30) $k "abcde")))) 120)))
         ; A display that cannot be written drops the record.
         ($_ (nt:require unwritable-root
               (case (catch (display:write (path:join $artifact "below") ((JsonPair "kind" (JsonString "plan")))))
                 ((1 False) ($other True))))))
    (println! UNIT_DISPLAY_OK)))
