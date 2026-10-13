; The launcher selects an engine. Mail itself is MeTTa/CeTTa only.
!(import_prolog_function getenv)
!(import_prolog_function string_length)
(= (mail:cetta?)
   (case (getenv "METTACLAW_ACTIVE_ENGINE")
     (("cetta" True) (cetta True) ($other False))))
!(if (mail:cetta?) (import! &self ./src/mail_cetta) (import! &self ./src/mail_unavailable))
(= (mail-send $to $body) (mail:send $to $body))
; A stable occurrence ID is optional, useful for deliberate retries.
(= (mail-send $to $body $id) (mail:send-id $to $body $id))
(= (mail-receive) (mail:receive))
