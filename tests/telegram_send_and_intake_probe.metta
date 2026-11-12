; The MeTTa Telegram client's intake log and its one-send-per-turn rule, with
; no channel service configured, so a send or delete says Telegram is off.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (last-record)
   (let $lines (str:split "\n" (str:trim (fs:read-text (tg:ledger))))
     (json-parse (index-atom $lines (- (size-atom $lines) 1)))))
(= (suppressed? $answer) (== $answer "duplicate send suppressed"))
!(configure commchannel telegram)

; A channel post is logged with its kind, its chat and the raw update.
(= (intake:channel-post)
   (let* (($update (json-parse "{\"update_id\": 200, \"channel_post\": {\"message_id\": 20, \"chat\": {\"id\": -100, \"type\": \"channel\", \"title\": \"News\"}, \"text\": \"channel text\"}}"))
          (($kind $message) (tg:message-of $update))
          ($_ (assertEqual $kind "channel_post"))
          ($_ (tg:log-update $update $kind $message True True ""))
          ($record (last-record))
          ($_ (assertEqual (json:get $record "kind") (JsonString "channel_post")))
          ($_ (assertEqual (json:get $record "chat_id") (JsonString "-100")))
          ($_ (assertEqual (json:get $record "chat_title") (JsonString "News")))
          ($_ (assertEqual (json:get (json:get (json:get $record "raw") "channel_post") "text") (JsonString "channel text"))))
     (println! INTAKE_LOG_OK)))

; The model sends a message to a chat at most once per turn; a new turn may
; send it again, and the control plane's own sends are not counted. Without a
; service a send or delete fails at once and says why.
(= (send:once-per-turn)
   (let* (($_ (py-call (telegram.begin_effect_turn "1")))
          ($_ (assertEqual (tg:send-effect "same message" "-1") "send failed: the Telegram service is not configured"))
          ($_ (assertEqual (tg:send-effect "same message" "-1") "duplicate send suppressed"))
          ($_ (assertEqual (suppressed? (tg:send-effect "other message" "-1")) False))
          ($_ (py-call (telegram.begin_effect_turn "2")))
          ($_ (assertEqual (suppressed? (tg:send-effect "same message" "-1")) False))
          ($_ (assertEqual (suppressed? (tg:send "same message" "-1")) False))
          ($_ (assertEqual (tg:delete "-1" 5) "delete failed: the Telegram service is not configured")))
     (println! EFFECT_SEND_ONCE_OK)))

; Every section must answer before the witness.
!(let* (($a (intake:channel-post)) ($b (send:once-per-turn)))
   (println! TELEGRAM_SEND_AND_INTAKE_OK))
