; The command responder's answers, one command at a time, with no channel
; service and no model: what an operator's command does to the agent's
; settings, what it wakes, and the form of its reply (text, or a menu with
; its buttons). The end-to-end test holds the same answers through the real
; service; /start, which also starts the agent's own service unit, is held
; there and by the service's own suite.
!(import! &self (library lib_import))
!(import! &self ./channels/telegram_control.metta)

(= (assertEqual $A $B) (if (== $A $B) True (let $_ (println! (MISMATCH $A $B)) (assert False))))
(= (contains? $text $needle) (case (str:find $text $needle) (((TextNotFound $n) False) ($at True))))
(= (wake-file) (system:getenv-or-default "METTACLAW_WAKE_REQUEST_PATH" ""))
(= (woken) (if (fs:exists (wake-file)) (let $why (fs:read-text (wake-file)) (let $_ (fs:remove (wake-file)) $why)) ""))
(= (text? $reply) (== (get-type $reply) String))
; A menu's button data, row by row.
(= (menu-data $reply)
   (case $reply
     (((tc:menu $toast $text (JsonArray $rows))
       (collapse (let (JsonArray $row) (tc:items $rows) (let (JsonArray ($label (JsonString $data))) (tc:items $row) $data))))
      ($other not-a-menu))))
(= (all-prefixed? $items $prefix)
   (if (== $items ()) True
     (and (str:starts-with (car-atom $items) $prefix) (all-prefixed? (cdr-atom $items) $prefix))))

(= (responder:settings)
   (let* (($_ (woken))
          ; /mode shows the mode and how to switch; a switch persists and wakes her.
          ($view (tc:answer "/mode" ""))
          ($_ (assertEqual (contains? $view " — /mode <name> to switch, /modes to list") True))
          ($_ (assertEqual (woken) ""))
          ($set (tc:answer "/mode" "iter"))
          ($_ (assertEqual (text? $set) True))
          ($_ (assertEqual (woken) "mode switch"))
          ($_ (assertEqual (contains? (tc:answer "/mode" "") "iter") True))
          ($refused (tc:guarded (catch (tc:answer "/mode" "no-such-mode")) "/mode"))
          ($_ (assertEqual (text? $refused) True))
          ($_ (assertEqual (contains? (tc:answer "/mode" "") "iter") True))
          ($_ (woken))
          ; /fuel and /model show; neither wakes her.
          ($_ (assertEqual (contains? (tc:answer "/fuel" "") " — /fuel <name> to switch, /fuels to list") True))
          ($model (tc:answer "/model" ""))
          ($_ (assertEqual (str:starts-with $model "selected model: ") True))
          ($_ (assertEqual (contains? $model "/model <name> to switch, /models to list, /quota for budget") True))
          ($_ (assertEqual (woken) ""))
          ($_ (assertEqual (tc:answer "/nope" "") "unknown command /nope")))
     (println! RESPONDER_SETTINGS_OK)))

; The menus answer with buttons whose data names the setting they change.
(= (responder:menus)
   (let* (($modes (menu-data (tc:answer "/modes" "")))
          ($_ (assertEqual (> (size-atom $modes) 1) True))
          ($_ (assertEqual (all-prefixed? $modes "mode:") True))
          ($fuels (menu-data (tc:answer "/fuels" "")))
          ($_ (assertEqual (> (size-atom $fuels) 1) True))
          ($_ (assertEqual (all-prefixed? $fuels "fuel:") True))
          ($energy (menu-data (tc:answer "/energy" "")))
          ($_ (assertEqual (all-prefixed? $energy "energy:") True))
          ($_ (assertEqual (tc:member? "energy:default:full" $energy) True))
          ; As the service reads them: ["menu", toast, text, rows], or ["answer", text].
          ($body (json-parse (tc:body (tc:answer "/modes" ""))))
          ($_ (assertEqual (case $body (((JsonArray ((JsonString "menu") (JsonString $toast) (JsonString $text) (JsonArray $rows))) (> (size-atom $rows) 0)) ($other False))) True))
          ($_ (assertEqual (tc:body "plain") "[\"answer\",\"plain\"]"))
          ($_ (assertEqual (woken) "")))
     (println! RESPONDER_MENUS_OK)))

; /wake and /stop wake her loop, each with its reason; /health and /activity
; report without waking anything.
(= (responder:lifecycle)
   (let* (($_ (woken))
          ($_ (assertEqual (tc:answer "/wake" "") "Waking."))
          ($_ (assertEqual (woken) "/wake"))
          ($_ (assertEqual (text? (tc:answer "/health" "")) True))
          ($_ (assertEqual (text? (tc:answer "/activity" "")) True))
          ($_ (assertEqual (woken) ""))
          ($_ (assertEqual (lifecycle:enabled) 1))
          ($_ (assertEqual (tc:answer "/stop" "") "Stopped. Cognition is paused and nothing is sent until /start."))
          ($_ (assertEqual (woken) "operator stop"))
          ($_ (assertEqual (lifecycle:enabled) 0)))
     (println! RESPONDER_LIFECYCLE_OK)))

!(responder:settings)
!(responder:menus)
; The lifecycle latch is written only into a state file the runner names.
!(if (or (== (wake-file) "") (== (system:getenv-or-default "METTACLAW_LIFECYCLE_PATH" "") ""))
   (println! RESPONDER_LIFECYCLE_NOT_RUN)
   (responder:lifecycle))
!(println! TELEGRAM_RESPONDER_OK)
