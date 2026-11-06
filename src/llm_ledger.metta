; The LLM call ledger in the native store: collection `provider` of the agent's ledger store,
; served by the same server as the input ledger (src/input_ledger_server.metta). It holds what
; llm-budget.sqlite3 holds, and its writes are the transitions of the live SQLite code
; (src/llm_policy_cetta.metta, src/native_lifetime_cetta.metta, src/llm_policy.py,
; src/native_lifetime.py), each one transaction:
;
;   (llm:request <id> <started> <reserved> <status> <output> <provider> <mode> <finish> <owner> <fingerprint>)
;                                                     requests
;   (llm:cooldown <provider> <until>)                 cooldowns
;   (llm:call <id> <epoch> <pid> <start> <created> <updated> <state> <guard> <transport> <soft>
;             <bytes> <quiet> <reason> <activity> <lane> <purpose> <notified>)
;                                                     native_calls without its result
;   (llm:result <id> <text>)                          native_calls.result, one atom per call that has one
;   (llm:event <sequence> <call> <event> <observed> <detail>)
;                                                     llm_call_history; <detail> is its JSON text
;   (llm:recent <id> <older> <created>)               the turn calls newest first (ORDER BY created DESC,
;                                                     calls of one created time in the order they came, as
;                                                     SQLite's index lists them): <older> is the next one
;   (llm:recovery <id> <recovered-at> <reason> <cutoff>)
;                                                     admission_recovery, kept as evidence; nothing writes it
;   (llm:state <key> <value>)                         "selected": the ledger's selection (below);
;                                                     "event-tail": the last history sequence;
;                                                     "recent-turn": the newest turn call, or none
;
; A NULL column is the symbol none; text columns are strings, numbers are numbers. The
; history keeps one event per (call, event): the first write of a pair wins, as INSERT OR
; IGNORE does under UNIQUE(call_id,event).
;
; Selection. The SQLite file stays the ledger of an instance until a migration completes:
;   no "selected" record                              legacy: callers keep using the SQLite file
;   (llm:state "selected" (llm-ledger-v1 <source> <at>))  native: this collection is the ledger
;   (llm:state "selected" (llm-ledger-legacy <why>))  legacy again, after a rollback
; Any other value (an import in progress included) answers an Error, never legacy. Every verb
; answers llm-legacy on a legacy store before it reads or writes anything else, so a caller
; asks the server once per operation and falls back to SQLite only on that answer.
;
; Writes carry a request id made by the caller once per operation (rq1.<uuid>): a repeat after
; a lost reply replays the first answer; the same id with other fields is refused by the
; engine. Reads open one snapshot. Process liveness (the owner of a pending request, the
; incarnation of a claimed call) is observed here, inside the transaction, through
; src/process_identity_cetta.metta: the server runs on the agent's host as the agent's user.

!(import! &self (library db))
!(import! &self (library str))
!(import! &self (library list))
!(import! &self ./process_identity_cetta.metta)

(= (llm-ledger:ensure $db)
   (db:ensure $db provider ((SpaceIndexV1 11 1) (SpaceIndexV1 11 4) (SpaceIndexV1 3 1)
                            (SpaceIndexV1 18 1) (SpaceIndexV1 18 7) (SpaceIndexV1 6 2) (SpaceIndexV1 6 3)
                            (SpaceIndexV1 4 1) (SpaceIndexV1 5 1))))

; ---- small helpers ----

(= (llm-ledger:append $first $second)
   (if (== $first ()) $second
     (let* ((($head $tail) (decons-atom $first)))
       (cons-atom $head (llm-ledger:append $tail $second)))))

(= (llm-ledger:state-of $space $key $initial)
   (case (collapse (match $space (llm:state $key $value) $value))
     ((() $initial)
      (($value) $value)
      ($other (Error llm-ledger:state (several $key))))))

(= (llm-ledger:state-put $space $key $value)
   (case (collapse (match $space (llm:state $key $old) (llm:state $key $old)))
     ((() (let $_ (add-atom $space (llm:state $key $value)) done))
      (($old) (input-ledger:replace $space $old (llm:state $key $value)))
      ($other (Error llm-ledger:state (several $key))))))

(= (llm-ledger:selection $space)
   (case (collapse (match $space (llm:state "selected" $value) $value))
     ((() legacy)
      (((llm-ledger-v1 $source $at)) native)
      (((llm-ledger-legacy $why)) legacy)
      ($other (Error llm-ledger:selection (unsupported $other))))))

(= (llm-ledger:reason-detail $reason) (str:concat "{\"reason\":\"" (str:concat $reason "\"}")))

; ---- the history ----

(= (llm-ledger:has-event $space $call $event)
   (not (== (collapse (match $space (llm:event $q $call $event $o $d) $q)) ())))

(= (llm-ledger:event-detail $space $call $event)
   (case (collapse (match $space (llm:event $q $call $event $o $d) $d))
     ((() none) (($detail) $detail) ($other none))))

; INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail): True when added.
(= (llm-ledger:event-in $space $call $event $now $detail)
   (if (llm-ledger:has-event $space $call $event)
       False
       (let* (($n (+ (llm-ledger:state-of $space "event-tail" 0) 1))
              ($_ (llm-ledger:state-put $space "event-tail" $n))
              ($_ (add-atom $space (llm:event $n $call $event $now $detail))))
         True)))

; ---- requests and cooldowns ----

(= (llm-ledger:request-of $space $id)
   (collapse (match $space (llm:request $id $st $rv $s $o $p $m $f $ow $fp)
               (llm:request $id $st $rv $s $o $p $m $f $ow $fp))))

(= (llm-ledger:request-status $space $id)
   (case (llm-ledger:request-of $space $id)
     ((((llm:request $i $st $rv $s $o $p $m $f $ow $fp)) $s)
      ($other none))))

