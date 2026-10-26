; Native effect receipts. Explicit host boundaries: strategy bookkeeping,
; stream receipt observation, and configuration-selected worklog plugins ONLY.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library list))
!(import! &self ./src/effect_receipt_values_cetta.metta)
!(import! &self ./src/effect_receipt_io_cetta.metta)
!(import! &self ./src/public_worklog_cetta.metta)
!(import! &self ./src/strategy_host.py)
!(import! &self ./src/stream_sink.py)
!(import! &self ./src/effect_receipts_plugin.py)

; Reviewer mail2b51e0f80b544595b546a6effb97187b: one serial evaluator per
; runtime process reaches these hooks. This is evaluator-local reentry state,
; NOT an implementation of arbitrary multithreaded Python threading.local.
; Import-once owns one history; no lock is held across arbitrary sink code.
!(bind! &er-reporting (new-state False))
!(bind! &er-published (new-state ()))
!(bind! &er-in-flight (new-state ()))
!(bind! &er-captured (new-state ()))

(= (er:env $name) (erv:strip (system:getenv-or-default $name "")))
(= (er:enabled?) (== (system:getenv-or-default "METTACLAW_PUBLIC_TOOL_RECEIPTS" "0") "1"))
(= (er:failed? $value)
   (case $value (((ErFailure $_ $_ $_) True) ((Error $_ $_) True) ($other False))))
(= (er:answer $answers)
   (case $answers ((($value) $value)
     ($other (ErFailure NativeEvaluationError "native call did not return one outcome" $answers)))))
(= (er:best $answers)
   (case $answers (((1) 1) ($other 0))))
(= (er:tools)
   ("shell" "query" "read-file" "read-lines" "write-file" "edit-file" "grep-files"
    "ls-tree" "background" "jobs" "job-output" "search" "metta" "rest" "pin"
    "remember" "send" "send-file" "help" "tmux-peek"))
(= (er:dispositions) ("returned" "failed" "no_result" "exception" "uncertain" "withheld" "deferred"))
(= (er:tool-value $value)
   (case $value
     (((JsonArray $items) (if (== $items ()) JsonNull
       (let $head (car-atom $items) (if (== (get-type $head) String) $head JsonNull))))
      ($other JsonNull))))
(= (er:tool $command)
   (if (== (get-type $command) String)
     (let $parsed (pw:parsed (json:parse-python $command))
       (case $parsed
         (((ErFailure JSONDecodeError $_ $_) JsonNull)
          ((ErFailure ValueError $_ $_) JsonNull)
          ((ErFailure $_ $_ $_) $parsed)
          ($other (er:tool-value $other)))))
     (er:tool-value $command)))
(= (er:hash $text)
   (let $encoded (erv:encode $text)
     (if (erv:failed? $encoded) $encoded (str:concat "rcpt:" (str:byte-slice (str:sha256 $encoded) 0 16)))))
(= (er:receipt-id $turn $disposition $command $result)
   (let* (($number (erv:int $turn)) ($d (erv:str $disposition)) ($r (erv:str $result))
          ($failure (erv:first-failure ($number $d $r))))
     (if (not (== $failure ErNoFailure)) $failure
       (let $wire (pw:json-result (json:stringify-python-with
         (JsonArray ($number $d $command $r)) ((ensure-ascii False) (sort-keys True))))
         (if (erv:failed? $wire) $wire (er:hash $wire))))))
(= (er:event $turn $disposition $command $result $at $occurrence)
   (let* (($tool (er:tool $command)) ($d (erv:str $disposition)))
     (if (erv:failed? $tool) $tool
      (if (erv:failed? $d) $d
       (if (or (not (erv:contains (er:tools) $tool)) (not (erv:contains (er:dispositions) $d))) JsonNull
       (let* (($id (if (== $occurrence JsonNull) (er:receipt-id $turn $d $command $result)
                    (let $text (erv:str $occurrence) (if (erv:failed? $text) $text (er:hash $text)))))
              ($stamp (erio:iso $at "utc")) ($failure (erv:first-failure ($id $stamp))))
         (if (not (== $failure ErNoFailure)) $failure
           (JsonObject ((JsonPair "tool" $tool) (JsonPair "disposition" $d)
             (JsonPair "receipt_id" $id) (JsonPair "at" $stamp))))))))))
