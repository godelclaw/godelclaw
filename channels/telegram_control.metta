; Lila's operator-command responder, in MeTTa.
;
; The CeTTa Telegram channel service asks about the operator commands it does
; not answer itself, and about taps on the buttons of their menus. /mode,
; /fuel, /model, /quota, /health, /activity, /wake, /start, /stop,
; /claude_code_authorization and the menus /modes, /fuels, /models
; and /energy read or change Lila's settings, never her thinking, so they are
; answered here, in a process of its own beside her loop that makes no model
; call: an answer never waits for her turn. /delete stays with her loop's
; Telegram client, which owns her sends and their receipts.
;
; Run by `run.sh telegram-control`. The service answers the operator itself
; if this process is not there to answer in time.

!(import! &self str)
!(import! &self json)
!(import! &self fs)
!(import! &self cwp)
!(import! &self system)
!(import! &self (library path))
!(import! &self ./src/helper.py)
!(import! &self ./src/mode_state.metta)
!(import! &self ./src/fuel_io)
!(import! &self ./src/input_notice_cetta.metta)
!(import! &self ./src/runtime_health_cetta.metta)
!(import! &self ./channels/telegram_failure_notice.metta)
!(import! &self ./channels/energy.metta)
!(import! &self ./channels/telegram_control_input.metta)
!(import! &self ./channels/telegram_notice_cleanup.metta)
!(import! &self ./channels/telegram_control_io.metta)
!(import! &self ./src/sibling_cetta.metta)
!(import! &self ./src/model_selection_cetta.metta)

(= (tc:env $name $default) (system:getenv-or-default $name $default))
(= (tc:socket) (tc:env "METTACLAW_TELEGRAM_SERVICE_SOCKET" ""))
(= (tc:call $code $name $body)
   (let $path (tc:socket) (cwp:call $path $code $name $body 5000)))

(= (tc:tasks-after $after $limit) (tc:tasks-after-code 1 $after $limit))

; Fetch controls independently of conversational input. Limits bound one scan,
; never admission or durable retention. Each cycle starts with controls.
(= (tc:tasks-after-code $code $after $limit)
   (if (== $limit 0) (("runtime-scan-unavailable" "[]"))
     (case (tc:call $code $after "")
       (((cwp:reply 65 $task $body)
         (let $rest (tc:tasks-after-code $code $task (- $limit 1))
           (cons-atom ($task $body) $rest)))
        ((cwp:reply 64 $name $body) ())
        ($other (("runtime-scan-unavailable" "[]")))))))

(= (tc:cut $text $n) (if (> (str:char-length $text) $n) (str:char-slice $text 0 $n) $text))

; A reply is text, or a menu: (tc:menu TOAST TEXT ROWS), ROWS a JsonArray of
; rows of [label, data] buttons. The toast answers a tap on a button.
(= (tc:body $reply)
   (case $reply
     (((tc:menu $toast $text $rows)
       (json-stringify (JsonArray ((JsonString "menu") (JsonString (tc:cut $toast 190))
                                   (JsonString (tc:cut $text 3800)) $rows))))
      ($text (json-stringify (JsonArray ((JsonString "answer") (JsonString (tc:cut $text 3800)))))))))

(= (tc:answer-task $task $reply)
   (let $body (tc:body $reply)
     (case (tc:call 2 $task $body)
       (((cwp:reply 66 $n $b) True)
        ((cwp:reply 68 $n $b) True)
        ((cwp:reply 69 $n $b) True)
        ($other False)))))

(= (tc:text $value) (format-args "{}" ($value)))

; Wakes Lila from a rest: her loop's client reads and removes this file.
(= (tc:wake $reason)
   (let $file (tc:env "METTACLAW_WAKE_REQUEST_PATH" "")
     (if (== $file "") () (fs:write $file $reason))))

(= (tc:with-hint $view $hint) (format-args "{} — {}" ($view $hint)))

; A command or tap whose code raises is answered with what failed. A raise
; must never end this process: the service would only ask the same task
; again, and every command after it would wait for the notice.
(= (tc:guarded $reply $what)
   (case $reply
     (((Error $call $why)
       (tc:cut (format-args "{} failed: {}" ($what (tc:reason $call $why))) 300))
      ($ok $ok))))

;; A raised Python error names its exception, which reads as its type and
;; message; any other error reads as its reason.
(= (tc:reason $call $why)
   (case $call
     (((python_error $type $exception)
       (py-call (helper.exception_text $exception)))
      (mode-state
       (case $why
         (((PythonException $class $message $_)
           (py-call (helper.exception_text
             (str:join "" ((if (== (get-type $class) String) $class (str:symbol->string $class))
                           ": " $message)))))
          ($other $why))))
      ($other $why))))