(= (llm-ledger:pending-requests $space)
   (collapse (match $space (llm:request $id $st $rv "pending" $o $p $m $f $ow $fp)
               (llm:request $id $st $rv "pending" $o $p $m $f $ow $fp))))

(= (llm-ledger:cooldown-of $space $provider)
   (case (collapse (match $space (llm:cooldown $provider $until) $until))
     ((() none) (($until) $until) ($other (Error llm-ledger:cooldown (several $provider))))))

; INSERT INTO cooldowns ... ON CONFLICT(provider) DO UPDATE SET until=MAX(until,excluded.until)
(= (llm-ledger:cooldown-raise $space $provider $until)
   (case (collapse (match $space (llm:cooldown $provider $old) $old))
     ((() (let $_ (add-atom $space (llm:cooldown $provider $until)) done))
      (($old) (if (> $until $old)
                  (input-ledger:replace $space (llm:cooldown $provider $old) (llm:cooldown $provider $until))
                  done))
      ($other (Error llm-ledger:cooldown (several $provider))))))

; lp:recover / recover_interrupted: a pending request whose owner process is gone has an
; unknown remote outcome. Answers the number recovered.
(= (llm-ledger:owner-exited $space $rows $now $count)
   (if (== $rows ()) $count
     (let* ((($row $rest) (decons-atom $rows)))
       (case $row
         (((llm:request $id $st $rv $s $o $p $m $f $ow $fp)
           (if (process:alive? $ow)
               (llm-ledger:owner-exited $space $rest $now $count)
               (let* (($_ (input-ledger:replace $space $row (llm:request $id $st $rv "unknown" $o $p $m $f $ow $fp)))
                      ($_ (llm-ledger:event-in $space $id "unknown" $now (llm-ledger:reason-detail "owner-exited"))))
                 (llm-ledger:owner-exited $space $rest $now (+ $count 1))))))))))

(= (llm-ledger:recover-requests-in $space $now)
   (llm-ledger:owner-exited $space (llm-ledger:pending-requests $space) $now 0))

; ---- calls ----

(= (llm-ledger:calls-in-state $space $state)
   (collapse (match $space (llm:call $i $e $pid $s $c $u $state $g $t $so $b $q $r $a $l $pu $n)
               (llm:call $i $e $pid $s $c $u $state $g $t $so $b $q $r $a $l $pu $n))))

(= (llm-ledger:active-calls $space)
   (llm-ledger:append (llm-ledger:calls-in-state $space "claimed") (llm-ledger:calls-in-state $space "submitted")))

(= (llm-ledger:call-of $space $id)
   (collapse (match $space (llm:call $id $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
               (llm:call $id $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))))

(= (llm-ledger:active-state $state) (or (== $state "claimed") (== $state "submitted")))

; The one call row of (id, epoch) whose state is claimed or submitted, or ().
(= (llm-ledger:active-call $space $id $epoch)
   (case (llm-ledger:call-of $space $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
       (if (and (== $e $epoch) (llm-ledger:active-state $st))
           ((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
           ()))
      ($other ()))))

(= (llm-ledger:has-call-epoch $space $id $epoch)
   (case (llm-ledger:call-of $space $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)) (== $e $epoch))
      ($other False))))

(= (llm-ledger:result-put $space $id $text)
   (let* (($old (collapse (match $space (llm:result $id $r) (llm:result $id $r))))
          ($_ (if (== $old ()) done (remove-atom $space (car-atom $old)))))
     (if (== $text none) done (add-atom $space (llm:result $id $text)))))

(= (llm-ledger:result-of $space $id)
   (case (collapse (match $space (llm:result $id $text) $text))
     ((() none) (($text) $text) ($other none))))

; The newest turn calls, newest first: a new call goes after every call created at or after
; its time (normally none: it becomes the head), so calls of one time keep their arrival order.
(= (llm-ledger:recent-created $space $id)
   (case (collapse (match $space (llm:recent $id $older $created) $created))
     ((($created) $created) ($other none))))

(= (llm-ledger:recent-insert $space $id $created)
   (let $head (llm-ledger:state-of $space "recent-turn" none)
     (if (llm-ledger:not-later $space $head $created)
         (let* (($_ (add-atom $space (llm:recent $id $head $created))))
           (llm-ledger:state-put $space "recent-turn" $id))
         (llm-ledger:recent-after $space $head $id $created))))

; True when $node is none or was created before $created.
(= (llm-ledger:not-later $space $node $created)
   (if (== $node none) True (< (llm-ledger:recent-created $space $node) $created)))

(= (llm-ledger:recent-after $space $node $id $created)
   (case (collapse (match $space (llm:recent $node $older $c) (llm:recent $node $older $c)))
     ((((llm:recent $n $older $c))
       (if (llm-ledger:not-later $space $older $created)
           (let* (($_ (input-ledger:replace $space (llm:recent $n $older $c) (llm:recent $n $id $c))))
             (let $_ (add-atom $space (llm:recent $id $older $created)) done))
           (llm-ledger:recent-after $space $older $id $created)))
      ($other (Error llm-ledger:recent (broken $node))))))

; nl:recover / native_lifetime.recover. A claimed or submitted call whose request is terminal
; (and that has not moved for 2 s) is settled from the request; one whose process incarnation
; is gone, or was never recorded, becomes uncertain (abandoned for compaction). Answers the
; number of calls moved.
(= (llm-ledger:recover-calls-in $space $now)
   (llm-ledger:recover-each $space (llm-ledger:active-calls $space) $now 0))

(= (llm-ledger:recover-each $space $rows $now $count)
   (if (== $rows ()) $count
     (let* ((($row $rest) (decons-atom $rows))
            ($moved (llm-ledger:recover-call $space $row $now)))
       (llm-ledger:recover-each $space $rest $now (if $moved (+ $count 1) $count)))))