(= (er:required $event $key)
   (case $event
     (((JsonObject $_) (if (erv:has $event $key) (erv:get $event $key JsonNull)
                         (erv:fail KeyError (erv:quote $key))))
      ((JsonArray $_) (erv:fail TypeError "list indices must be integers or slices, not str"))
      ($other (if (== (get-type $other) String)
        (erv:fail TypeError "string indices must be integers, not 'str'")
        (erv:fail TypeError (str:join "" ("'" (erv:typename $other) "' object is not subscriptable"))))))))
(= (er:public-text $event)
   (let $tool (er:required $event "tool")
     (if (erv:failed? $tool) $tool (er:public-text-tool $event (erv:str $tool)))))
(= (er:public-text-tool $event $tool)
   (if (erv:failed? $tool) $tool
     (let $d (er:required $event "disposition")
       (if (erv:failed? $d) $d
         (er:public-text-fields $tool $d (er:required $event "receipt_id"))))))
(= (er:public-text-fields $tool $d $id)
   (if (erv:failed? $id) $id
     (let* (($dtext (erv:str $d)) ($idtext (erv:str $id)))
       (erv:join "" ("Activity · " $tool "\n- Receipt: " $dtext " · " $idtext
         (if (== $d "returned") "\n- Dispatcher return only; not a success verdict."
           (if (erv:contains ("withheld" "deferred") $d) "\n- The command did not run." "")))))))
(= (er:without-turn $turn $items)
   (filter-atom $items $item (let ($key $_) $item (not (== $key $turn)))))
(= (er:epoch $turn $items)
   (if (== $items ()) JsonNull
     (let ($key $epoch) (car-atom $items)
       (if (== $key $turn) $epoch (er:epoch $turn (cdr-atom $items))))))
(= (er:bounded $items $limit)
   (if (> (size-atom $items) $limit) (erv:drop $items (- (size-atom $items) $limit)) $items))
