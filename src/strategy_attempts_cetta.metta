; The effect broker's command attempts. A batch the host's broker admitted
; under a ticket (strategy_host.broker_begin, begin_continuation) is opened
; here for its turn. Each of its commands is recorded in the input ledger as
; attempted, at its position in the batch, before it runs; a broker that
; recovers finds an attempt without a result uncertain, never not run. The
; display shows the command's dispatch under the attempt's identity,
; <ticket>:<position>, and keeps the epoch it saw until the receipt
; (display:receipt).
;
; - The batch is this process's own: one at a time, closed when the broker
;   reports it, and replaced by the next batch the broker admits (a batch a
;   failure left open is never reused: broker turns do not repeat). A command
;   of a turn without an open batch runs unrecorded, as it did before the
;   broker existed.
; - An attempt the ledger did not record as new (it exists, or the ledger did
;   not answer) is the host's failure, raised as its own failures are
;   (strategy_host.attempt_not_recorded): the batch fails there and its
;   command does not run. This dialect has no native raise.
;
; Entries: strategy:open-batch, strategy:close-batch, strategy:reserve,
; strategy:attempt.
!(import! &self (library system))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self ./ledger_client.metta)
!(import! &self ./display_cetta.metta)
!(import! &self ./public_worklog_cetta.metta)

!(bind! &strategy-batch (new-space))

; The open batch, (turn ticket next-position), or none.
(= (strategy:batch)
   (case (collapse (match &strategy-batch (StrategyBatchV1 $turn $ticket $next) ($turn $ticket $next)))
     ((() none) ($batches (car-atom $batches)))))
(= (strategy:close-batch)
   (let $_ (collapse (match &strategy-batch (StrategyBatchV1 $turn $ticket $next)
                       (remove-atom &strategy-batch (StrategyBatchV1 $turn $ticket $next))))
     True))
; The broker admitted a batch for $turn under $ticket; "" is no ticket (the
; batch has no commands to record).
(= (strategy:open-batch $turn $ticket)
   (let $_ (strategy:close-batch)
     (if (== $ticket "") True
       (let $_ (add-atom &strategy-batch (StrategyBatchV1 $turn $ticket 0)) True))))

; Records $command as the open batch's next attempt and shows its dispatch: 1,
; or 0 when no batch is open for $turn.
(= (strategy:reserve $turn $command)
   (case (strategy:batch)
     ((none 0)
      (($open $ticket $position)
       (if (== $open $turn) (strategy:record $turn $ticket $position $command) 0)))))
(= (strategy:record $turn $ticket $position $command)
   (let $text (pw:json-result (json:stringify-python-with (erv:cross $command)
                ((ensure-ascii False) (sort-keys True) (separators "," ":"))))
     (if (erv:failed? $text) (strategy:not-recorded $ticket $position $text)
       (case (catch (ledger-client:call (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "")
                      "attempt-begin" ($ticket $position $text (system:uuid4-hex))))
         ((created (strategy:attempted $turn $ticket $position $command))
          ($refused (strategy:not-recorded $ticket $position $refused)))))))
(= (strategy:attempted $turn $ticket $position $command)
   (let* (($_ (strategy:close-batch))
          ($next (+ $position 1))
          ($_ (add-atom &strategy-batch (StrategyBatchV1 $turn $ticket $next)))
          ($_ (display:event "tool.dispatching" (strategy:identity $ticket $position)
                             (erv:str (erv:cross $command)) "dispatching")))
     1))
(= (strategy:not-recorded $ticket $position $why)
   (py-call (strategy_host.attempt_not_recorded $ticket $position (repr $why))))
(= (strategy:identity $ticket $position) (str:concat $ticket (str:concat ":" (repr $position))))

; The attempt a receipt of $turn belongs to: (StrategyAttemptV1 identity
; position) of the batch's last reserved command, or none.
(= (strategy:attempt $turn)
   (case (strategy:batch)
     ((none none)
      (($open $ticket $next)
       (if (and (== $open $turn) (> $next 0))
         (let $position (- $next 1) (StrategyAttemptV1 (strategy:identity $ticket $position) $position))
         none)))))