(= (llm-ledger:terminal-status $status)
   (or (== $status "completed") (or (== $status "failed") (== $status "unknown"))))

(= (llm-ledger:recover-call $space (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n) $now)
   (let $request (llm-ledger:request-status $space $i)
     (if (and (llm-ledger:terminal-status $request) (>= (- $now $u) 2))
         (let ($outcome $reason)
              (if (== $pu "compaction") ("abandoned" "compaction-publication-not-completed")
                (if (== $request "completed") ("completed" "request-completed-before-lifetime-settlement")
                  (if (and (== $request "failed") (== $st "claimed")) ("failed" "request-failed-before-submission")
                    ("uncertain" "terminal-request-remote-outcome-unknown"))))
           (let* (($_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                                                   (llm:call $i $e $pid $s $c $now $outcome $g $t $so $b $q $reason $a $l $pu $n)))
                  ($_ (if (== $outcome "uncertain")
                          (let* (($_ (llm-ledger:request-unknown $space $i "transport-unknown" False)))
                            (llm-ledger:event-in $space $i "unknown" $now (llm-ledger:reason-detail $reason)))
                          done)))
             True))
         (if (if (== $s none) False (== (process:birth $pid) $s))
             False
             (let* (($outcome (if (== $pu "compaction") "abandoned" "uncertain"))
                    ($_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                          (llm:call $i $e $pid $s $c $now $outcome $g $t $so $b $q "process-interrupted-remote-outcome-unknown" $a $l $pu $n)))
                    ($_ (llm-ledger:request-unknown $space $i "interrupted-unknown" True))
                    ($_ (llm-ledger:event-in $space $i "unknown" $now (llm-ledger:reason-detail "process-interrupted"))))
               True)))))

; UPDATE requests SET status='unknown',output=NULL,finish=<finish> WHERE id=? [AND status='pending']
(= (llm-ledger:request-unknown $space $id $finish $only-pending)
   (case (llm-ledger:request-of $space $id)
     ((((llm:request $i $st $rv $s $o $p $m $f $ow $fp))
       (if (or (not $only-pending) (== $s "pending"))
           (input-ledger:replace $space (llm:request $i $st $rv $s $o $p $m $f $ow $fp)
                                        (llm:request $i $st $rv "unknown" none $p $m $finish $ow $fp))
           done))
      ($other done))))

; nl:settle-statements: answers True when the call moved. $purpose is the caller's (the plan's).
(= (llm-ledger:settle-in $space $id $epoch $purpose $state $result $reason $soft $now)
   (case (llm-ledger:active-call $space $id $epoch)
     ((() False)
      (((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
       (let* (($abandon (and (== $purpose "compaction") (or (== $state "uncertain") (== $state "late"))))
              ($stored (if $abandon "abandoned" $state))
              ($kept (if $abandon none $result))
              ($_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                    (llm:call $i $e $pid $s $c $now $stored $g $t (if (> $soft $so) $soft $so) $b $q $reason $a $l $pu $n)))
              ($_ (llm-ledger:result-put $space $i $kept))
              ($_ (if (== $state "uncertain")
                      (let* (($_ (llm-ledger:request-unknown $space $i "transport-unknown" False)))
                        (llm-ledger:event-in $space $i "unknown" $now (llm-ledger:reason-detail "transport-unknown")))
                      done)))
         True)))))

; ---- the transitions ----

; lp:admit-configured / admit: recovery of dead owners, then the cooldown test of a managed
; provider, then the request and its admitted event.
(= (llm-ledger:admit-in $space $ticket $provider $maximum $fingerprint $profile $mode $owner $detail $now)
   (let* (($_ (llm-ledger:recover-requests-in $space $now))
          ($until (llm-ledger:cooldown-of $space $provider)))
     (if (llm-ledger:cooling $profile $until $now)
         (LedgerDenied "provider-cooldown" (- $until $now))
         (if (not (== (llm-ledger:request-of $space $ticket) ()))
             (LedgerError (PythonException IntegrityError "UNIQUE constraint failed: requests.id"))
             (let* (($_ (add-atom $space (llm:request $ticket $now $maximum "pending" none $provider $mode none $owner $fingerprint)))
                    ($_ (llm-ledger:event-in $space $ticket "admitted" $now $detail)))
               (LedgerAdmitted $ticket))))))

(= (llm-ledger:cooling $profile $until $now)
   (if (and (== $profile "managed") (not (== $until none))) (> $until $now) False))

; describe: the request event, when the request exists.
(= (llm-ledger:describe-in $space $ticket $detail $now)
   (if (== (llm-ledger:request-of $space $ticket) ())
       (LedgerChanged False)
       (LedgerChanged (llm-ledger:event-in $space $ticket "request" $now $detail))))

; llm_policy.submitted (the Python transport): the submitted event of a pending request.
(= (llm-ledger:request-submitted-in $space $ticket $now)
   (if (== (llm-ledger:request-status $space $ticket) "pending")
       (LedgerChanged (llm-ledger:event-in $space $ticket "submitted" $now "{}"))
       (LedgerChanged False)))

; nl:claim / native_lifetime.claim: recovery first, then the caller's validation result, then
; eligibility against the active calls, then the purpose test (refused only when eligible).
(= (llm-ledger:eligible $space $unscoped $textual $activity $lane $capacity $reserve)
   (let $active (llm-ledger:active-calls $space)
     (if $unscoped
         (== $active ())
         (llm-ledger:scoped-eligible $active $textual $activity $lane $capacity $reserve))))

(= (llm-ledger:scoped-eligible $active $textual $activity $lane $capacity $reserve)
   (let* (($blocked (collapse (let $row (superpose $active)
                                (case $row
                                  (((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                                    (if (or (== $a "") (and $textual (== $a $activity))) $i (empty))))))))
          ($background (collapse (let $row (superpose $active)
                                   (case $row
                                     (((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                                       (if (== $l "background") $i (empty)))))))))
     (and (== $blocked ())
          (and (< (size-atom $active) $capacity)
               (or (not (== $lane "background")) (< (size-atom $background) (- $capacity $reserve)))))))

(= (llm-ledger:valid-purpose $purpose)
   (or (== $purpose "turn") (or (== $purpose "coding") (== $purpose "compaction"))))

(= (llm-ledger:claim-in $space $id $epoch $pid $start $guard $activity $lane $purpose $unscoped $textual
                        $capacity $reserve $check $detail $now)
   (let $_ (llm-ledger:recover-calls-in $space $now)
     (if (not (== $check ok))
         (LedgerClaimRefused $check)
         (if (not (llm-ledger:eligible $space $unscoped $textual $activity $lane $capacity $reserve))
             (LedgerChanged False)
             (if (not (llm-ledger:valid-purpose $purpose))
                 (LedgerClaimRefused invalid-purpose)
                 (if (not (== (llm-ledger:call-of $space $id) ()))
                     (LedgerError (PythonException IntegrityError "UNIQUE constraint failed: native_calls.id"))
                     (let* (($_ (add-atom $space (llm:call $id $epoch $pid $start $now $now "claimed" $guard none 0 0 0
                                                           none $activity $lane $purpose 0)))
                            ($_ (if (== $purpose "turn") (llm-ledger:recent-insert $space $id $now) done))
                            ($_ (llm-ledger:event-in $space $id "claimed" $now $detail)))
                       (LedgerChanged True))))))))

; nl:submitted / native_lifetime.submitted
(= (llm-ledger:submitted-in $space $id $epoch $transport $now)
   (case (llm-ledger:active-call $space $id $epoch)
     ((((llm:call $i $e $pid $s $c $u "claimed" $g $t $so $b $q $r $a $l $pu $n))
       (let* (($_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u "claimed" $g $t $so $b $q $r $a $l $pu $n)
                    (llm:call $i $e $pid $s $c $now "submitted" $g $transport $so $b $q $r $a $l $pu $n)))
              ($_ (llm-ledger:event-in $space $i "submitted" $now "{}")))
         (LedgerChanged True)))
      ($other (LedgerChanged False)))))

; nl:progress / native_lifetime.progress
(= (llm-ledger:progress-in $space $id $epoch $bytes $quiet $soft $now)
   (case (llm-ledger:active-call $space $id $epoch)
     ((((llm:call $i $e $pid $s $c $u "submitted" $g $t $so $b $q $r $a $l $pu $n))
       (let $_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u "submitted" $g $t $so $b $q $r $a $l $pu $n)
                 (llm:call $i $e $pid $s $c $now "submitted" $g $t $soft $bytes $quiet $r $a $l $pu $n))
         (LedgerChanged True)))
      ($other (LedgerChanged False)))))

