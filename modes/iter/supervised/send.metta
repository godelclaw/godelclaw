;; Sends through a channel. telegram addresses the operator; telegram_CHAT_THREAD
;; names a group or thread from the channel routes. Any other name is a
;; channel module in channels/, loaded here and asked to send.
(= (description) "Send a message through a communication channel.")
(= (parameters) (channel content))

(= (run $channel $content) (host:send-tool $channel $content))
