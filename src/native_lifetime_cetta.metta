; Native ownership/settlement over the existing provider ledger. OS facts
; are observed before a transaction; guarded updates recheck the DB facts.
!(import! &self ./llm_policy_cetta.metta)

(: nl:field (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:field $metadata $key $default) (lp:field $metadata $key $default))
; Retention is explicit maintenance; ordinary calls never erase old receipts.
(: nl:prune (-> %Undefined% %Undefined%))
(= (nl:prune $now) ())
(: nl:transaction (-> %Undefined% %Undefined% %Undefined%))
(= (nl:transaction $statements $now) (lp:transaction $statements))
(: nl:ascii-id (-> %Undefined% %Undefined%))
(= (nl:ascii-id $text)
   (if (ue:equal $text "") True
     (let $first (str:char-slice $text 0 1)
       (case (str:find "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" $first)
         (((TextNotFound $ignored0) False)
          ($found (nl:ascii-id (str:char-slice $text 1 (str:char-length $text)))))))))
(: nl:valid-activity (-> %Undefined% %Undefined% %Undefined%))
; Python matches the regex against str(activity).
(= (nl:valid-activity $activity $lane)
   (let $text (lp:python-str $activity)
     (and (> (str:char-length $text) 0)
       (and (<= (str:char-length $text) 64)
         (and (or (ue:equal $lane "interactive") (ue:equal $lane "background")) (nl:ascii-id $text))))))
(: nl:capacity (-> %Undefined%))
(= (nl:capacity)
   (case (lp:python-int (lp:env "METTACLAW_ACTIVITY_CALL_CAPACITY" "2"))
     (((PythonException $class $message) (lp:python-error $class $message))
      ($capacity
        (case (lp:python-int (lp:env "METTACLAW_ACTIVITY_INTERACTIVE_RESERVE" "1"))
          (((PythonException $class $message) (lp:python-error $class $message))
           ($reserve
             (if (and (>= $reserve 1) (< $reserve $capacity))
               (NativeCapacity $capacity $reserve)
               (lp:python-error ValueError "invalid activity call capacity")))))))))

; The flag survives later UPDATEs whose own changes() value may be zero.
(: nl:recover-one (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:recover-one $row $outcome $reason $terminal $now)
   (let ($identity $pid $start $state $updated $request $purpose $epoch) $row
     (lp:append
       ((sqlite:statement "UPDATE native_calls SET state=?,reason=?,updated=? WHERE id=? AND epoch=? AND state=? AND updated=? AND pid=? AND process_start IS ? AND (SELECT status FROM requests WHERE id=?) IS ?"
          ($outcome $reason $now $identity $epoch $state $updated $pid $start $identity $request))
        (sqlite:statement "UPDATE nl_recovery SET changed=changes()" ()))
       (if $terminal
         (if (ue:equal $outcome "uncertain")
           ((sqlite:statement "UPDATE requests SET status='unknown',output=NULL,finish='transport-unknown' WHERE id=? AND (SELECT changed FROM nl_recovery)=1" ($identity))
            (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'unknown',?,? WHERE (SELECT changed FROM nl_recovery)=1"
              ($identity $now (lp:detail (JsonObject ((JsonPair "reason" $reason))))))) ())
         ((sqlite:statement "UPDATE requests SET status='unknown',output=NULL,finish='interrupted-unknown' WHERE id=? AND status='pending' AND (SELECT changed FROM nl_recovery)=1" ($identity))
          (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'unknown',?,? WHERE (SELECT changed FROM nl_recovery)=1"
            ($identity $now "{\"reason\":\"process-interrupted\"}")))))))
(: nl:recovery (-> %Undefined% %Undefined% %Undefined%))
; Python evaluates now - float(updated) >= 2 only for a terminal request. The
; float can raise, which rolls the whole recovery back; NaN compares false and
; an infinity by its sign, where CeTTa's arithmetic would overflow.
(: nl:due (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:due $request $updated $now)
   (if (or (ue:equal $request "completed") (or (ue:equal $request "failed") (ue:equal $request "unknown")))
     (case (lp:python-float $updated)
       (((PythonException $class $message) (PythonException $class $message))
        ($float
          (case (lp:number-kind $float)
            ((NumberNaN False)
             (NumberInf (< $float 0))
             ($finite (>= (- $now $float) 2)))))))
     False))