(= (er:sink-status)
   (let* (($root (er:env "METTACLAW_PUBLIC_WORK_DIR")) ($library (er:env "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") (ErSinkStatus False JsonNull)
       (if (not (== $library ""))
         (let $value (py-call (effect_receipts_plugin.status $root $library))
           (case $value ((($enabled $epoch) (ErSinkStatus (== $enabled 1) $epoch)) ($other $other))))
         (let $state (pw:transact $root "status" "" JsonNull)
           (if (erv:failed? $state) $state
             (let $epoch (erv:int (erv:get $state "epoch" 0))
               (if (erv:failed? $epoch) $epoch
                 (ErSinkStatus (erv:truth (erv:get $state "enabled" False)) $epoch)))))))))
(= (er:sink-add $text $epoch)
   (let* (($root (er:env "METTACLAW_PUBLIC_WORK_DIR")) ($library (er:env "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") 0
       (if (not (== $library "")) (py-call (effect_receipts_plugin.add $root $library $text $epoch))
         (let $state (pw:transact $root "add" $text $epoch)
           (if (erv:failed? $state) $state
             (if (erv:truth (erv:get $state "accepted" True)) 1 0)))))))
(= (er:capture-body $turn)
   (let* (($status (er:answer (collapse (catch (er:sink-status)))))
          ($number (erv:int $turn)))
     (if (erv:failed? $number) $number
       (let* (($rest (er:without-turn $number (get-state &er-captured)))
              ($enabled (case $status (((ErSinkStatus True $_) True) ($other False))))
              ($next (if $enabled (let (ErSinkStatus $_ $epoch) $status
                       (er:bounded (list:append $rest (($number $epoch))) 64)) $rest))
              ($_ (change-state! &er-captured $next)))
         (if $enabled 1 0)))))
(= (er:capture $turn)
   ; Python catches sink failures, not the subsequent int(turn) conversion.
   (if (er:enabled?) (er:answer (collapse (catch (er:capture-body (erv:cross $turn))))) 0))
(= (er:publish-admitted $event $epoch)
   (let* (($id (erv:get $event "receipt_id" ""))
          ($_ (change-state! &er-in-flight (cons-atom $id (get-state &er-in-flight))))
          ($outcomes (collapse (catch (er:sink-add (er:public-text $event) $epoch))))
          ($accepted (er:best $outcomes))
          ($_ (change-state! &er-in-flight (list:remove-all $id (get-state &er-in-flight))))
          ($_ (if (== $accepted 1)
                (change-state! &er-published (er:bounded (list:append (get-state &er-published) ($id)) 1024)) ())))
     $accepted))
(= (er:publish-body $turn $disposition $command $result $at $occurrence)
   (let $event (er:event $turn $disposition $command $result $at $occurrence)
     (if (or (== $event JsonNull) (erv:failed? $event)) 0
       (let* (($number (erv:int $turn)) ($id (erv:get $event "receipt_id" ""))
              ($epoch (er:epoch $number (get-state &er-captured))))
         (if (or (== $epoch JsonNull) (or (erv:contains (get-state &er-published) $id)
               (erv:contains (get-state &er-in-flight) $id))) 0
           (er:publish-admitted $event $epoch))))))
(= (er:publish $turn $disposition $command $result $at $occurrence)
   (if (or (not (er:enabled?)) (get-state &er-reporting)) 0
     (let* (($_ (change-state! &er-reporting True))
            ($outcomes (collapse (catch (er:publish-body $turn $disposition $command $result $at $occurrence))))
            ($_ (change-state! &er-reporting False)))
       (er:best $outcomes))))
(= (er:path) (er:env "METTACLAW_EFFECT_RECEIPT_PATH"))
(= (er:append $entry)
   (let $raw (er:path)
     (if (== $raw "") 0
       (let* (($path (erio:path $raw)) ($made (erio:mkdir (erio:parent $path) 511)))
         (if (erv:failed? $made) $made
           (let $wire (pw:json-result (json:stringify-python-with $entry ((ensure-ascii False) (sort-keys True))))
             (if (erv:failed? $wire) $wire
               (let $payload (erv:encode (str:concat $wire "\n"))
                 (if (erv:failed? $payload) $payload (erio:append $path $payload True))))))))))
(= (er:commit $turn $disposition $command $result $occurrence $now)
   (let* (($number (erv:int $turn)) ($d (erv:text $disposition 80))
          ($c (erv:text $command 6000)) ($r (erv:text $result 6000))
          ($failure (erv:first-failure ($number $d $c $r))))
     (if (not (== $failure ErNoFailure)) $failure
       (let* (($entry (JsonObject ((JsonPair "schema" 1) (JsonPair "recorded_at" $now)
                (JsonPair "turn" $number) (JsonPair "occurrence" $occurrence)
                (JsonPair "disposition" $d) (JsonPair "command" $c) (JsonPair "result" $r))))
              ($committed (er:append $entry)))
         (if (== $committed 1)
           (let $_ (er:publish $turn $disposition $command $result $now $occurrence) 1) $committed)))))
(= (er:strategy $turn $disposition $command $result $occurrence)
   (if (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1")
     (let $outcome (er:answer (collapse (catch (py-call (strategy_host.command_result $turn $disposition $command $result)))))
       (if (er:failed? $outcome) $outcome
         (if (erv:truth $occurrence) $occurrence
           (erv:cross (py-call (strategy_host.command_identity $turn)))))) $occurrence))
(= (er:record $turn $disposition $command $result $occurrence)
   (er:record-occurrence $turn $disposition $command $result (erv:cross $occurrence)))
; Only the omitted-argument facade supplies Python None. An explicitly supplied
; native JsonNull atom crosses to the string "JsonNull", like the old py-call.
(= (er:record-occurrence $turn $disposition $command $result $occurrence)
   (let $chosen (er:strategy $turn $disposition $command $result $occurrence)
     (if (er:failed? $chosen) $chosen
       (let* (($identity (if (erv:truth $chosen) $chosen (system:uuid4-hex)))
              ($now (if (er:failed? $identity) $identity (system:wall-time))))
         (if (er:failed? $now) $now
           (let $committed (er:commit (erv:cross $turn) (erv:cross $disposition) (erv:cross $command)
                             (erv:cross $result) $identity $now)
             (if (== $committed 1)
               (let $_ (collapse (catch (py-call (stream_sink.receipt (erv:host-array $identity) $command $result $disposition)))) 1)
               $committed)))))))
(= (er:suffix $turn $disposition $commands $reason)
   (let* (($now (system:wall-time)) ($number (erv:int (erv:cross $turn)))
          ($d (erv:text (erv:cross $disposition) 80)) ($c (erv:text (erv:cross $commands) 6000))
          ($r (erv:text (erv:cross $reason) 240)) ($failure (erv:first-failure ($number $d $c $r))))
     (if (not (== $failure ErNoFailure)) $failure
       (er:append (JsonObject ((JsonPair "schema" 1) (JsonPair "recorded_at" $now)
         (JsonPair "turn" $number) (JsonPair "disposition" $d) (JsonPair "commands" $c) (JsonPair "reason" $r)))))))
(= (er:entries $lines)
   (if (== $lines ()) ()
     (let* (($value (json:parse-python (car-atom $lines))) ($rest (er:entries (cdr-atom $lines))))
       (case $value (((JsonObject $_) (cons-atom $value $rest)) ($other $rest))))))
(= (er:render-entry $entry)
   (let* (($d (erv:str (erv:get $entry "disposition" "unknown")))
          ($head (erv:join "" ("turn=" (erv:str (erv:get $entry "turn" "?")) " disposition=" $d))))
     (if (erv:has $entry "atlas")
       (erv:join "" ("disposition=" $d " server=" (erv:str (erv:get $entry "server" ""))
         " tool=" (erv:str (erv:get $entry "tool" "")) " atlas="
         (pw:json-result (json:stringify-python-with (erv:get $entry "atlas" (JsonObject ()))
           ((ensure-ascii False) (sort-keys True) (separators "," ":"))))))
       (if (erv:has $entry "command")
         (erv:join "" ($head " command=" (erv:str (erv:get $entry "command" "")) " result=" (erv:str (erv:get $entry "result" ""))))
         (erv:join "" ($head " reason=" (erv:str (erv:get $entry "reason" "")) " commands=" (erv:str (erv:get $entry "commands" ""))))))))
(= (er:render $entries $limit)
   (let* (($count (erv:int $limit)))
     (if (erv:failed? $count) $count
       (let $recent (erv:drop $entries (- (size-atom $entries) (if (< $count 1) 1 $count)))
         (if (== $recent ()) ""
           (erv:join "\n" (cons-atom
             "AUTHORITATIVE BROKER RECEIPTS: proposals are not actions. returned means only that the dispatcher returned; inspect result before claiming success. withheld/deferred commands did not run. A proposal without a matching receipt is not witnessed completion."
             (map-atom $recent $entry (er:render-entry $entry)))))))))
(= (er:view $limit)
   (let $path (er:path)
     (if (== $path "") "" (er:view-path (erio:path $path) $limit))))
(= (er:view-path $path $limit)
   (case (erio:metadata $path)
     (((fs:info regular $_ $_)
       (let $tail (erio:single (collapse (catch (fs:tail-text $path 180000))))
         (case $tail
           (((fs:tail $size $truncated $text)
             (let $lines (erio:lines $text)
               (er:render (er:entries (if (and (> $size 180000) (not (== $lines ()))) (cdr-atom $lines) $lines)) $limit)))
            ((ErFailure $_ $_ $_) $tail)
            ($other (ErFailure NativeCapabilityError "missing receipt tail outcome" $other))))))
      ((ErFailure $class $message $detail) (ErFailure $class $message $detail))
      ($other ""))))

; context_source_loaders.load uses UTF-8 errors=replace at its transport
; boundary. Preserve that transformation here, not in the lossless view API.
(= (er:context-text $text)
   (erv:from-points (map-atom (erv:points $text) $cp (if (erv:surrogate? $cp) 63 $cp))))
(= (er:context-load)
   (let $answers (collapse (catch
       (if (== (py-call (strategy_host.enabled_flag)) 1) "Task receipts are in TASK_CONTEXT." (er:view 18))))
     (case $answers
       ((($text) (if (== (get-type $text) String) ("value" (er:context-text $text)) ("unavailable")))
        ($other ("unavailable"))))))
