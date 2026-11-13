; Her conversation window, as the Telegram client renders it from the ledger:
; causal up to the frontier fixed when this turn's input was prepared, without
; that input, one entry per message (its newest revision before the frontier),
; commands and control replies left out, the route line first, newest last,
; within its budgets. The runner starts this probe with operator 7, primary
; chat 42 and allowed chats 42 and -100.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(configure commchannel telegram)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (contains? $text $needle) (case (str:find $text $needle) (((TextNotFound $n) False) ($at True))))
(= (times $text $needle) (- (size-atom (str:split $needle $text)) 1))
(= (window) (tg:conversation-window 48))

; One ledger record, as the client logs it; $update is its JSON value.
(= (record $kind $update $message $chat $title $note $text)
   (tg:ledger-append
     (JsonObject ((JsonPair "received_at" (JsonString "2026-08-15T12:00:00+0200"))
                  (JsonPair "kind" (JsonString $kind)) (JsonPair "allowed" (JsonBool True))
                  (JsonPair "queued" (JsonBool True)) (JsonPair "note" (JsonString $note))
                  (JsonPair "chat_id" (JsonString $chat)) (JsonPair "chat_title" (JsonString $title))
                  (JsonPair "from" (JsonString "@zar")) (JsonPair "from_is_bot" (JsonBool False))
                  (JsonPair "message_id" (JsonNumber $message)) (JsonPair "text" (JsonString $text))
                  (JsonPair "update_id" $update)))))
(= (said $update $message $text) (record "message" (JsonNumber $update) $message "7" "Room" "" $text))
(= (edited $update $message $text) (record "edited_message" (JsonNumber $update) $message "7" "Room" "" $text))
(= (answered $message $text) (record "outbound" JsonNull $message "7" "Room" "own_send" $text))
; A new ledger, and a turn that has prepared nothing.
(= (fresh)
   (let* (($_ (fs:write (tg:ledger) "")) ($_ (tg:set context-frontier -1)) ($_ (tg:set batch-ids ""))) True))
; What preparing a batch of these updates fixes: the ledger's end and their ids.
(= (prepare $ids)
   (let* (($_ (tg:set context-frontier (tg:ledger-size))) ($_ (tg:set batch-ids $ids))) True))

(= (message $id $chat $from $text)
   (json-parse (format-args "{\"update_id\": {}, \"message\": {\"message_id\": {}, \"date\": 1, \"chat\": {\"id\": {}, \"type\": \"private\", \"first_name\": \"Zed\"}, \"from\": {\"id\": {}, \"first_name\": \"Zed\"}, \"text\": \"{}\"}}"
                            ($id $id $chat $from $text))))
(= (take $id $text)
   (tg:intake (format-args "task-{}" ($id)) "42.0" "message" "ordinary" (message $id 42 7 $text)))

; No ledger yet: the route line and nothing else.
(= (window:empty)
   (let* (($_ (if (fs:exists (tg:ledger)) (fs:remove (tg:ledger)) ()))
          ($window (window))
          ($_ (assertEqual (str:starts-with $window "TELEGRAM ROUTE: plain (send ...) is bound to ") True))
          ($_ (assertEqual (contains? $window "chat_id=42. For every other audience") True))
          ($_ (assertEqual (contains? $window "\n\n(no prior conversation events)") True)))
     (println! WINDOW_EMPTY_OK)))

; Through the client's own intake and batch: what was said before is shown,
; this turn's prepared input is not (it is the new activity), and what
; arrives after the batch was prepared waits for the next turn.
(= (window:causal)
   (let* (($_ (fresh))
          ($_ (take 10 "prior human"))
          ($_ (tg:forget-items (tg:pending-items)))
          ($_ (answered "11" "prior answer"))
          ($_ (take 12 "current correction"))
          ($batch (tg:activity-batch))
          ($_ (assertEqual (tg:get batch-ids "") "12"))
          ($_ (assertEqual (tg:get context-frontier -1) (tg:ledger-size)))
          ($_ (take 13 "future input"))
          ($_ (assertEqual (> (tg:ledger-size) (tg:get context-frontier -1)) True))
          ($window (window))
          ($_ (assertEqual (contains? $window "prior human") True))
          ($_ (assertEqual (contains? $window "prior answer") True))
          ($_ (assertEqual (contains? $window "current correction") False))
          ($_ (assertEqual (contains? $window "future input") False))
          ; The route line comes first, and her own answer reads as hers.
          ($_ (assertEqual (str:starts-with $window "TELEGRAM ROUTE: ") True))
          ($_ (assertEqual (contains? $window " OUT GODEL chat=Room message_id=11]\nprior answer") True))
          ($_ (assertEqual (< (str:find $window "prior human") (str:find $window "prior answer")) True))
          ($_ (tg:forget-items (tg:pending-items))))
     (println! WINDOW_CAUSAL_OK)))