; lp:complete-owned (+ nl:settle-statements when the call has an epoch): usage, the request,
; its completed event and the call's settlement in one transaction. Usage is recorded when
; the call was admitted and (without an epoch, or) this epoch owns or owned the call.
(= (llm-ledger:complete-request $space $ticket $epoch $output $finish)
   (case (llm-ledger:request-of $space $ticket)
     ((((llm:request $i $st $rv "pending" $o $p $m $f $ow $fp))
       (if (or (== $epoch none) (not (== (llm-ledger:active-call $space $ticket $epoch) ())))
           (let $_ (input-ledger:replace $space (llm:request $i $st $rv "pending" $o $p $m $f $ow $fp)
                                                (llm:request $i $st $rv "completed" $output $p $m $finish $ow $fp))
             True)
           False))
      ($other False))))

(= (llm-ledger:complete-in $space $ticket $epoch $evidence $output $finish $completed $claimed
                           $canonical $soft $purpose $now)
   (let* (($owned (or (== $epoch none)
                      (or (llm-ledger:has-call-epoch $space $ticket $epoch)
                          (== (llm-ledger:event-detail $space $ticket "claimed") $claimed))))
          ($_ (if (and $owned (llm-ledger:has-event $space $ticket "admitted"))
                  (llm-ledger:event-in $space $ticket "usage" $now $evidence)
                  False))
          ($flag (llm-ledger:complete-request $space $ticket $epoch $output $finish))
          ($_ (if $flag (llm-ledger:event-in $space $ticket "completed" $now $completed) False))
          ($_ (if (and $flag (not (== $epoch none)))
                  (llm-ledger:settle-in $space $ticket $epoch $purpose "completed" $canonical none $soft $now)
                  False)))
     (LedgerChanged $flag)))

; lp:fail-owned (+ nl:settle-statements when the call has an epoch): the request fails, its
; failed event, a managed provider's cooldown and the call's settlement in one transaction.
; $mode is the pending request's mode the caller read for its policy; $until is the
; cooldown the caller computed from that policy, or none.
(= (llm-ledger:fail-request $space $ticket $epoch $mode $output)
   (case (llm-ledger:request-of $space $ticket)
     ((((llm:request $i $st $rv "pending" $o $p $m $f $ow $fp))
       (if (and (== $m $mode)
                (or (== $epoch none)
                    (or (== (llm-ledger:call-of $space $ticket) ()) (llm-ledger:has-call-epoch $space $ticket $epoch))))
           (let $_ (input-ledger:replace $space (llm:request $i $st $rv "pending" $o $p $m $f $ow $fp)
                                                (llm:request $i $st $rv "failed" $output $p $m $f $ow $fp))
             $p)
           none))
      ($other none))))

