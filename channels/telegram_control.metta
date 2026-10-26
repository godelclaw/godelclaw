; Lila's operator-command responder, in MeTTa.
;
; The CeTTa Telegram channel service asks about the operator commands it does
; not answer itself, and about taps on the buttons of their menus. /engine,
; /mode, /fuel, /model, /quota, /health, /activity, /wake, /start, /stop,
; /claude_code_authorization and the menus /engines, /modes, /fuels, /models
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
!(import! &self ./src/helper.py)
!(import! &self ./src/mode_state.metta)
!(import! &self ./src/fuel_io)
!(import! &self ./src/mode_channel.py)
!(import! &self ./src/input_ack.py)
!(import! &self ./channels/energy.metta)
!(import! &self ./channels/telegram_control_input.metta)
!(import! &self ./channels/telegram_notice_cleanup.metta)

(= (tc:env $name $default) (system:getenv-or-default $name $default))
(= (tc:socket) (tc:env "METTACLAW_TELEGRAM_SERVICE_SOCKET" ""))
(= (tc:call $code $name $body)
   (let $path (tc:socket) (cwp:call $path $code $name $body 5000)))

(= (tc:tasks-after $after $limit)
   (if (== $limit 0) (("runtime-scan-unavailable" "[]"))
     (case (tc:call 1 $after "")
       (((cwp:reply 65 $task $body)
         (let $rest (tc:tasks-after $task (- $limit 1))
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
      ($other $why))))

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
; one (an engine chosen but not yet running) marked apart, and those not
; available said so. Its data is PREFIX:NAME.
(= (tc:settings-menu $kind $prefix $toast)
   (let $state (json-parse (py-call (helper.menu_state $kind)))
     (let $current (tc:string (tc:at $state "current"))
       (let $selected (tc:string (tc:at $state "selected"))
         (let $unavailable (tc:strings (tc:at $state "unavailable"))
           (let $rows (collapse (let $name (tc:items (tc:strings (tc:at $state "names")))
                                  (let $data (format-args "{}:{}" ($prefix $name))
                                    (if (> (str:byte-length $data) 64) (empty)
                                      (let $off (tc:member? $name $unavailable)
                                        (let $mark (if (== $name $current) "● "
                                                     (if (== $name $selected) "◌ "
                                                       (if (and $off (== $kind "models")) "⚠ " "")))
                                          (let $label (format-args "{}{}{}" ($mark $name (if (and $off (== $kind "engines")) " (unavailable)" "")))
                                            (JsonArray ((JsonArray ((JsonString (tc:cut $label 64)) (JsonString $data))))))))))))
             (tc:menu $toast (tc:string (tc:at $state "text")) (JsonArray $rows))))))))

(= (tc:energy-menu $toast)
   (let $view (energy:view)
     (tc:menu $toast (format-args "arming energy — {}" ($view)) (energy:menu))))

; Switch the engine, and restart Lila's loop on it when it is not the one
; running.
(= (tc:switch-engine $name)
   (let $adopted (py-call (helper.adopt_active_engine))
     (let $reply (tc:text (engine:set $name))
       (let $selected (tc:text (engine:selected))
         (let $active (tc:text (engine:active))
           (if (and (not (str:starts-with $reply "engine-set failed:")) (not (== $selected $active)))
             (let $recycle (engine:recycle)
               (let $woken (tc:wake "engine switch") $reply))
             $reply))))))

; A tap on a menu's button: PREFIX:ARGUMENT. The answer is the menu again,
; redrawn, with the outcome as the tap's toast.
(= (tc:tap $data)
   (case (str:find $data ":")
     (((TextNotFound $needle) (format-args "unknown button {}" ($data)))
      ($at (let $kind (str:byte-slice $data 0 $at)
             (let $arg (str:byte-slice $data (+ $at 1) (str:byte-length $data))
               (case $kind
                 (("mode" (let $reply (tc:text (mode:set $arg))
                            (let $woken (tc:wake "mode switch") (tc:settings-menu "modes" "mode" $reply))))
                  ("fuel" (let $reply (tc:text (fuel:set $arg))
                            (tc:settings-menu "fuels" "fuel" $reply)))
                  ("engine" (let $reply (tc:switch-engine $arg) (tc:settings-menu "engines" "engine" $reply)))
                  ("model" (let $reply (tc:text (py-call (synthetic_llm.set_model $arg)))
                             (tc:settings-menu "models" "model" $reply)))
                  ("energy" (case (str:find $arg ":")
                              (((TextNotFound $n) (format-args "unknown button {}" ($data)))
                               ($cut (let $reply (energy:set (str:byte-slice $arg 0 $cut) (str:byte-slice $arg (+ $cut 1) (str:byte-length $arg)))
                                       (tc:energy-menu $reply))))))
                  ($other (format-args "unknown button {}" ($data)))))))))))

(= (tc:answer $name $args)
   (case $name
     (("/start"
       (let $reply (lifecycle:start)
         (let $resumed (py-call (sibling.resume_self))
           (if (== (lifecycle:enabled) 1)
             (let $woken (tc:wake "operator start")
               (let $note (tc:text $resumed) (format-args "Started. Cognition is enabled; {}." ($note))))
             (let $view (tc:text (lifecycle:view))
               (format-args "Start recorded, but cognition is still disabled: {}" ($view)))))))
      ("/stop"
       (let $reply (lifecycle:stop)
         (let $woken (tc:wake "operator stop")
           (if (== (lifecycle:enabled) 0)
             "Stopped. Cognition is paused and nothing is sent until /start."
             (let $view (tc:text (lifecycle:view))
               (format-args "Stop recorded, but cognition still reads enabled: {}" ($view)))))))
      ("/wake" (let $woken (tc:wake "/wake") "Waking."))
      ("/claude_code_authorization" (tc:text (py-call (helper.claude_code_toggle))))
      ("/health" (tc:text (py-call (runtime_health.report))))
      ("/activity" (tc:text (py-call (runtime_health.activity_report))))
      ("/engine"
       (if (== $args "")
         (let $adopted (py-call (helper.adopt_active_engine))
           (tc:with-hint (tc:text (engine:view)) "/engine <name> to switch, /engines to list"))
         (tc:switch-engine $args)))
      ("/engines" (tc:settings-menu "engines" "engine" ""))
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
         (tc:with-hint (tc:text (mode:view)) "/mode <name> to switch, /modes to list")
         (let $reply (tc:text (mode:set $args))
           (let $woken (tc:wake "mode switch") $reply))))
      ("/quota" (tc:text (py-call (synthetic_llm.quota))))
      ("/model"
       (if (== $args "")
         (let $model (tc:text (py-call (synthetic_llm.current_model)))
           (format-args "selected model: {} — applied at the next provider request; /model <name> to switch, /models to list, /quota for budget" ($model)))
         (tc:text (py-call (synthetic_llm.set_model $args)))))
      ($other (format-args "unknown command {}" ($name))))))

; Answer every command task but /delete, and every tap, once each.
(= (tc:parse $task $body)
   (case (collapse (match &self (tc:cached $task $body $value) $value))
     ((($value) $value)
      (() (let $value (json-parse $body)
            (let $_ (remove-atom &self (tc:cached $task $old $prior))
              (let $_ (add-atom &self (tc:cached $task $body $value)) $value)))))))

(= (tc:prune $tasks)
   (if (== $tasks ()) 0
     (let $task (car-atom $tasks)
       (let $_ (if (== (py-call (input_ack.task_active $task)) 1) 0
                 (remove-atom &self (tc:cached $task $body $value)))
         (tc:prune (cdr-atom $tasks))))))

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

; A delayed receipt notice leaves ordinary input pending for cognition.
; Submission and delivery have independent durable service identities.
(= (tc:input-notice $task $lane $kind $role $update)
   (if (ti:operator-control? $kind $role $update) (nc:withdraw $task)
    (let $key (tc:text (py-call (input_ack.key $task $lane $kind $role (json-stringify $update))))
     (if (== $key "") 0
       (let $text (tc:text (py-call (input_ack.text)))
         (let $body (json-stringify (JsonArray ((JsonArray ((JsonString "send") (JsonString $text) (JsonString "plain"))))))
           (case (tc:call 4 $key $body)
             (((cwp:reply 66 $name $answer) (py-call (input_ack.submitted $task)))
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
   (let $tasks (tc:tasks-after "" 256)
     (let $ordinary (tc:notice-tasks $tasks)
      (let $retained (py-call (input_ack.begin_scan (quote $ordinary)))
       (let $_ (tc:prune (collapse (match &self (tc:cached $task $body $value) $task)))
       (let $handled (tc:handle $tasks)
         (let $cleaned (tc:guarded (catch (nc:pump)) "receipt cleanup") $handled)))))))

; One effectful cycle has one committed outcome. Alternatives inside a scan
; must not survive into a later cycle and replay already submitted effects.
; Arm before scanning, so an arrival during the scan remains a wake-up.
(= (tc:cycle)
   (let $_ (py-call (input_ack.arm_changes))
   (let $answered (catch (tc:serve-once))
     (let $pumped (catch (py-call (mode_channel.pump)))
       (let $health (catch (py-call (mode_channel.heartbeat)))
         (let $slept (py-call (input_ack.wait_changes))
           $answered))))))

(= (tc:serve)
   (let $_ (once (tc:cycle)) (tc:serve)))
