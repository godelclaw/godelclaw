;; Telegram, through the agent's durable channel service. Input stays with the
;; service until a provider turn starts whose saved history holds it; a send
;; is a durable intent that the independent control responder submits.
(= (receive) (host:telegram-receive))

(= (send $content) (host:telegram-send "telegram" $content))
