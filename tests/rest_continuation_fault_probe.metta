!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $a $b) (assert (== $a $b)))

;; Fault-injection probes for timed rest continuations (G2 follow-up):
;; (a) Refused working:rest-claim prevents dispatch (0 attempts, 0 effects,
;;     continuation stays pending in memory & waiting durably, refusal recorded).
;; (b) Refused working:rest-finish after the effect propagates the Error,
;;     records the refusal, keeps the effect receipt & ledger action, and does
;;     not replay on the next iteration or after a restart.
;; (c) Refused turn:broker-end after the effect keeps the receipt & ledger
;;     action and does not replay on the next iteration or after a restart —
;;     both for a scheduled rest_timer and for an unscheduled continuation where
;;     working:rest-claim itself must install the durable running barrier.
(= (rf:nonempty $xs)
   (if (== $xs ()) ()
     (let $rest (rf:nonempty (cdr-atom $xs))
       (if (== (car-atom $xs) "") $rest (cons-atom (car-atom $xs) $rest)))))
(= (rf:lines $path)
   (if (not (fs:exists $path)) ()
     (rf:nonempty (str:split "\n" (fs:read-text $path)))))
(= (rf:last $xs $found)
   (if (== $xs ()) $found
     (rf:last (cdr-atom $xs) (car-atom $xs))))
(= (rf:ticket-of $line)
   (case (json-object-get (json-parse $line) "occurrence")
     (((JsonString $occ) (str:byte-slice $occ 0 (- (str:byte-length $occ) 2)))
      ($other ""))))
(= (rf:attempts $ticket)
   (ledger-client:call (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "")
     "attempts" ((ticket $ticket))))
(= (rf:action-statuses $rows)
   (if (== $rows ()) ()
     (case (car-atom $rows)
       (((action $t $r $serial $tool $cmd $status $obs $rc $pay)
         (cons-atom $status (rf:action-statuses (cdr-atom $rows))))
        ($other (rf:action-statuses (cdr-atom $rows)))))))

(= (rf:clear-rest-claim)
   (collapse (match &self (= (working:rest-claim) $body)
               (remove-atom &self (= (working:rest-claim) $body)))))
(= (rf:clear-rest-finish)
   (collapse (match &self (= (working:rest-finish) $body)
               (remove-atom &self (= (working:rest-finish) $body)))))
(= (rf:clear-after-broker-report)
   (collapse (match &self (= (turn:after-broker-report $t $k) $body)
               (remove-atom &self (= (turn:after-broker-report $t $k) $body)))))

!(bind! &rf-broker-fault (new-state 0))
(= (rf:after-broker-report $turn $ticket)
   (if (== (get-state &rf-broker-fault) 1)
     (let $_ (change-state! &rf-broker-fault 0)
       (Error turn:broker-end forced-after-result))
     True))

(= (rf:step-continuation $state $timed-out)
   (let* (($periphery (process-periphery $state))
          ($res (applyTimedContinuation $periphery $timed-out))
          ($trial (case $res
                    (((Error $call $why) (Error $call $why))
                     ($settled (candidate $settled)))))
          ($outcome (protectedOutcome (catch $trial)))
          ($next (process-step $state $outcome)))
     ($res $next)))