; Native structured errors are values, unlike a raised Python bridge error.
; Propagate them before formatting or performing a later wake/resume effect.
(= (tc:mode-view $value $hint)
   (case $value
     (((Error $call $why) (Error $call $why))
      ($view (tc:with-hint (tc:text $view) $hint)))))
(= (tc:mode-set $name)
   (case (mode:set $name)
     (((Error $call $why) (Error $call $why))
      ($value (let $reply (tc:text $value) (let $woken (tc:wake "mode switch") $reply))))))
(= (tc:mode-tap $name)
   (case (mode:set $name)
     (((Error $call $why) (Error $call $why))
      ($value (let $reply (tc:text $value)
        (let $woken (tc:wake "mode switch") (tc:settings-menu "modes" "mode" $reply)))))))
(= (tc:lifecycle-start $value)
   (case $value
     (((Error $call $why) (Error $call $why))
      ($reply
       (let $resumed (sibling:resume-self)
         (case (lifecycle:enabled)
           (((Error $call $why) (Error $call $why))
            (1 (let $woken (tc:wake "operator start")
                 (let $note (tc:text $resumed) (format-args "Started. Cognition is enabled; {}." ($note)))))
            ($disabled
             (case (lifecycle:view)
               (((Error $call $why) (Error $call $why))
                ($view (format-args "Start recorded, but cognition is still disabled: {}" ((tc:text $view))))))))))))))
(= (tc:lifecycle-stop $value)
   (case $value
     (((Error $call $why) (Error $call $why))
      ($reply
       (let $woken (tc:wake "operator stop")
         (case (lifecycle:enabled)
           (((Error $call $why) (Error $call $why))
            (0 "Stopped. Cognition is paused and nothing is sent until /start.")
            ($enabled
             (case (lifecycle:view)
               (((Error $call $why) (Error $call $why))
                ($view (format-args "Stop recorded, but cognition still reads enabled: {}" ((tc:text $view))))))))))))))

;; ------------------------------------------------------------- menus

(= (tc:items $items)
   (if (== $items ()) (empty)
     (superpose ((car-atom $items) (tc:items (cdr-atom $items))))))
(= (tc:at $object $key)
   (case $object
     (((JsonObject $pairs) (tc:pair $pairs $key))
      ($other JsonNull))))
(= (tc:pair $pairs $key)
   (if (== $pairs ()) JsonNull
     (let $head (car-atom $pairs)
       (case $head
         (((JsonPair $key $value) $value)
          ($other (tc:pair (cdr-atom $pairs) $key)))))))
(= (tc:string $value) (case $value (((JsonString $s) $s) ($other ""))))
(= (tc:strings $value)
   (case $value
     (((JsonArray $items) (collapse (let $item (tc:items $items) (tc:string $item))))
      ($other ()))))
(= (tc:member? $x $items) (not (== (collapse (let $i (tc:items $items) (if (== $i $x) True (empty)))) ())))

; A settings menu: one button a choice, the current one marked, the selected
; one (chosen but not yet running) marked apart, and those not available
; said so. Its data is PREFIX:NAME.
(= (tc:settings-page $state $toast)
   (tc:menu $toast (tc:string (tc:at $state "text")) (tc:at $state "rows")))
(= (tc:settings-menu $kind $prefix $toast)
   (tc:settings-page (json-parse (tc:text (py-call (helper.menu_page $kind $prefix 0)))) $toast))

(= (tc:energy-menu $toast)
   (let $view (energy:view)
     (tc:menu $toast (format-args "arming energy — {}" ($view)) (energy:menu))))

; A tap on a menu's button: PREFIX:ARGUMENT. The answer is the menu again,
; redrawn, with the outcome as the tap's toast.
(= (tc:tap $data)
   (case (str:find $data ":")
     (((TextNotFound $needle) (format-args "unknown button {}" ($data)))
      ($at (let $kind (str:byte-slice $data 0 $at)
             (let $arg (str:byte-slice $data (+ $at 1) (str:byte-length $data))
               (case $kind
                 (("menu" (tc:settings-page (json-parse (tc:text (py-call (helper.menu_page_button $arg)))) ""))
                  ("choice" (tc:tap (tc:text (py-call (helper.menu_choice_button $arg)))))
                  ("mode" (tc:mode-tap $arg))
                  ("fuel" (let $reply (tc:text (fuel:set $arg))
                            (tc:settings-menu "fuels" "fuel" $reply)))
                  ("model" (let $reply (tc:text (py-call (synthetic_llm.set_model $arg)))
                             (tc:settings-menu "models" "model" $reply)))
                  ("energy" (case (str:find $arg ":")
                              (((TextNotFound $n) (format-args "unknown button {}" ($data)))
                               ($cut (let $reply (energy:set (str:byte-slice $arg 0 $cut) (str:byte-slice $arg (+ $cut 1) (str:byte-length $arg)))
                                       (tc:energy-menu $reply))))))
                  ($other (format-args "unknown button {}" ($data)))))))))))

