;; Replaceable model-output adapter shared by live and shadow turns.
;; It owns no authority: it only balances, reads, and projects the proposed
;; outer command list. The effect broker remains a separate addition below it.

;; Undelivered prose, parenthesis balancing and command-batch normalization
;; are native.
!(progn (import! &self ./src/undelivered_prose.metta)
        (import! &self ./src/balance_parentheses.metta)
        (import! &self ./src/command_batches_cetta.metta)
        (add-atom &self (= (model-response-normalize $raw)
                          (command-batches:normalize $raw))))

(= (model-response-balance $raw)
   (balance:parentheses $raw))

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
