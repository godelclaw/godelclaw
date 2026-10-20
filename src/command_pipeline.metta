;; Replaceable model-output adapter shared by live and shadow turns.
;; It owns no authority: it only balances, reads, and projects the proposed
;; outer command list. The effect broker remains a separate addition below it.

(= (model-response-balance $raw)
   (py-call (helper.balance_parentheses $raw)))

(= (model-response-envelope $balanced)
   (if (== "(" (first_char $balanced))
       $balanced
       (repr (no_commands_parsed))))

(= (model-response-read $response)
   (catch (sread (py-call (helper.decode_safe_text $response)))))

(= (model-response-commands $parsed)
   (case $parsed
     (((Error $a $b) ())
      ((no_commands_parsed) ())
      ;; sread returns one top-level empty expression for the wire text `()`.
      ;; That is an empty command batch, not a batch containing one empty
      ;; command.
      ((()) ())
      ($valid $valid))))
