; The launcher selects an engine. Mail itself is MeTTa/CeTTa only.
!(import! &self ./src/mail_cetta)
(= (mail-send $to $body) (mail:send $to $body))
; A stable occurrence ID is optional, useful for deliberate retries.
(= (mail-send $to $body $id) (mail:send-id $to $body $id))
(= (mail-receive) (mail:receive))
