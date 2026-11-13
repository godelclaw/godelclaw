!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; With the observed strategy on, a timed continuation is a batch the broker
;; admitted under a ticket: its command is recorded in the ledger as attempted
;; before it runs, its result is recorded at that attempt, and its receipt
;; names it (src/strategy_attempts_cetta.metta).
(= (sc:last $lines $found)
   (if (== $lines ()) $found
     (sc:last (cdr-atom $lines) (if (== (car-atom $lines) "") $found (car-atom $lines)))))
(= (sc:occurrence)
   (let $line (sc:last (str:split "\n" (fs:read-text (system:getenv-or-default "METTACLAW_EFFECT_RECEIPT_PATH" ""))) "")
     (case (json-object-get (json-parse $line) "occurrence") (((JsonString $text) $text) ($other "")))))
(= (sc:ticket $occurrence)
   (str:byte-slice $occurrence 0 (- (str:byte-length $occurrence) 2)))
(= (sc:attempts $ticket)
   (ledger-client:call (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "") "attempts" ((ticket $ticket))))

!(let* (($periphery
          (coordinate iteration 7
            (coordinate loops 0
              (coordinate sleep-interval 1
                (coordinate last-results before
                  (coordinate last-heartbeat 9
                    (coordinate active-policy godelclaw
                      (coordinate autonomous-ready 0
                        (coordinate pending-continuation
                          (continuation-value (quote (help "pin")))
                          coordinates-empty)))))))))
         ($resumed (applyTimedContinuation $periphery 1))
         ($_ (assertEqual (loop-pending-continuation $resumed) (quote no-continuation)))
         ($occurrence (sc:occurrence))
         ($_ (println! (CONTINUATION_ATTEMPT $occurrence)))
         ($_ (assertEqual (str:ends-with $occurrence ":0") True))
         ($ticket (sc:ticket $occurrence))
         ($_ (assertEqual (> (str:byte-length $ticket) 0) True))
         ($_ (assertEqual (case (sc:attempts $ticket)
                            ((((attempt $t 0 "[\"help\",\"pin\"]" $result $order)) (not (== $result none)))
                             ($other False)))
                          True))
         ($_ (assertEqual (strategy:batch) none)))
     (println! STRATEGY_CONTINUATION_ATTEMPT_OK))