(= (llm-ledger:fail-in $space $ticket $epoch $mode $output $failed $until $state $reason $soft $purpose $now)
   (let* (($provider (llm-ledger:fail-request $space $ticket $epoch $mode $output))
          ($flag (not (== $provider none)))
          ($_ (if $flag (llm-ledger:event-in $space $ticket "failed" $now $failed) False))
          ($_ (if (and $flag (not (== $until none))) (llm-ledger:cooldown-raise $space $provider $until) done))
          ($_ (if (and $flag (not (== $epoch none)))
                  (llm-ledger:settle-in $space $ticket $epoch $purpose $state none $reason $soft $now)
                  False)))
     (LedgerChanged $flag)))

; nl:command-output-rejected / native_lifetime.command_output_rejected
(= (llm-ledger:rejected-in $space $id $epoch $code)
   (case (llm-ledger:call-of $space $id)
     ((((llm:call $i $e $pid $s $c $u "completed" $g $t $so $b $q $r $a $l "turn" $n))
       (if (== $e $epoch)
           (let $_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u "completed" $g $t $so $b $q $r $a $l "turn" $n)
                     (llm:call $i $e $pid $s $c $u "completed" $g $t $so $b $q (str:concat "command-output:" $code) $a $l "turn" $n))
             (LedgerChanged True))
           (LedgerChanged False)))
      ($other (LedgerChanged False)))))

; native_lifetime.notify's mark: UPDATE native_calls SET notified=1 WHERE id=?
(= (llm-ledger:notified-in $space $id)
   (case (llm-ledger:call-of $space $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu 0))
       (let $_ (input-ledger:replace $space (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu 0)
                                            (llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu 1))
         (LedgerChanged True)))
      ($other (LedgerChanged False)))))

; ---- the reads ----

; nl:completion-state: committed when this epoch's call and its request completed with exactly
; this result, pending while both are still open, else conflict.
(= (llm-ledger:completion-state-of $view $id $epoch $canonical)
   (case (llm-ledger:call-of $view $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
       (if (not (== $e $epoch)) (LedgerCompletion conflict)
         (let $request (llm-ledger:request-status $view $i)
           (if (and (== $request "completed") (and (== $st "completed") (== (llm-ledger:result-of $view $i) $canonical)))
               (LedgerCompletion committed)
               (if (and (== $request "pending") (llm-ledger:active-state $st))
                   (LedgerCompletion pending)
                   (LedgerCompletion conflict))))))
      ($other (LedgerCompletion conflict)))))

; SELECT mode FROM requests WHERE id=? AND status='pending'
(= (llm-ledger:pending-mode-of $view $ticket)
   (case (llm-ledger:request-of $view $ticket)
     ((((llm:request $i $st $rv "pending" $o $p $m $f $ow $fp)) $m)
      ($other none))))

; status(): the 12 newest turn calls (id state created updated soft_elapsed bytes quiet_ms reason).
(= (llm-ledger:status-of $view)
   (llm-ledger:recent-walk $view (llm-ledger:state-of $view "recent-turn" none) 12))

(= (llm-ledger:recent-walk $view $id $left)
   (if (or (== $id none) (<= $left 0)) ()
     (case (collapse (match $view (llm:recent $id $older $created) $older))
       ((($older)
         (case (llm-ledger:call-of $view $id)
           ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
             (cons-atom ($i $st $c $u $so $b $q $r) (llm-ledger:recent-walk $view $older (- $left 1))))
            ($other (llm-ledger:recent-walk $view $older $left)))))
        ($other ())))))

; notify()'s selection: turn calls late or uncertain and not yet notified, oldest first: (id state).
; (superpose takes its list as written: a list computed by a call is bound first.)
(= (llm-ledger:notices-of $view)
   (let* (($rows (llm-ledger:append (llm-ledger:calls-in-state $view "late") (llm-ledger:calls-in-state $view "uncertain")))
          ($entries (collapse (let $row (superpose $rows)
                                (case $row
                                  (((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l "turn" 0) ($c ($i $st)))
                                   ($other (empty))))))))
     (map-atom (sort-atom $entries) $entry (input-ledger:second $entry))))

; result(): (state result reason purpose) of a call, or none.
(= (llm-ledger:result-row-of $view $id)
   (case (llm-ledger:call-of $view $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
       ($st (llm-ledger:result-of $view $i) $r $pu))
      ($other none))))

; activity_model_host.finish: (state reason) of the call (id, epoch), or none.
(= (llm-ledger:call-state-of $view $id $epoch)
   (case (llm-ledger:call-of $view $id)
     ((((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n))
       (if (== $e $epoch) ($st $r) none))
      ($other none))))

; usage_history.report's inputs over admission time [since, until): the recording start,
; the request rows without an admitted event, and every event of the calls admitted in the
; window as (call event detail admitted), in history order.
; A variable bound by a case pattern is bound for the rest of the rule, so each clause below
; names its own.
(= (llm-ledger:usage-of $view $since $until)
   (let* (($started (case (collapse (match $view (llm:event $sq "" "recording-started" $so $sd) $so))
                      ((($first) $first) ($none none))))
          ($legacy (size-atom (collapse (match $view (llm:request $id $st $rv $s $ro $p $m $f $ow $fp)
                                          (if (and (>= $st $since) (and (< $st $until) (not (llm-ledger:has-event $view $id "admitted"))))
                                              $id (empty))))))
          ($admitted (collapse (match $view (llm:event $aq $ac "admitted" $ao $ad)
                                 (if (and (>= $ao $since) (< $ao $until)) ($ac $ao) (empty)))))
          ($rows (collapse (let ($call $at) (superpose $admitted)
                             (match $view (llm:event $eq $call $event $eo $ed) ($eq ($call $event $ed $at)))))))
     (usage $started $legacy (map-atom (sort-atom $rows) $entry (input-ledger:second $entry)))))