; Claude Code's authorization is the flag file the bridge (src/claude_bridge.py)
; reads before every headless turn; the command turns it over.
(= (tc:claude-code-flag) (system:expanduser "~/.config/pettaclaw/claude_code_authorized"))
(= (tc:claude-code-authorized? $flag)
   (case (fs:read-text-result $flag 0 "raw") ((("ok" $text) (== (str:trim $text) "1")) ($unreadable False))))
(= (tc:claude-code-toggle)
   (let* (($flag (tc:claude-code-flag))
          ($on (not (tc:claude-code-authorized? $flag)))
          ($_ (fs:make-directories (path:dirname $flag) 511))
          ($written (fs:write $flag (if $on "1" "0"))))
     (case $written
       (((fs:failed $reason $operation $path $code $progress $message) (Error tc:claude-code-toggle $message))
        ($done (format-args "claude-code authorization: {}" ((if $on "ON" "OFF"))))))))

(= (tc:answer $name $args)
   (case $name
     (("/start" (tc:lifecycle-start (lifecycle:start)))
      ("/stop" (tc:lifecycle-stop (lifecycle:stop)))
      ("/wake" (let $woken (tc:wake "/wake") "Waking."))
      ("/claude_code_authorization" (tc:claude-code-toggle))
      ("/health" (rh:report))
      ("/activity" (rh:activity))
      ("/modes" (tc:settings-menu "modes" "mode" ""))
      ("/fuels" (tc:settings-menu "fuels" "fuel" ""))
      ("/models" (tc:settings-menu "models" "model" ""))
      ("/energy" (tc:energy-menu ""))
      ("/fuel"
       (if (== $args "")
         (tc:with-hint (tc:text (fuel:view)) "/fuel <name> to switch, /fuels to list")
         (tc:text (fuel:set $args))))
      ("/mode"
       (if (== $args "")
         (tc:mode-view (mode:view) "/mode <name> to switch, /modes to list")
         (tc:mode-set $args)))
      ("/quota" (tc:text (py-call (synthetic_llm.quota))))
      ("/model"
       (if (== $args "")
         (let $model (model:current "syn:large:text")
           (format-args "selected model: {} — applied at the next provider request; /model <name> to switch, /models to list, /quota for budget" ($model)))
         (tc:text (py-call (synthetic_llm.set_model $args)))))
      ($other (format-args "unknown command {}" ($name))))))

; A task's body parsed once: the cache keeps each task's body and value
; while the task is ordinary input on the page, and forgets the rest after
; every scan. Its own spaces keep a lookup by task constant-time.
!(bind! &tc-parsed (new-space))
!(bind! &tc-page (new-space))

(= (tc:parse $task $body)
   (case (collapse (match &tc-parsed (tc:cached $task $body $value) $value))
     ((($value) $value)
      (() (let $value (json-parse $body)
            (let $_ (remove-atom &tc-parsed (tc:cached $task $old $prior))
              (let $_ (add-atom &tc-parsed (tc:cached $task $body $value)) $value)))))))

; Keep a cached parse only for a task of $ordinary, the page's ordinary input.
(= (tc:prune $ordinary)
   (let* (($_ (collapse (match &tc-page (tc:on-page $old) (remove-atom &tc-page (tc:on-page $old)))))
          ($_ (tc:mark-page $ordinary)))
     (tc:drop-off-page (collapse (match &tc-parsed (tc:cached $task $body $value) $task)))))
(= (tc:mark-page $tasks)
   (if (== $tasks ()) 0
     (let ($task $body) (car-atom $tasks)
       (let $_ (add-atom &tc-page (tc:on-page $task)) (tc:mark-page (cdr-atom $tasks))))))
(= (tc:drop-off-page $tasks)
   (if (== $tasks ()) 0
     (let $task (car-atom $tasks)
       (let $_ (if (== (collapse (match &tc-page (tc:on-page $task) True)) ())
                 (remove-atom &tc-parsed (tc:cached $task $body $value)) 0)
         (tc:drop-off-page (cdr-atom $tasks))))))