; Commands and the replies to them stay in the ledger and out of the window.
(= (window:control)
   (let* (($_ (fresh))
          ($_ (record "message" (JsonNumber "20") "1" "7" "Room" "slash_command:/activity" "/activity"))
          ($_ (record "message" (JsonNumber "22") "2" "7" "Room" "slash_command_other_bot:otherbot" "/mode@OtherBot x"))
          ($_ (answered "3" "activity: idle | steps=0"))
          ($_ (record "callback_query" (JsonNumber "23") "4" "7" "Room" "" "mode:iter"))
          ($_ (said "21" "5" "real conversation"))
          ($window (window))
          ($_ (assertEqual (contains? $window "/activity") False))
          ($_ (assertEqual (contains? $window "/mode@OtherBot") False))
          ($_ (assertEqual (contains? $window "steps=0") False))
          ($_ (assertEqual (contains? $window "mode:iter") False))
          ($_ (assertEqual (contains? $window "real conversation") True)))
     (println! WINDOW_CONTROL_OK)))

; A message and its edits are one entry, the newest; the ledger keeps both.
; The same message id in another chat is another message.
(= (window:revisions)
   (let* (($_ (fresh))
          ($_ (said "30" "8" "draft answer"))
          ($_ (edited "31" "8" "final answer"))
          ($_ (record "message" (JsonNumber "40") "9" "7" "Room A" "" "from A"))
          ($_ (record "message" (JsonNumber "41") "9" "8" "Room B" "" "from B"))
          ($window (window))
          ($_ (assertEqual (contains? $window "draft answer") False))
          ($_ (assertEqual (times $window "final answer") 1))
          ($_ (assertEqual (size-atom (fs:read-lines (tg:ledger))) 4))
          ($_ (assertEqual (contains? $window "from A") True))
          ($_ (assertEqual (contains? $window "from B") True))
          ; One chat's window holds only that chat.
          ($one (tg:conversation-window-for 48 "8"))
          ($_ (assertEqual (contains? $one "from B") True))
          ($_ (assertEqual (contains? $one "from A") False)))
     (println! WINDOW_REVISIONS_OK)))

; An edit beyond the frontier cannot reach back: the revision she saw stays.
; An edit that is this turn's input does not erase the earlier revision
; before she has read it.
(= (window:frontier)
   (let* (($_ (fresh))
          ($_ (said "50" "10" "visible now"))
          ($_ (prepare ""))
          ($_ (edited "51" "10" "future revision"))
          ($window (window))
          ($_ (assertEqual (contains? $window "visible now") True))
          ($_ (assertEqual (contains? $window "future revision") False))
          ($_ (fresh))
          ($_ (said "60" "11" "prior revision"))
          ($_ (edited "61" "11" "current revision"))
          ($_ (prepare "61"))
          ($current (window))
          ($_ (assertEqual (contains? $current "prior revision") True))
          ($_ (assertEqual (contains? $current "current revision") False))
          ; A frontier beyond the file, as after a ledger was replaced, is the file's end.
          ($_ (tg:set context-frontier 99999999))
          ($_ (tg:set batch-ids ""))
          ($_ (assertEqual (contains? (window) "current revision") True)))
     (println! WINDOW_FRONTIER_OK)))

(= (say-many $from $to $text)
   (if (> $from $to) True
     (let $_ (said (format-args "{}" ($from)) (format-args "{}" ($from)) (format-args "{} {}" ($text $from)))
       (say-many (+ $from 1) $to $text))))
(= (repeat $text $n) (if (<= $n 0) "" (str:concat $text (repeat $text (- $n 1)))))

; The budgets: at most the asked number of entries, the newest; one long
; entry is cut; and the whole stays within 30000 characters, route included.
(= (window:budgets)
   (let* (($_ (fresh))
          ($_ (say-many 100 109 "short"))
          ($three (tg:conversation-window 3))
          ($_ (assertEqual (times $three "\n\n[") 3))
          ($_ (assertEqual (contains? $three "short 109") True))
          ($_ (assertEqual (contains? $three "short 107") True))
          ($_ (assertEqual (contains? $three "short 106") False))
          ($_ (assertEqual (< (str:find $three "short 107") (str:find $three "short 109")) True))
          ($_ (fresh))
          ($_ (said "200" "200" (repeat "long " 2000)))
          ($_ (assertEqual (contains? (window) "long…") True))
          ($_ (assertEqual (< (str:char-length (window)) 6400) True))
          ($_ (fresh))
          ($_ (say-many 300 359 (repeat "y" 900)))
          ($full (tg:conversation-window 64))
          ($_ (assertEqual (str:starts-with $full "TELEGRAM ROUTE: ") True))
          ($_ (assertEqual (<= (str:char-length $full) 30000) True))
          ($_ (assertEqual (> (str:char-length $full) 28000) True))
          ($_ (assertEqual (contains? $full "y 359") True))
          ($_ (assertEqual (contains? $full "y 300") False)))
     (println! WINDOW_BUDGETS_OK)))

!(window:empty)
!(window:causal)
!(window:control)
!(window:revisions)
!(window:frontier)
!(window:budgets)
!(println! TELEGRAM_WINDOW_OK)