; snapshot(): (requests ((status count) ...) <accounted output tokens>)
(= (llm-ledger:requests-of $view)
   (let $rows (collapse (match $view (llm:request $id $st $rv $s $o $p $m $f $ow $fp) ($s (if (== $o none) $rv $o))))
     (requests (llm-ledger:status-counts $rows ()) (llm-ledger:output-sum $rows 0))))

(= (llm-ledger:status-counts $rows $counts)
   (if (== $rows ()) $counts
     (let* ((($row $rest) (decons-atom $rows)) (($status $output) $row))
       (llm-ledger:status-counts $rest (llm-ledger:count-in $counts $status)))))

(= (llm-ledger:count-in $counts $status)
   (if (== $counts ()) (($status 1))
     (let* ((($first $rest) (decons-atom $counts)) (($name $n) $first))
       (if (== $name $status) (cons-atom ($name (+ $n 1)) $rest)
         (cons-atom $first (llm-ledger:count-in $rest $status))))))

(= (llm-ledger:output-sum $rows $sum)
   (if (== $rows ()) $sum
     (let* ((($row $rest) (decons-atom $rows)) (($status $output) $row))
       (llm-ledger:output-sum $rest (+ $sum $output)))))

; unknown_context(): the 5 newest unknown requests by start, (id provider mode).
(= (llm-ledger:unknown-of $view)
   (let $rows (collapse (match $view (llm:request $id $st $rv "unknown" $o $p $m $f $ow $fp) ($st ($id $p $m))))
     (list:take 5 (list:reverse (map-atom (sort-atom $rows) $entry (input-ledger:second $entry))))))

; scripts/inspect_retry_wake.metta: the active calls with their request status.
(= (llm-ledger:active-of $view)
   (let $rows (llm-ledger:active-calls $view)
     (collapse (let $row (superpose $rows)
               (case $row
                 (((llm:call $i $e $pid $s $c $u $st $g $t $so $b $q $r $a $l $pu $n)
                   ($i $e $pid $s $st $u (llm-ledger:request-status $view $i) $pu))))))))

; ---- migration (scripts/migrate_llm_ledger.py) ----

; (census <requests> <cooldowns> <calls> <results> <events> <recent> <recoveries> <states> <all>), from one snapshot.
(= (llm-ledger:census-of $view)
   (census (size-atom (collapse (match $view (llm:request $a $b $c $d $e $f $g $h $i $j) $a)))
           (size-atom (collapse (match $view (llm:cooldown $a $b) $a)))
           (size-atom (collapse (match $view (llm:call $a $b $c $d $e $f $g $h $i $j $k $l $m $n $o $p $q) $a)))
           (size-atom (collapse (match $view (llm:result $a $b) $a)))
           (size-atom (collapse (match $view (llm:event $a $b $c $d $e) $a)))
           (size-atom (collapse (match $view (llm:recent $a $b $c) $a)))
           (size-atom (collapse (match $view (llm:recovery $a $b $c $d) $a)))
           (size-atom (collapse (match $view (llm:state $a $b) $a)))
           (size-atom (collapse (match $view $atom $atom)))))

; Every record of one kind, for the reader check and the reverse export: requests, cooldowns,
; calls, results, events, recent, recoveries or states.
(= (llm-ledger:records-of $view $kind)
   (case $kind
     (("requests" (collapse (match $view (llm:request $a $b $c $d $e $f $g $h $i $j) (llm:request $a $b $c $d $e $f $g $h $i $j))))
      ("cooldowns" (collapse (match $view (llm:cooldown $a $b) (llm:cooldown $a $b))))
      ("calls" (collapse (match $view (llm:call $a $b $c $d $e $f $g $h $i $j $k $l $m $n $o $p $q)
                           (llm:call $a $b $c $d $e $f $g $h $i $j $k $l $m $n $o $p $q))))
      ("results" (collapse (match $view (llm:result $a $b) (llm:result $a $b))))
      ("events" (collapse (match $view (llm:event $a $b $c $d $e) (llm:event $a $b $c $d $e))))
      ("recent" (collapse (match $view (llm:recent $a $b $c) (llm:recent $a $b $c))))
      ("recoveries" (collapse (match $view (llm:recovery $a $b $c $d) (llm:recovery $a $b $c $d))))
      ("states" (collapse (match $view (llm:state $a $b) (llm:state $a $b))))
      ($other (Error llm-ledger:records (unknown-kind $other))))))

; An import adds whole records. The first batch ($phase begin) needs a collection without any
; atom and marks it (llm-ledger-importing <source>): every verb then answers an Error until the
; last batch ($phase finish) replaces that mark with (llm-ledger-v1 <source> <at>), and only if
; the census then equals $expect. Other batches ($phase more) need the importing mark.
(= (llm-ledger:import-in $space $phase $source $rows $expect $at)
   (let $mark (collapse (match $space (llm:state "selected" $value) $value))
     (case $phase
       ((begin (if (== (collapse (match $space $atom $atom)) ())
                   (let* (($_ (add-atom $space (llm:state "selected" (llm-ledger-importing $source))))
                          ($_ (input-ledger:add-all $space $rows)))
                     imported)
                   refused))
        (more (if (== $mark ((llm-ledger-importing $source)))
                  (let $_ (input-ledger:add-all $space $rows) imported)
                  refused))
        (finish (if (== $mark ((llm-ledger-importing $source)))
                    (let* (($_ (input-ledger:add-all $space $rows))
                           ($census (llm-ledger:census-of $space)))
                      (if (== $census $expect)
                          (let $_ (input-ledger:replace $space (llm:state "selected" (llm-ledger-importing $source))
                                                               (llm:state "selected" (llm-ledger-v1 $source $at)))
                            imported)
                          (mismatch $census)))
                    refused))
        ($other refused)))))

