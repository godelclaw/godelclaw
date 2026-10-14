; The launcher selects an engine. Mail itself is MeTTa/CeTTa only.
!(import! &self ./src/engine_modes.py)
(= (mail:cetta?)
   (case (py-call (engine_modes.active_engine))
     (("cetta" True) (cetta True) ($other False))))
!(if (mail:cetta?) (import! &self ./src/mail_cetta) (import! &self ./src/mail_unavailable))
(= (mail-send $to $body) (mail:send $to $body))
; A stable occurrence ID is optional, useful for deliberate retries.
(= (mail-send $to $body $id) (mail:send-id $to $body $id))
(= (mail-receive) (mail:receive))