(= (nl:recovery $row $now)
   (let ($identity $pid $start $state $updated $request $purpose $epoch) $row
    (let $due (nl:due $request $updated $now)
     (case $due
      (((PythonException $class $message) $due)
       ($flag
     (if $flag
       (if (ue:equal $purpose "compaction")
         (nl:recover-one $row "abandoned" "compaction-publication-not-completed" True $now)
         (if (ue:equal $request "completed")
           (nl:recover-one $row "completed" "request-completed-before-lifetime-settlement" True $now)
           (if (and (ue:equal $request "failed") (ue:equal $state "claimed"))
             (nl:recover-one $row "failed" "request-failed-before-submission" True $now)
             (nl:recover-one $row "uncertain" "terminal-request-remote-outcome-unknown" True $now))))
       (if (and (not (ue:equal $start sqlite:null)) (ue:equal (lp:process-start $pid) $start)) ()
         (nl:recover-one $row (if (ue:equal $purpose "compaction") "abandoned" "uncertain")
                             "process-interrupted-remote-outcome-unknown" False $now)))))))))
(: nl:recover-statements (-> %Undefined% %Undefined% %Undefined%))
(= (nl:recover-statements $rows $now)
   (if (ue:equal $rows ()) ()
     (case (nl:recovery (car-atom $rows) $now)
       (((PythonException $class $message) (PythonException $class $message))
        ($statements
          (case (nl:recover-statements (cdr-atom $rows) $now)
            (((PythonException $class $message) (PythonException $class $message))
             ($rest (lp:append $statements $rest)))))))))
(: nl:recover (-> %Undefined% %Undefined%))
(= (nl:recover $now)
   (case (lp:init)
     (((sqlite:committed $ignored1)
       (case (lp:rows (sqlite:observe (lp:path)
          "SELECT n.id,n.pid,n.process_start,n.state,n.updated,r.status,n.purpose,n.epoch FROM native_calls n LEFT JOIN requests r ON r.id=n.id WHERE n.state IN ('claimed','submitted')" () 5000))
         (((LedgerError $error) (LedgerError $error))
          ($rows
            (case (nl:recover-statements $rows $now)
              (((PythonException $class $message) (lp:python-error $class $message))
               ($statements
                 (case (nl:transaction (lp:append
                         ((sqlite:statement "CREATE TEMP TABLE nl_recovery(changed INTEGER NOT NULL)" ())
                          (sqlite:statement "INSERT INTO nl_recovery VALUES(0)" ()))
                         $statements) $now)
                   (((sqlite:committed $results) (sqlite:committed $results))
                    ($failure (LedgerError (lp:failure $failure))))))))))))
      ($error $error))))