; An import that stopped half way is discarded whole: every record goes, the importing mark
; with them, and the collection is empty (legacy) again.
(= (llm-ledger:discard-in $space)
   (case (collapse (match $space (llm:state "selected" $value) $value))
     ((((llm-ledger-importing $source))
       (let* (($all (collapse (match $space $atom $atom)))
              ($gone (collapse (let $atom (superpose $all) (remove-atom $space $atom)))))
         discarded))
      ($other refused))))

; A rollback to the SQLite file (after scripts/migrate_llm_ledger.py --export): callers answer
; from SQLite again; the records stay as evidence.
(= (llm-ledger:rollback-in $space $why)
   (case (collapse (match $space (llm:state "selected" $value) $value))
     ((((llm-ledger-v1 $source $at))
       (input-ledger:replace $space (llm:state "selected" (llm-ledger-v1 $source $at))
                                    (llm:state "selected" (llm-ledger-legacy $why))))
      ($other refused))))

; ---- the verbs ----

(= (llm-ledger:write $db $verb $fields $request-id $body)
   (llm-ledger:settle $db $verb
     (input-ledger:request-digest $verb (str:sha256 (str:concat (input-ledger:field $verb) (input-ledger:fields $fields))))
     $request-id $body (input-ledger:attempts) none))

(= (llm-ledger:settle $db $verb $operation $request-id $body $left $prior)
   (case (db:transaction-result $db provider $request-id $operation $body)
     (((db:committed $r $values) (superpose $values))
      ((db:replayed $r $values) (superpose $values))
      ((db:aborted $r) (empty))
      ((Error db:transaction (StoreRefused conflict))
       (if (> $left 1) (llm-ledger:settle $db $verb $operation $request-id $body (- $left 1) $prior)
           (input-ledger:remember $prior (Error db:transaction (StoreRefused conflict)))))
      ((db:uncertain $r $c $o)
       (if (> $left 1) (llm-ledger:settle $db $verb $operation $request-id $body (- $left 1) (db:uncertain $r $c $o))
           (Error $verb (db:uncertain $r $c $o))))
      ((Error $where $why) (input-ledger:remember $prior (Error $where $why)))
      ($other (Error $verb $other)))))

; A write reads the selection first: a legacy store answers llm-legacy and writes nothing, an
; unsupported one answers its Error. The transaction checks it again (an import or a rollback
; in between answers an Error, unpublished). A native body always answers a value, so an
; empty outcome is a failure, never legacy.
(= (llm-ledger:selected-write $db $verb $fields $request-id $body)
   (case (llm-ledger:selection-read $db)
     ((native
       (let $outcome (collapse (llm-ledger:write $db $verb $fields $request-id
                                 (|-> ($space) (llm-ledger:still-native $space $body))))
         (case $outcome
           ((($answer) $answer)
            ($other (Error $verb (answers $other)))))))
      (legacy llm-legacy)
      ($failure $failure))))

(= (llm-ledger:still-native $space $body)
   (case (llm-ledger:selection $space)
     ((native ($body $space))
      ($other (Error llm-ledger:selection (changed $other))))))

; A write whose body changed nothing (it answers 0) gives up, so no receipt records it: the
; verb then answers $unchanged.
(= (llm-ledger:quiet-write $db $verb $fields $request-id $unchanged $body)
   (case (llm-ledger:selection-read $db)
     ((native
       (let $outcome (collapse (llm-ledger:write $db $verb $fields $request-id
                                 (|-> ($space) (llm-ledger:still-native $space
                                                 (|-> ($s) (case ($body $s) ((0 (empty)) ($count $count))))))))
         (case $outcome
           ((() $unchanged)
            (($answer) $answer)
            ($other (Error $verb (answers $other)))))))
      (legacy llm-legacy)
      ($failure $failure))))

(= (llm-ledger:read $db $body)
   (case (db:snapshot $db provider)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer (case (llm-ledger:selection $view)
                               ((native ($body $view))
                                (legacy llm-legacy)
                                ($failure $failure))))
                    ($_ (db:close $view)))
               $answer)))))

(= (llm-ledger:selection-read $db)
   (case (db:snapshot $db provider)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer (llm-ledger:selection $view)) ($_ (db:close $view))) $answer)))))

(= (llm-ledger:pageable $verb) (or (== $verb "llm-usage") (== $verb "llm-records")))

