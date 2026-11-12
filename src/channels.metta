;configured at runtime:
(= (IRC_channel) (empty))
(= (IRC_server) (empty))
(= (IRC_port) (empty))
(= (IRC_user) (empty))
(= (commchannel) (empty))
(= (MM_URL) (empty))
(= (MM_CHANNEL_ID) (empty))
(= (MM_BOT_TOKEN) (empty))
(= (TG_BOT_TOKEN) (empty))
(= (TG_CHAT_ID) (empty))

;Connect all the channels. (configureChannel) is compiled into
;config/channel.metta by initialize.sh from config/local.toml [channel] kind
;(telegram | irc | mattermost); it keeps the configure argument a literal, which
;PeTTa requires for the specialization that follows to compile correctly:
(= (initChannels)
   (progn (println! "Initializing channels")
          (configureChannel)
          (if (== (commchannel) telegram)
              (progn (configure TG_BOT_TOKEN "")
                     (configure TG_CHAT_ID "")
                     (let $started (py-call (telegram.start_telegram (TG_BOT_TOKEN) (TG_CHAT_ID)))
                       (if (tg:client-metta?) (println! (tg:start)) $started)))
              (if (== (commchannel) irc)
                  (progn (configure IRC_channel ##metta)
                         (configure IRC_server "irc.quakenet.org")
                         (configure IRC_port 6667)
                         (configure IRC_user maxbotnick)
                         (py-call (irc.start_irc (IRC_channel) (IRC_server) (IRC_port) (IRC_user))))
                  (progn (configure MM_URL "https://chat.singularitynet.io")
                         (configure MM_CHANNEL_ID "8fjrmabjx7gupy7e5kjznpt5qh")
                         (configure MM_BOT_TOKEN "")
                         (py-call (mattermost.start_mattermost (MM_URL) (MM_CHANNEL_ID) (MM_BOT_TOKEN))))))))

;Receive the latest user message considering all communication channels:
!(import! &self ./src/input_scope_cetta.metta)
(= (receive-channel)
   (if (== (commchannel) telegram)
       (if (tg:client-metta?) (tg:activity-batch) (py-call (telegram.getActivityBatch)))
       (if (== (commchannel) irc)
           (py-call (irc.getLastMessage))
           (py-call (mattermost.getLastMessage)))))

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
       (if (tg:client-metta?)
         (let $ids (tg:update-ids (tg:prepared-items)) (input-scope:prepare $ids))
         (let $encoded (py-call (telegram.prepared_input_ids_json)) (input-scope:prepare-json $encoded)))
       (if (== (commchannel) irc)
         (let $encoded (py-call (irc.prepared_input_ids_json)) (input-scope:prepare-json $encoded))
         (let $encoded (py-call (mattermost.prepared_input_ids_json)) (input-scope:prepare-json $encoded))))
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
;; tg:quiet-batch?. Other channels and clients always wake it.
(= (channel:quiet-activity?)
   (if (and (== (commchannel) telegram) (tg:client-metta?)) (tg:quiet-batch?) False))
;; Whether this wake may hand back the whole banked budget: see
;; tg:full-wake-batch?. Other channels and clients always may.
(= (channel:full-wake?)
   (if (and (== (commchannel) telegram) (tg:client-metta?)) (tg:full-wake-batch?) True))
;; The arming allowance of the batch's routed sender.
(= (channel:wake-allowance)
   (if (tg:client-metta?) (tg:last-message-arm-loops) (py-call (telegram.lastMessageArmLoops))))

; Existing channel adapters never route or acknowledge native mail.
(= (channel:sleep-until-message $seconds $enabled)
   (if (tg:client-metta?) (tg:sleep-until-message $seconds $enabled)
     (if (mail:enabled?)
       (let $deadline (+ (get_time) $seconds) (channel:mail-wait $deadline $seconds $enabled))
       (py-call (telegram.sleep_until_message $seconds)))))
(= (channel:mail-wait $deadline $seconds $enabled)
   (if (== (py-call (helper.recycle_requested)) 1) "recycle requested"
     (let $incoming (receive-channel)
       (if (not (mail:empty? $incoming)) "woken by channel"
         (if (and $enabled (mail:pending?)) "woken by mail"
           (if (>= (get_time) $deadline) (format-args "rested {}s" ($seconds))
             (let $waited (if $enabled (mail:idle-wait 250) (tg:sleep 0.25))
               (let $_ (py-call (process_supervision.progress "channel-wait" 10))
                 (channel:mail-wait $deadline $seconds $enabled)))))))))

(= (lastMessageIsHuman)
   (if (or (mail:active?) (jobs:active?)) False
   (if (== (commchannel) telegram)
       (== (if (tg:client-metta?) (tg:last-message-is-human) (py-call (telegram.lastMessageIsHuman))) 1)
       True)))

;Send a message to all communication channels:
(= (send $msg)
   (let $safemsg (string-replace $msg  "\n" "\\n")
        (let $outmsg (utils:decode-safe $safemsg)
             (if (== (commchannel) irc)
                 (let $temp (cut) (py-call (irc.send_message $outmsg)))
	                 (if (== (commchannel) telegram)
	                     (let $temp (cut) (if (tg:client-metta?) (tg:send-effect $outmsg "")
	                                            (py-call (telegram.send_effect_message $outmsg))))
	                     (let $temp (cut) (py-call (mattermost.send_message $outmsg))))))))

(= (send-telegram-chat $chat_id $msg)
   (let $safemsg (string-replace $msg  "\n" "\\n")
        (let $outmsg (utils:decode-safe $safemsg)
             (let $temp (cut) (if (tg:client-metta?) (tg:send-effect $outmsg (format-args "{}" ($chat_id)))
                                  (py-call (telegram.send_effect_message_to_chat $chat_id $outmsg)))))))

;; Clean up after a test send: delete your own last k messages to a chat,
;; newest first, using the ids recorded when they were sent. Only your own
;; messages, only within Telegram's 48h window.
(= (delete-my-recent $chat_id $k)
   (if (tg:client-metta?) (tg:delete-my-recent (format-args "{}" ($chat_id)) $k)
     (py-call (telegram.delete_my_recent $chat_id $k))))

;; Exact-address cleanup for a receipt-backed bot-owned Telegram message.
;; Legacy ids absent from the own-send ledger require the authenticated
;; operator's /delete fast path; group-admin deletion authority is not exposed
;; through the model skill.
(= (delete-message-exact $chat_id $message_id)
   (if (tg:client-metta?) (tg:delete-recorded (format-args "{}" ($chat_id)) $message_id)
     (py-call (telegram.delete_recorded_message $chat_id $message_id))))

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
   (if (tg:client-metta?) (tg:recent-activity $k $chat 0)
     (py-call (telegram.recent_activity $k $chat 0))))

(= (recent-messages $chat $k $chars)
   (if (tg:client-metta?) (tg:recent-activity $k $chat $chars)
     (py-call (telegram.recent_activity $k $chat $chars))))