(: nl:claim-insert (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:claim-insert $metadata $activity $lane $capacity $reserve $now)
   (let* (($identity (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($purpose (nl:field $metadata "purpose" "turn")) ($guard (nl:field $metadata "guard" JsonNull))
          ($pid (system:pid)) ($start (lp:sql-null (lp:process-start $pid)))
          ; `not activity` takes the unscoped path; only a text activity can
          ; equal a stored one, as Python compares them.
          ($unscoped (if (lp:python-truthy $activity) 0 1))
          ($textual (if (ue:equal (get-type $activity) String) 1 0)))
     (case (lp:changed-result (nl:transaction
       ; Validate purpose only after the refusal checks, under the same lock.
       ; The constraint rolls the transaction back on an eligible bad purpose,
       ; where Python raises ValueError("invalid native call purpose").
       ((sqlite:statement "CREATE TEMP TABLE nl_claim(eligible INTEGER NOT NULL,valid INTEGER NOT NULL,changed INTEGER NOT NULL DEFAULT 0,CHECK(eligible=0 OR valid=1))" ())
        (sqlite:statement "INSERT INTO nl_claim(eligible,valid) SELECT (?=1 AND NOT EXISTS(SELECT 1 FROM native_calls WHERE state IN ('claimed','submitted'))) OR (?=0 AND NOT EXISTS(SELECT 1 FROM native_calls WHERE state IN ('claimed','submitted') AND (activity='' OR (?=1 AND activity=?))) AND (SELECT COUNT(*) FROM native_calls WHERE state IN ('claimed','submitted'))<? AND (?<>'background' OR (SELECT COUNT(*) FROM native_calls WHERE state IN ('claimed','submitted') AND lane='background')<?)),? IN ('turn','coding','compaction')"
          ($unscoped $unscoped $textual $activity $capacity $lane (- $capacity $reserve) $purpose))
        (sqlite:statement "INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,activity,lane,purpose) SELECT ?,?,?,?,?,?,'claimed',?,?,?,? WHERE (SELECT eligible FROM nl_claim)=1"
          ($identity $epoch $pid $start $now $now $guard $activity $lane $purpose))
        (sqlite:statement "UPDATE nl_claim SET changed=changes()" ())
        (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'claimed',?,? WHERE (SELECT changed FROM nl_claim)=1"
          ($identity $now (lp:detail (JsonObject ((JsonPair "epoch" $epoch))))))
        (sqlite:statement "SELECT changed FROM nl_claim" ())) $now))
       (((LedgerError (PythonException IntegrityError "CHECK constraint failed: eligible=0 OR valid=1"))
         (lp:python-error ValueError "invalid native call purpose"))
        ($answer $answer)))))
(: nl:claim-ready (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim-ready $metadata $now)
   (let* (($activity (nl:field $metadata "activity" "")) ($lane (nl:field $metadata "lane" ""))
          ($purpose (nl:field $metadata "purpose" "turn")))
       (if (not (lp:python-truthy $activity)) (nl:claim-insert $metadata $activity $lane 0 0 $now)
         (if (not (nl:valid-activity $activity $lane)) (lp:python-error ValueError "invalid native call activity")
           (case (nl:capacity)
             (((NativeCapacity $capacity $reserve) (nl:claim-insert $metadata $activity $lane $capacity $reserve $now))
              ($error $error)))))))
(: nl:claim (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim $metadata $now)
   (let $native (nl:claim-native $metadata $now)
     (if (lp:legacy $native)
       (case (nl:recover $now)
         (((sqlite:committed $ignored2) (nl:claim-ready $metadata $now)) ($error $error)))
       $native)))

; The native claim: recovery, then the validation nl:claim-ready makes (its result travels
; with the claim, so recovery commits even when it refuses), eligibility and the purpose.
(: nl:claim-native (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim-native $metadata $now)
   (if (ue:equal (lp:socket) "") llm-legacy
     (let* (($activity (nl:field $metadata "activity" "")) ($lane (nl:field $metadata "lane" ""))
            ($unscoped (not (lp:python-truthy $activity)))
            ($plan (if $unscoped (NativeCapacity 0 0)
                     (if (nl:valid-activity $activity $lane) (nl:capacity) invalid-activity))))
       (case $plan
         (((NativeCapacity $capacity $reserve) (nl:claim-call $metadata $activity $lane $unscoped $capacity $reserve ok $now))
          (invalid-activity (nl:claim-call $metadata $activity $lane $unscoped 0 0 invalid-activity $now))
          ((LedgerError $error) (nl:claim-call $metadata $activity $lane $unscoped 0 0 (capacity-error $error) $now)))))))
(: nl:claim-call (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:claim-call $metadata $activity $lane $unscoped $capacity $reserve $check $now)
   (let* (($identity (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($purpose (nl:field $metadata "purpose" "turn")) ($guard (lp:none (nl:field $metadata "guard" JsonNull)))
          ($pid (system:pid)) ($start (lp:none (lp:process-start $pid)))
          ($textual (ue:equal (get-type $activity) String))
          ($detail (lp:detail (JsonObject ((JsonPair "epoch" $epoch))))) ($request (lp:request-id)))
     (case (lp:native "llm-claim" ($identity $epoch $pid $start $guard $activity $lane $purpose $unscoped $textual
                                   $capacity $reserve $check $detail $now $request))
       (((LedgerClaimRefused invalid-activity) (lp:python-error ValueError "invalid native call activity"))
        ((LedgerClaimRefused invalid-purpose) (lp:python-error ValueError "invalid native call purpose"))
        ((LedgerClaimRefused (capacity-error $error)) (LedgerError $error))
        ($answer $answer)))))

; Python's submitted and progress store int(value) in these columns. It
; converts the arguments in order, and the first exception is the answer;
; then binding refuses any integer beyond SQLite's 64 bits. Both raise before
; any statement runs.
(: nl:python-ints (-> %Undefined% %Undefined%))
(= (nl:python-ints $values)
   (if (ue:equal $values ()) ()
     (case (lp:python-int (car-atom $values))
       (((PythonException $class $message) (PythonException $class $message))
        ($integer
          (case (nl:python-ints (cdr-atom $values))
            (((PythonException $class $message) (PythonException $class $message))
             ($rest (cons-atom $integer $rest)))))))))
(: nl:bindable (-> %Undefined% %Undefined%))
(= (nl:bindable $integers)
   (if (ue:equal $integers ()) True
     (let $first (car-atom $integers)
       (if (or (< $first -9223372036854775808) (> $first 9223372036854775807)) False
         (nl:bindable (cdr-atom $integers))))))
(: nl:stored-integers (-> %Undefined% %Undefined%))
(= (nl:stored-integers $values)
   (case (nl:python-ints $values)
     (((PythonException $class $message) (lp:python-error $class $message))
      ($integers
        (if (nl:bindable $integers) $integers
          (lp:python-error OverflowError "Python int too large to convert to SQLite INTEGER"))))))
(: nl:submitted (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:submitted $metadata $transport $now)
   (case (nl:stored-integers ($transport))
     (((LedgerError $error) (LedgerError $error))
      (($stored)
        (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")) ($request (lp:request-id))
               ($native (lp:native "llm-submitted" ($id $epoch $stored $now $request))))
          (if (lp:legacy $native)
            (nl:transaction
              ((sqlite:statement "UPDATE native_calls SET state='submitted',transport=?,updated=? WHERE id=? AND epoch=? AND state='claimed'"
                                  ($stored $now $id $epoch))
               (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'submitted',?,'{}' WHERE changes()=1" ($id $now))) $now)
            $native))))))
(: nl:progress (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:progress $metadata $bytes $quiet $soft $now)
   (case (nl:stored-integers ($bytes $quiet $soft))
     (((LedgerError $error) (LedgerError $error))
      (($count $wait $elapsed)
        (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")) ($request (lp:request-id))
               ($native (lp:native "llm-progress" ($id $epoch $count $wait $elapsed $now $request))))
          (if (lp:legacy $native)
            (nl:transaction
              ((sqlite:statement "UPDATE native_calls SET bytes=?,quiet_ms=?,soft_elapsed=?,updated=? WHERE id=? AND epoch=? AND state='submitted'"
                ($count $wait $elapsed $now $id $epoch))) $now)
            $native))))))

(: nl:settle-statements (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:settle-statements $metadata $state $result $reason $soft $now $require-completion)
   (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($abandon (and (ue:equal (nl:field $metadata "purpose" "turn") "compaction")
                         (or (ue:equal $state "uncertain") (ue:equal $state "late")))))
     (lp:append
       ((sqlite:statement
          (str:concat "UPDATE native_calls SET state=?,result=?,reason=?,updated=?,soft_elapsed=MAX(soft_elapsed,?) WHERE id=? AND epoch=? AND state IN ('claimed','submitted')"
                      (if $require-completion " AND (SELECT changed FROM lp_transition)=1" ""))
          ((if $abandon "abandoned" $state) (if $abandon sqlite:null (lp:sql-null $result))
           (lp:sql-null $reason) $now $soft $id $epoch))
        (sqlite:statement "INSERT INTO nl_transition VALUES(changes())" ()))
       (if (ue:equal $state "uncertain")
         ((sqlite:statement "UPDATE requests SET status='unknown',output=NULL,finish='transport-unknown' WHERE id=? AND (SELECT changed FROM nl_transition)=1" ($id))
          (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'unknown',?,? WHERE (SELECT changed FROM nl_transition)=1"
            ($id $now "{\"reason\":\"transport-unknown\"}"))) ()))))
(: nl:valid-settlement (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:valid-settlement $metadata $state $result)
   (if (not (or (ue:equal $state "completed") (or (ue:equal $state "late") (or (ue:equal $state "failed") (ue:equal $state "uncertain")))))
     (LedgerInvalid (PythonException ValueError "invalid native call outcome"))
     (if (ue:equal $result JsonNull) True
       (if (> (str:byte-length $result) (* (nl:field $metadata "cap" 0) 4))
         (LedgerInvalid (PythonException ValueError "native evidence exceeds its bound")) True))))
(: nl:settle (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:settle $metadata $state $result $reason $soft $now)
   (case (nl:valid-settlement $metadata $state $result)
     ((True
       (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
              ($purpose (nl:field $metadata "purpose" "turn")) ($kept (lp:none $result)) ($why (lp:none $reason))
              ($request (lp:request-id))
              ($native (lp:native "llm-settle" ($id $epoch $purpose $state $kept $why $soft $now $request))))
         (if (lp:legacy $native)
           (lp:changed-result (nl:transaction
             (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
               (lp:append (nl:settle-statements $metadata $state $result $reason $soft $now False)
                          ((sqlite:statement "SELECT changed FROM nl_transition" ())))) $now))
           $native)))
      ($error $error))))

; Request, usage receipt and native settlement commit together. Epoch checks
; apply to both the usage observation and the authority to complete a turn.
(: nl:completion-state (-> %Undefined% %Undefined% %Undefined%))
(= (nl:completion-state $metadata $canonical)
   (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($native (lp:native "llm-completion-state" ($id $epoch $canonical))))
     (if (lp:legacy $native) (nl:completion-state-sqlite $id $epoch $canonical) $native)))
(: nl:completion-state-sqlite (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:completion-state-sqlite $id $epoch $canonical)
     ; A fresh read resolves an ambiguous local commit. A receipt is authority
     ; only for this call, epoch and exact canonical answer, committed together.
     (case (sqlite:observe (lp:path)
       "SELECT CASE WHEN r.status='completed' AND n.state='completed' AND n.result=? THEN 'committed' WHEN r.status='pending' AND n.state IN ('claimed','submitted') THEN 'pending' ELSE 'conflict' END FROM native_calls n JOIN requests r ON r.id=n.id WHERE n.id=? AND n.epoch=?"
       ($canonical $id $epoch) 5000)
       (((sqlite:result $columns (("committed")) $changes $row-id) (LedgerCompletion committed))
        ((sqlite:result $columns (("pending")) $changes $row-id) (LedgerCompletion pending))
        ((sqlite:result $columns $rows $changes $row-id) (LedgerCompletion conflict))
        ($error (LedgerError $error)))))

(: nl:complete (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:complete $metadata $usage $finish $canonical $soft $now)
   (let* (($evidence (ue:evidence $usage)) ($output (lp:output $evidence)) ($reason (lp:text $finish)))
       (case (nl:valid-settlement $metadata "completed" $canonical)
         ((True
           (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
                  ($short (str:char-slice $reason 0 (math:min 64 (str:char-length $reason))))
                  ($usage-detail (lp:detail $evidence)) ($stored (lp:none $output))
                  ($completed (lp:detail (JsonObject ((JsonPair "finish" $short)))))
                  ($claimed (lp:detail (JsonObject ((JsonPair "epoch" $epoch)))))
                  ($purpose (nl:field $metadata "purpose" "turn")) ($request (lp:request-id))
                  ($native (lp:native "llm-complete" ($id $epoch $usage-detail $stored $short $completed $claimed
                                                      $canonical $soft $purpose $now $request))))
             (if (lp:legacy $native)
               (lp:complete-owned $id $evidence $short $output $epoch $now
                 (lp:append (nl:prune $now)
                   (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
                     (nl:settle-statements $metadata "completed" $canonical JsonNull $soft $now True))))
               $native)))
          ($error $error)))))
(: nl:fail (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:fail $metadata $kind $status $retry $soft $now)
   (let* (($sent (nl:field $metadata "sent" False))
          ($state (if (and $sent (ue:equal $kind "transport-error")) "uncertain" "failed")))
     (case (let $native (lp:fail-native (nl:field $metadata "id" "") $sent $status $retry (nl:field $metadata "epoch" "")
                                         $state $kind $soft (nl:field $metadata "purpose" "turn") $now)
             (if (lp:legacy $native)
               (lp:fail-owned (nl:field $metadata "id" "") $sent $status $retry
                 (nl:field $metadata "epoch" "") $now
                 (lp:append (nl:prune $now)
                   (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
                     (nl:settle-statements $metadata $state JsonNull $kind $soft $now True))))
               $native))
       ; A known terminal request may still have an unsettled old-peer lease.
       ; This is not a replay of a failed/unknown commit: only an authoritative
       ; unchanged result permits the separate lifetime reconciliation.
       (((LedgerChanged False) (nl:settle $metadata $state JsonNull $kind $soft $now))
        ($answer $answer)))))

(: nl:command-output-rejected (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:command-output-rejected $metadata $code $now)
   (if (not (or (ue:equal $code "invalid-envelope")
               (or (ue:equal $code "invalid-native-tool")
                   (or (ue:equal $code "incomplete-output") (ue:equal $code "refused-or-structured-output")))))
     (lp:python-error ValueError "invalid command rejection code")
     (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")) ($request (lp:request-id))
            ($native (lp:native "llm-rejected" ($id $epoch $code $request))))
       (if (lp:legacy $native)
         (lp:statement-changed (nl:transaction
           ((sqlite:statement "UPDATE native_calls SET reason=? WHERE id=? AND epoch=? AND state='completed' AND purpose='turn'"
              ((str:concat "command-output:" $code) $id $epoch))) $now))
         $native))))