; Answer every command task but /delete, and every tap, once each.
(= (tc:handle $tasks)
   (if (== $tasks ()) 0
     (let $head (car-atom $tasks)
       (let $tail (cdr-atom $tasks)
         (let ($task $body) $head
           (let $done
                (case (tc:parse $task $body)
                  (((JsonArray ((JsonString "command") (JsonString $key) (JsonString $name) (JsonString $args) (JsonString $lane)))
                    (if (== $name "/delete") 0
                      (let $reply (tc:guarded (catch (tc:answer $name $args)) $name)
                        (if (tc:answer-task $task $reply) 1 0))))
                   ((JsonArray ((JsonString "callback") (JsonString $key) (JsonString $data) (JsonString $lane) $message))
                    (let $reply (tc:guarded (catch (tc:tap $data)) $data)
                      (if (tc:answer-task $task $reply) 1 0)))
                   ((JsonArray ((JsonString "input") (JsonString $lane) (JsonString $kind) (JsonString $role) $update))
                    (let $noticed (tc:guarded (catch (tc:input-notice $task $lane $kind $role $update)) "receipt notice") 0))
                   ($other 0)))
             (+ $done (tc:handle $tail))))))))

(= (tc:notices $tasks)
   (if (== $tasks ()) 0
     (let* ((($task $body) (car-atom $tasks))
            ($noticed (case (tc:parse $task $body)
              (((JsonArray ((JsonString "input") (JsonString $lane) (JsonString $kind) (JsonString $role) $update))
                (tc:guarded (catch (tc:input-notice $task $lane $kind $role $update)) "receipt notice"))
               ($other 0)))))
       (tc:notices (cdr-atom $tasks)))))

; A delayed receipt notice leaves ordinary input pending for cognition.
; Submission and delivery have independent durable service identities. A
; repeated key carries the same bytes: the text never changes.
(= (tc:notice-text) "Received. Your message is queued; /activity shows whether I'm waiting, retrying, or resting.")
(= (tc:input-notice $task $lane $kind $role $update)
   (if (ti:operator-control? $kind $role $update) (nc:withdraw $task)
     (case (notice:key $task $lane $kind $role $update)
       (("" 0)
        ((Error $where $why) (Error $where $why))
        ($key
         (let $body (json-stringify (JsonArray ((JsonArray ((JsonString "send") (JsonString (tc:notice-text)) (JsonString "plain"))))))
           (case (tc:call 4 $key $body)
             (((cwp:reply 66 $name $answer) (notice:submitted $task))
              ($other 0)))))))))

; Observe the same ordinary-input set that the cognition client queues.
; Controls may remain in the service while that client is busy; they are
; independently handled controls, not unanswered conversational input.
(= (tc:notice-tasks $tasks)
   (if (== $tasks ()) ()
     (let* ((($task $body) (car-atom $tasks))
            ($tail (tc:notice-tasks (cdr-atom $tasks)))
            ($control (case (tc:parse $task $body)
              (((JsonArray ((JsonString "input") (JsonString $lane) (JsonString $kind) (JsonString $role) $update))
                (ti:operator-control? $kind $role $update)) ($other False)))))
       (if $control $tail (cons-atom ($task $body) $tail)))))

(= (tc:serve-once)
   (let $controls (tc:tasks-after-code 6 "" 256)
    (let $handled (tc:handle $controls)
     (let $tasks (tc:tasks-after (notice:scan-after) 256)
      (let $ordinary (tc:notice-tasks $tasks)
       (let $scanned (notice:begin-scan $ordinary $tasks)
        (let $_ (tc:prune $ordinary)
         (let $failure (tc:guarded (catch (fnote:notify)) "failure notice")
          (let $noticed (tc:notices $tasks)
           (let $cleaned (tc:guarded (catch (nc:pump)) "receipt cleanup") $handled))))))))))

; One effectful cycle has one committed outcome. Alternatives inside a scan
; must not survive into a later cycle and replay already submitted effects.
; Arm before scanning, so an arrival during the scan remains a wake-up.
(= (tc:cycle)
   (let $armed (tio:arm)
   (let $answered (catch (tc:serve-once))
     (let $pumped (catch (tio:pump))
       (let $health (catch (tio:heartbeat (notice:input-view)))
         (let $waited (tio:wait $armed)
           $answered))))))

(= (tc:serve)
   (let $_ (once (tc:cycle)) (tc:serve)))
