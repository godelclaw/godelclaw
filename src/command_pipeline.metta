;; Replaceable model-output adapter shared by live and shadow turns.
;; It owns no authority: it only balances, reads, and projects the proposed
;; outer command list. The effect broker remains a separate addition below it.

;; Undelivered prose is native on CeTTa; SWI-PeTTa keeps src/helper.py's
;; oracle. The engine choice and command vocabulary are captured once at boot.
!(import! &self ./src/engine_modes.py)
(= (model-response:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (model-response:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/undelivered_prose.metta)
     (import! &self ./src/undelivered_prose_python.metta))

(= (model-response-balance $raw)
   (py-call (helper.balance_parentheses $raw)))

;; The text of the raw model response that no command carries, or "".
(= (model-response-undelivered-prose $raw)
   (prose:undelivered $raw))

(= (model-response-envelope $balanced)
   (if (== "(" (first_char $balanced))
       $balanced
       (repr (no_commands_parsed))))

(= (model-response-read $response)
   (catch (sread (utils:decode-safe $response))))

(= (model-response-commands $parsed)
   (case $parsed
     (((Error $a $b) ())
      ((no_commands_parsed) ())
      ;; sread returns one top-level empty expression for the wire text `()`.
      ;; That is an empty command batch, not a batch containing one empty
      ;; command.
      ((()) ())
      ($valid $valid))))