(= (llm-ledger:perform $db $verb $arguments)
   (case ($verb $arguments)
     ((("llm-selection" ()) (llm-ledger:selection-read $db))
      (("llm-admit" ($ticket $provider $maximum $fingerprint $profile $mode $owner $detail $now $rq))
       (llm-ledger:selected-write $db llm-admit ($ticket $provider $maximum $fingerprint $profile $mode $owner $detail $now) $rq
         (|-> ($space) (llm-ledger:admit-in $space $ticket $provider $maximum $fingerprint $profile $mode $owner $detail $now))))
      (("llm-describe" ($ticket $detail $now $rq))
       (llm-ledger:selected-write $db llm-describe ($ticket $detail $now) $rq
         (|-> ($space) (llm-ledger:describe-in $space $ticket $detail $now))))
      (("llm-request-submitted" ($ticket $now $rq))
       (llm-ledger:selected-write $db llm-request-submitted ($ticket $now) $rq
         (|-> ($space) (llm-ledger:request-submitted-in $space $ticket $now))))
      (("llm-claim" ($id $epoch $pid $start $guard $activity $lane $purpose $unscoped $textual $capacity $reserve $check $detail $now $rq))
       (llm-ledger:selected-write $db llm-claim
         ($id $epoch $pid $start $guard $activity $lane $purpose $unscoped $textual $capacity $reserve $check $detail $now) $rq
         (|-> ($space) (llm-ledger:claim-in $space $id $epoch $pid $start $guard $activity $lane $purpose $unscoped $textual
                                            $capacity $reserve $check $detail $now))))
      (("llm-recover" ($now $rq))
       (llm-ledger:quiet-write $db llm-recover ($now) $rq 0
         (|-> ($space) (llm-ledger:recover-calls-in $space $now))))
      (("llm-recover-requests" ($now $rq))
       (llm-ledger:quiet-write $db llm-recover-requests ($now) $rq 0
         (|-> ($space) (llm-ledger:recover-requests-in $space $now))))
      (("llm-submitted" ($id $epoch $transport $now $rq))
       (llm-ledger:selected-write $db llm-submitted ($id $epoch $transport $now) $rq
         (|-> ($space) (llm-ledger:submitted-in $space $id $epoch $transport $now))))
      (("llm-progress" ($id $epoch $bytes $quiet $soft $now $rq))
       (llm-ledger:selected-write $db llm-progress ($id $epoch $bytes $quiet $soft $now) $rq
         (|-> ($space) (llm-ledger:progress-in $space $id $epoch $bytes $quiet $soft $now))))
      (("llm-settle" ($id $epoch $purpose $state $result $reason $soft $now $rq))
       (llm-ledger:selected-write $db llm-settle ($id $epoch $purpose $state $result $reason $soft $now) $rq
         (|-> ($space) (LedgerChanged (llm-ledger:settle-in $space $id $epoch $purpose $state $result $reason $soft $now)))))
      (("llm-complete" ($ticket $epoch $evidence $output $finish $completed $claimed $canonical $soft $purpose $now $rq))
       (llm-ledger:selected-write $db llm-complete
         ($ticket $epoch $evidence $output $finish $completed $claimed $canonical $soft $purpose $now) $rq
         (|-> ($space) (llm-ledger:complete-in $space $ticket $epoch $evidence $output $finish $completed $claimed
                                               $canonical $soft $purpose $now))))
      (("llm-fail" ($ticket $epoch $mode $output $failed $until $state $reason $soft $purpose $now $rq))
       (llm-ledger:selected-write $db llm-fail ($ticket $epoch $mode $output $failed $until $state $reason $soft $purpose $now) $rq
         (|-> ($space) (llm-ledger:fail-in $space $ticket $epoch $mode $output $failed $until $state $reason $soft $purpose $now))))
      (("llm-rejected" ($id $epoch $code $rq))
       (llm-ledger:selected-write $db llm-rejected ($id $epoch $code) $rq
         (|-> ($space) (llm-ledger:rejected-in $space $id $epoch $code))))
      (("llm-notified" ($id $rq))
       (llm-ledger:selected-write $db llm-notified ($id) $rq (|-> ($space) (llm-ledger:notified-in $space $id))))
      (("llm-pending-mode" ($ticket)) (llm-ledger:read $db (|-> ($view) (llm-ledger:pending-mode-of $view $ticket))))
      (("llm-completion-state" ($id $epoch $canonical))
       (llm-ledger:read $db (|-> ($view) (llm-ledger:completion-state-of $view $id $epoch $canonical))))
      (("llm-status" ()) (llm-ledger:read $db (|-> ($view) (llm-ledger:status-of $view))))
      (("llm-notices" ()) (llm-ledger:read $db (|-> ($view) (llm-ledger:notices-of $view))))
      (("llm-result" ($id)) (llm-ledger:read $db (|-> ($view) (llm-ledger:result-row-of $view $id))))
      (("llm-call-state" ($id $epoch)) (llm-ledger:read $db (|-> ($view) (llm-ledger:call-state-of $view $id $epoch))))
      (("llm-usage" ($since $until)) (llm-ledger:read $db (|-> ($view) (llm-ledger:usage-of $view $since $until))))
      (("llm-requests" ()) (llm-ledger:read $db (|-> ($view) (llm-ledger:requests-of $view))))
      (("llm-unknown" ()) (llm-ledger:read $db (|-> ($view) (llm-ledger:unknown-of $view))))
      (("llm-active" ()) (llm-ledger:read $db (|-> ($view) (llm-ledger:active-of $view))))
      ; migration and its checks read whatever the selection is
      (("llm-census" ())
       (case (db:snapshot $db provider)
         (((Error $where $why) (Error $where $why))
          ($view (let* (($answer (llm-ledger:census-of $view)) ($_ (db:close $view))) $answer)))))
      (("llm-records" ($kind))
       (case (db:snapshot $db provider)
         (((Error $where $why) (Error $where $why))
          ($view (let* (($answer (llm-ledger:records-of $view $kind)) ($_ (db:close $view))) $answer)))))
      (("llm-import" ($phase $source $rows $expect $at $rq))
       (let $outcome (collapse (llm-ledger:write $db llm-import ($phase $source $rows $expect $at) $rq
                                 (|-> ($space) (case (llm-ledger:import-in $space $phase $source $rows $expect $at)
                                                 ((imported imported) ($refusal (empty)))))))
         (case $outcome ((() refused) (($answer) $answer) ($other (Error llm-import (answers $other)))))))
      (("llm-discard" ($rq))
       (let $outcome (collapse (llm-ledger:write $db llm-discard () $rq
                                 (|-> ($space) (case (llm-ledger:discard-in $space) ((refused (empty)) ($done discarded))))))
         (case $outcome ((() refused) (($answer) $answer) ($other (Error llm-discard (answers $other)))))))
      (("llm-rollback" ($why $rq))
       (let $outcome (collapse (llm-ledger:write $db llm-rollback ($why) $rq
                                 (|-> ($space) (case (llm-ledger:rollback-in $space $why) ((refused (empty)) ($done rolled-back))))))
         (case $outcome ((() refused) (($answer) $answer) ($other (Error llm-rollback (answers $other)))))))
      ($other (Error input-ledger:run (unknown-request $other))))))
