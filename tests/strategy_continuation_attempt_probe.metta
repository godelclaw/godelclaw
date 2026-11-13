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
; The display's records of an item: (kind epoch) of each, in order.
(= (sc:shown $item)
   (let $directory (system:getenv-or-default "METTACLAW_STREAM_DIR" "")
     (sc:records-of $item (sc:lines-of $directory (fs:entries $directory)))))
(= (sc:lines-of $directory $names)
   (if (== $names ()) ()
     (sc:append (str:split "\n" (fs:read-text (path:join $directory (car-atom $names))))
                (sc:lines-of $directory (cdr-atom $names)))))
(= (sc:append $front $back) (if (== $front ()) $back (cons-atom (car-atom $front) (sc:append (cdr-atom $front) $back))))
(= (sc:records-of $item $lines)
   (if (== $lines ()) ()
     (let $rest (sc:records-of $item (cdr-atom $lines))
       (if (== (car-atom $lines) "") $rest
         (let $record (json-parse (car-atom $lines))
           (if (== (json-object-get $record "item") (JsonString $item))
             (cons-atom ((json-object-get $record "kind") (json-object-get $record "epoch")) $rest)
             $rest))))))
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
         ($_ (assertEqual (strategy:batch) none))
         ; Its dispatch and its end are shown under the attempt, in one epoch.
         ($_ (assertEqual (sc:shown $occurrence)
                          (((JsonString "tool.dispatching") (JsonString "initial"))
                           ((JsonString "tool.finished") (JsonString "initial"))))))
     (println! STRATEGY_CONTINUATION_ATTEMPT_OK))