!(initMemory)
!(configure commchannel telegram)
!(let* (($log (system:getenv-or-default "METTACLAW_CONTINUATION_LOG" ""))
        ($receipts (system:getenv-or-default "METTACLAW_EFFECT_RECEIPT_PATH" ""))
        ; ---------------------------------------------------------------------
        ; (a) Refused working:rest-claim must prevent dispatch completely.
        ; ---------------------------------------------------------------------
        ($_ (assertEqual (working:rest-schedule 0
              (quote (shell "printf 'FIRED_AB\\n' >> \"$METTACLAW_CONTINUATION_LOG\""))) 1))
        ($boot-a (once (initLoop)))
        ($_ (rf:clear-rest-claim))
        ($_ (add-atom &self (= (working:rest-claim)
              (Error working-state (checkpoint forced-claim-refused)))))
        (($res-a $state-a) (rf:step-continuation $boot-a 1))
        ($_ (assertEqual $res-a (Error working-state (checkpoint forced-claim-refused))))
        ($_ (assertEqual (rf:lines $log) ()))
        ($_ (assertEqual (rf:lines $receipts) ()))
        ($_ (assertEqual (state:ledger "actions" ("all")) ()))
        ($_ (assertEqual (strategy:batch) none))
        ($_ (assertEqual (not (== (loop-pending-continuation (process-periphery $state-a)) no-continuation)) True))
        ($_ (assertEqual (case (working:rest-pending) ((("continuation-value" $_) True) ($other False))) True))
        ($health-a (health:read))
        ($_ (assertEqual (sf:get $health-a "last_failure_type" "") "host-transition-error"))
        ($_ (assertEqual (sf:get $health-a "local_failure_count" 0) 1))
        ; Restore working:rest-claim before (b).
        ($_ (rf:clear-rest-claim))
        ($_ (add-atom &self (= (working:rest-claim) (once (working-state:rest-claim)))))
        ; ---------------------------------------------------------------------
        ; (b) Refused working:rest-finish after effect keeps receipt and must
        ;     not replay on the next iteration or after a restart.
        ; ---------------------------------------------------------------------
        ($_ (rf:clear-rest-finish))
        ($_ (add-atom &self (= (working:rest-finish)
              (Error working-state (checkpoint forced-finish-refused)))))
        (($res-b1 $state-b1) (rf:step-continuation $state-a 1))
        ($_ (assertEqual $res-b1 (Error working-state (checkpoint forced-finish-refused))))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB")))
        ($receipts-b1 (rf:lines $receipts))
        ($_ (assertEqual (size-atom $receipts-b1) 1))
        ($ticket-b (rf:ticket-of (rf:last $receipts-b1 "")))
        ($_ (assertEqual (> (str:byte-length $ticket-b) 0) True))
        ($_ (assertEqual (case (rf:attempts $ticket-b)
                           ((((attempt $t 0 $cmd $result $ord)) (not (== $result none)))
                            ($other False))) True))
        ($_ (assertEqual (rf:action-statuses (state:ledger "actions" ("all"))) ("returned")))
        ($health-b (health:read))
        ($_ (assertEqual (sf:get $health-b "local_failure_count" 0) 2))
        ; Next iteration in the same process must not replay even while pending-continuation remains on periphery.
        (($res-b2 $state-b2) (rf:step-continuation $state-b1 1))
        ($_ (assertEqual (turn:failed? $res-b2) False))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 1))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 1))
        ; Restart (working:boot via initLoop) must retire running timer and must not replay.
        ($boot-b (once (initLoop)))
        ($_ (assertEqual (loop-pending-continuation (process-periphery $boot-b)) no-continuation))
        (($res-b3 $state-b3) (rf:step-continuation $boot-b 1))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 1))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 1))
        ; Restore working:rest-finish before (c).
        ($_ (rf:clear-rest-finish))
        ($_ (add-atom &self (= (working:rest-finish) (once (working-state:rest-finish)))))
        ; ---------------------------------------------------------------------
        ; (c1) Refused turn:broker-end after effect on a scheduled timer keeps
        ;      receipt and must not replay on next iteration or after restart.
        ; ---------------------------------------------------------------------
        ($_ (rf:clear-after-broker-report))
        ($_ (add-atom &self (= (turn:after-broker-report $bt $bk) (rf:after-broker-report $bt $bk))))
        ($_ (assertEqual (working:rest-schedule 0
              (quote (shell "printf 'FIRED_C1\\n' >> \"$METTACLAW_CONTINUATION_LOG\""))) 1))
        ($boot-c1 (once (initLoop)))
        ($_ (change-state! &rf-broker-fault 1))
        (($res-c1a $state-c1a) (rf:step-continuation $boot-c1 1))
        ($_ (assertEqual $res-c1a (Error turn:broker-end forced-after-result)))
        ($_ (assertEqual (strategy:batch) none))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB" "FIRED_C1")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 2))
        ($_ (assertEqual (rf:action-statuses (state:ledger "actions" ("all"))) ("returned" "returned")))
        ($health-c1 (health:read))
        ($_ (assertEqual (sf:get $health-c1 "local_failure_count" 0) 3))
        ; Next iteration in same process must not replay.
        (($res-c1b $state-c1b) (rf:step-continuation $state-c1a 1))
        ($_ (assertEqual (turn:failed? $res-c1b) False))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB" "FIRED_C1")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 2))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 2))
        ; Restart must not replay.
        ($boot-c1-after (once (initLoop)))
        ($_ (assertEqual (loop-pending-continuation (process-periphery $boot-c1-after)) no-continuation))
        (($res-c1c $state-c1c) (rf:step-continuation $boot-c1-after 1))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB" "FIRED_C1")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 2))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 2))
        ; ---------------------------------------------------------------------
        ; (c2) Refused turn:broker-end on an unscheduled in-memory continuation
        ;      (no pre-existing rest_timer): working:rest-claim must still have
        ;      installed the durable running barrier so next iteration and
        ;      restart do not replay even though turn:begin-continuation would
        ;      otherwise allocate a new ledger ticket.
        ; ---------------------------------------------------------------------
        ($pu (coordinate iteration 17
               (coordinate loops 0
                 (coordinate sleep-interval 1
                   (coordinate last-results before
                     (coordinate last-heartbeat 9
                       (coordinate active-policy godelclaw
                         (coordinate autonomous-ready 0
                           (coordinate pending-continuation
                             (continuation-value (quote (shell "printf 'FIRED_C2\\n' >> \"$METTACLAW_CONTINUATION_LOG\"")))
                             coordinates-empty)))))))))
        ($state-c2 (rooted weak-process-core-v1 $pu))
        ($_ (change-state! &rf-broker-fault 1))
        (($res-c2a $state-c2a) (rf:step-continuation $state-c2 1))
        ($_ (assertEqual $res-c2a (Error turn:broker-end forced-after-result)))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB" "FIRED_C1" "FIRED_C2")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 3))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 3))
        ; Next iteration must NOT replay!
        (($res-c2b $state-c2b) (rf:step-continuation $state-c2a 1))
        ($_ (assertEqual (turn:failed? $res-c2b) False))
        ($_ (assertEqual (rf:lines $log) ("FIRED_AB" "FIRED_C1" "FIRED_C2")))
        ($_ (assertEqual (size-atom (rf:lines $receipts)) 3))
        ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 3))
        ; Restart must retire the running marker cleanly and not replay.
        ($boot-c2-after (once (initLoop)))
        ($_ (assertEqual (loop-pending-continuation (process-periphery $boot-c2-after)) no-continuation)))
   (println! REST_CONTINUATION_FAULT_OK))
