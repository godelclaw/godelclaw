;configured at runtime:
(= (commchannel) (empty))

;Connect all the channels. (configureChannel) is compiled into
;config/channel.metta by initialize.sh from config/local.toml [channel] kind
;(telegram | irc | mattermost); it keeps the configure argument a literal, which
;PeTTa requires for the specialization that follows to compile correctly:
(= (initChannels)
   (progn (println! "Initializing channels")
          (configureChannel)
          (if (== (commchannel) telegram)
              (println! (tg:start))
              ;; IRC and Mattermost connect in a service of their own
              ;; (run.sh chat-channel), which the loop meets in the chat spool.
              (println! (chat:started (commchannel))))))

;Receive the latest user message considering all communication channels:
!(import! &self ./src/input_scope_cetta.metta)
(= (receive-channel)
   (if (== (commchannel) telegram) (tg:activity-batch) (chat:receive (commchannel))))

; Conversation arrivals retain their own acknowledgement path. Native mail
; is its own input, so settling a conversation cannot settle unrelated mail.
(= (receive)
   (let* (($_ (input-scope:reset)) ($_ (mail:reset)) ($_ (jobs:reset)))
     (let $conversation (receive-channel)
       (if (mail:empty? $conversation)
         (channel:receive-mail-or-job)
         (channel:capture-conversation $conversation)))))
(= (channel:capture-conversation $conversation)
   (let $_
     (if (== (commchannel) telegram)
       (let $ids (tg:update-ids (tg:prepared-items)) (input-scope:prepare $ids))
       (input-scope:prepare (chat:presented-ids)))
     (if (input-scope:all-settled (input-scope:ids))
       (let $_ (input-scope:reset) (channel:receive-mail-or-job)) $conversation)))
; Transport diagnostics are observations, not fabricated deliveries. The
; direct receive APIs retain their diagnostic text; the loop logs it.
(= (channel:receive-mail-or-job)
   (let $mail (mail:receive)
     (if (mail:active?) $mail
       (let* (($_ (if (mail:empty? $mail) True (println! (MAIL_RECEIVE_UNAVAILABLE $mail))))
              ($job (jobs:receive)))
         (if (jobs:active?) $job
           (let $_ (if (mail:empty? $job) True (println! (JOB_RECEIVE_UNAVAILABLE $job))) ""))))))

;; Whether pending conversation may keep a parked agent waiting: see
;; tg:quiet-batch? and chat:quiet-batch?.
(= (channel:quiet-activity?)
   (if (== (commchannel) telegram) (tg:quiet-batch?) (chat:quiet-batch?)))
;; The turn that read a chat channel's messages is committed: they leave its
;; inbox. Telegram's batch is settled by tg:ack-activity-batch.
(= (channel:ack-chat)
   (if (== (commchannel) telegram) 1 (chat:ack (commchannel))))
;; Whether this wake may hand back the whole banked budget: see
;; tg:full-wake-batch?. Other channels and clients always may.
(= (channel:full-wake?)
   (if (== (commchannel) telegram) (tg:full-wake-batch?) True))
;; The arming allowance of the batch's routed sender.
(= (channel:wake-allowance)
   (tg:last-message-arm-loops))

(= (channel:sleep-until-message $seconds $enabled)
   (tg:sleep-until-message $seconds $enabled))

(= (lastMessageIsHuman)
   (if (or (mail:active?) (jobs:active?)) False
   (if (== (commchannel) telegram)
       (== (tg:last-message-is-human) 1)
       True)))

;Send a message to all communication channels:
(= (send $msg)
   (let $safemsg (string-replace $msg  "\n" "\\n")
        (let $outmsg (utils:decode-safe $safemsg)
             (if (== (commchannel) telegram)
                 (let $temp (cut) (tg:send-effect $outmsg ""))
                 (let $temp (cut) (chat:send (commchannel) $outmsg))))))

(= (send-telegram-chat $chat_id $msg)
   (let $safemsg (string-replace $msg  "\n" "\\n")
        (let $outmsg (utils:decode-safe $safemsg)
             (let $temp (cut) (tg:send-effect $outmsg (format-args "{}" ($chat_id)))))))

;; Clean up after a test send: delete your own last k messages to a chat,
;; newest first, using the ids recorded when they were sent. Only your own
;; messages, only within Telegram's 48h window.
(= (delete-my-recent $chat_id $k)
   (tg:delete-my-recent (format-args "{}" ($chat_id)) $k))

;; Exact-address cleanup for a receipt-backed bot-owned Telegram message.
;; Legacy ids absent from the own-send ledger require the authenticated
;; operator's /delete fast path; group-admin deletion authority is not exposed
;; through the model skill.
(= (delete-message-exact $chat_id $message_id)
   (tg:delete-recorded (format-args "{}" ($chat_id)) $message_id))

;Search the internet for some information:
(= (search $msg)
   (let $query (utils:decode-safe $msg)
        (py-call (websearch.search $query))))

(= (search-tavily $msg)
   (let $query (utils:decode-safe $msg)
        (py-call (websearch.search_tavily $query))))

(= (search-ddg $msg)
   (let $query (utils:decode-safe $msg)
        (py-call (websearch.search_ddg $query))))

;; On-demand short-term recall for any chat the bot is in: last $k messages
;; (both sides) from the local update log, matched by chat title or id.
(= (recent-messages $chat $k)
   (tg:recent-activity $k $chat 0))

(= (recent-messages $chat $k $chars)
   (tg:recent-activity $k $chat $chars))
