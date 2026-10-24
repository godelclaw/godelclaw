; Native ownership/settlement over the existing provider ledger. OS facts
; are observed before a transaction; guarded updates recheck the DB facts.
!(import! &self ./llm_policy_cetta.metta)

(: nl:field (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:field $metadata $key $default) (lp:field $metadata $key $default))
(: nl:prune (-> %Undefined% %Undefined%))
(= (nl:prune $now)
   ((sqlite:statement "UPDATE native_calls SET result=NULL WHERE state NOT IN ('claimed','submitted') AND (updated<? OR id NOT IN (SELECT id FROM native_calls WHERE result IS NOT NULL AND state NOT IN ('claimed','submitted') ORDER BY updated DESC LIMIT ?))" ((- $now 604800) 32))
    (sqlite:statement "DELETE FROM native_calls WHERE state NOT IN ('claimed','submitted') AND (updated<? OR id NOT IN (SELECT id FROM native_calls WHERE state NOT IN ('claimed','submitted') ORDER BY updated DESC LIMIT ?))" ((- $now 2592000) 256))))
(: nl:transaction (-> %Undefined% %Undefined% %Undefined%))
(= (nl:transaction $statements $now)
   (lp:transaction (lp:append (nl:prune $now) $statements)))
(: nl:ascii-id (-> %Undefined% %Undefined%))
(= (nl:ascii-id $text)
   (if (ue:equal $text "") True
     (let $first (str:char-slice $text 0 1)
       (case (str:find "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" $first)
         (((TextNotFound $ignored0) False)
          ($found (nl:ascii-id (str:char-slice $text 1 (str:char-length $text)))))))))
(: nl:valid-activity (-> %Undefined% %Undefined% %Undefined%))
(= (nl:valid-activity $activity $lane)
   (and (> (str:char-length $activity) 0)
     (and (<= (str:char-length $activity) 64)
       (and (or (ue:equal $lane "interactive") (ue:equal $lane "background")) (nl:ascii-id $activity)))))
(: nl:capacity (-> %Undefined%))
(= (nl:capacity)
   (let* (($capacity (lp:integer (lp:env "METTACLAW_ACTIVITY_CALL_CAPACITY" "2")))
          ($reserve (lp:integer (lp:env "METTACLAW_ACTIVITY_INTERACTIVE_RESERVE" "1"))))
     (if (or (ue:equal $capacity InvalidInteger) (ue:equal $reserve InvalidInteger)) (LedgerError "invalid activity capacity")
       (if (and (>= $reserve 1) (and (< $reserve $capacity) (<= $capacity 16)))
         (NativeCapacity $capacity $reserve) (LedgerError "invalid activity capacity")))))

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
              ($identity $now (json:stringify (JsonObject ((JsonPair "reason" $reason))))))) ())
         ((sqlite:statement "UPDATE requests SET status='unknown',output=NULL,finish='interrupted-unknown' WHERE id=? AND status='pending' AND (SELECT changed FROM nl_recovery)=1" ($identity))
          (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'unknown',?,? WHERE (SELECT changed FROM nl_recovery)=1"
            ($identity $now "{\"reason\":\"process-interrupted\"}")))))))
(: nl:recovery (-> %Undefined% %Undefined% %Undefined%))
(= (nl:recovery $row $now)
   (let ($identity $pid $start $state $updated $request $purpose $epoch) $row
     (if (and (or (ue:equal $request "completed") (or (ue:equal $request "failed") (ue:equal $request "unknown")))
              (>= (- $now $updated) 2))
       (if (ue:equal $purpose "compaction")
         (nl:recover-one $row "abandoned" "compaction-publication-not-completed" True $now)
         (if (ue:equal $request "completed")
           (nl:recover-one $row "completed" "request-completed-before-lifetime-settlement" True $now)
           (if (and (ue:equal $request "failed") (ue:equal $state "claimed"))
             (nl:recover-one $row "failed" "request-failed-before-submission" True $now)
             (nl:recover-one $row "uncertain" "terminal-request-remote-outcome-unknown" True $now))))
       (if (and (not (ue:equal $start sqlite:null)) (ue:equal (lp:process-start $pid) $start)) ()
         (nl:recover-one $row (if (ue:equal $purpose "compaction") "abandoned" "uncertain")
                             "process-interrupted-remote-outcome-unknown" False $now)))))
(: nl:recover-statements (-> %Undefined% %Undefined% %Undefined%))
(= (nl:recover-statements $rows $now)
   (if (ue:equal $rows ()) ()
     (lp:append (nl:recovery (car-atom $rows) $now) (nl:recover-statements (cdr-atom $rows) $now))))
(: nl:recover (-> %Undefined% %Undefined%))
(= (nl:recover $now)
   (case (lp:init)
     (((sqlite:committed $ignored1)
       (case (lp:rows (sqlite:observe (lp:path)
          "SELECT n.id,n.pid,n.process_start,n.state,n.updated,r.status,n.purpose,n.epoch FROM native_calls n LEFT JOIN requests r ON r.id=n.id WHERE n.state IN ('claimed','submitted')" () 5000))
         (((LedgerError $error) (LedgerError $error))
          ($rows (nl:transaction (lp:append
            ((sqlite:statement "CREATE TEMP TABLE nl_recovery(changed INTEGER NOT NULL)" ())
             (sqlite:statement "INSERT INTO nl_recovery VALUES(0)" ()))
             (nl:recover-statements $rows $now)) $now)))))
      ($error $error))))

(: nl:claim-insert (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:claim-insert $metadata $activity $lane $capacity $reserve $now)
   (let* (($identity (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($purpose (nl:field $metadata "purpose" "turn")) ($guard (nl:field $metadata "guard" JsonNull))
          ($pid (system:pid)) ($start (lp:sql-null (lp:process-start $pid))))
     (lp:changed-result (nl:transaction
       ; Validate purpose only after the refusal checks, under the same lock.
       ; The constraint rolls the transaction back on an eligible bad purpose.
       ((sqlite:statement "CREATE TEMP TABLE nl_claim(eligible INTEGER NOT NULL,valid INTEGER NOT NULL,changed INTEGER NOT NULL DEFAULT 0,CHECK(eligible=0 OR valid=1))" ())
        (sqlite:statement "INSERT INTO nl_claim(eligible,valid) SELECT (?='' AND NOT EXISTS(SELECT 1 FROM native_calls WHERE state IN ('claimed','submitted'))) OR (?<>'' AND NOT EXISTS(SELECT 1 FROM native_calls WHERE state IN ('claimed','submitted') AND (activity='' OR activity=?)) AND (SELECT COUNT(*) FROM native_calls WHERE state IN ('claimed','submitted'))<? AND (?<>'background' OR (SELECT COUNT(*) FROM native_calls WHERE state IN ('claimed','submitted') AND lane='background')<?)),? IN ('turn','coding','compaction')"
          ($activity $activity $activity $capacity $lane (- $capacity $reserve) $purpose))
        (sqlite:statement "INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,activity,lane,purpose) SELECT ?,?,?,?,?,?,'claimed',?,?,?,? WHERE (SELECT eligible FROM nl_claim)=1"
          ($identity $epoch $pid $start $now $now (lp:sql-null $guard) $activity $lane $purpose))
        (sqlite:statement "UPDATE nl_claim SET changed=changes()" ())
        (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'claimed',?,? WHERE (SELECT changed FROM nl_claim)=1"
          ($identity $now (json:stringify (JsonObject ((JsonPair "epoch" $epoch))))))
        (sqlite:statement "SELECT changed FROM nl_claim" ())) $now))))
(: nl:claim-ready (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim-ready $metadata $now)
   (let* (($activity (nl:field $metadata "activity" "")) ($lane (nl:field $metadata "lane" ""))
          ($purpose (nl:field $metadata "purpose" "turn")))
       (if (ue:equal $activity "") (nl:claim-insert $metadata $activity $lane 0 0 $now)
         (if (not (nl:valid-activity $activity $lane)) (LedgerChanged False)
           (case (nl:capacity)
             (((NativeCapacity $capacity $reserve) (nl:claim-insert $metadata $activity $lane $capacity $reserve $now))
              ($error $error)))))))
(: nl:claim (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim $metadata $now)
   (case (nl:recover $now)
     (((sqlite:committed $ignored2) (nl:claim-ready $metadata $now)) ($error $error))))

(: nl:submitted (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:submitted $metadata $transport $now)
   (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")))
     (nl:transaction
       ((sqlite:statement "UPDATE native_calls SET state='submitted',transport=?,updated=? WHERE id=? AND epoch=? AND state='claimed'"
                           ($transport $now $id $epoch))
        (sqlite:statement "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) SELECT ?,'submitted',?,'{}' WHERE changes()=1" ($id $now))) $now)))
(: nl:progress (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:progress $metadata $bytes $quiet $soft $now)
   (nl:transaction
     ((sqlite:statement "UPDATE native_calls SET bytes=?,quiet_ms=?,soft_elapsed=?,updated=? WHERE id=? AND epoch=? AND state='submitted'"
       ($bytes $quiet $soft $now (nl:field $metadata "id" "") (nl:field $metadata "epoch" "")))) $now))

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
     (LedgerInvalid "invalid native call outcome")
     (if (ue:equal $result JsonNull) True
       (if (> (str:byte-length $result) (* (nl:field $metadata "cap" 0) 4))
         (LedgerInvalid "native evidence exceeds its bound") True))))
(: nl:settle (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:settle $metadata $state $result $reason $soft $now)
   (case (nl:valid-settlement $metadata $state $result)
     ((True (lp:changed-result (nl:transaction
       (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
         (lp:append (nl:settle-statements $metadata $state $result $reason $soft $now False)
                    ((sqlite:statement "SELECT changed FROM nl_transition" ())))) $now)))
      ($error $error))))

; Request, usage receipt and native settlement commit together. Epoch checks
; apply to both the usage observation and the authority to complete a turn.
(: nl:completion-state (-> %Undefined% %Undefined% %Undefined%))
(= (nl:completion-state $metadata $canonical)
   (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")))
     ; A fresh read resolves an ambiguous local commit. A receipt is authority
     ; only for this call, epoch and exact canonical answer, committed together.
     (case (sqlite:observe (lp:path)
       "SELECT CASE WHEN r.status='completed' AND n.state='completed' AND n.result=? THEN 'committed' WHEN r.status='pending' AND n.state IN ('claimed','submitted') THEN 'pending' ELSE 'conflict' END FROM native_calls n JOIN requests r ON r.id=n.id WHERE n.id=? AND n.epoch=?"
       ($canonical $id $epoch) 5000)
       (((sqlite:result $columns (("committed")) $changes $row-id) (LedgerCompletion committed))
        ((sqlite:result $columns (("pending")) $changes $row-id) (LedgerCompletion pending))
        ((sqlite:result $columns $rows $changes $row-id) (LedgerCompletion conflict))
        ($error (LedgerError $error))))))

(: nl:complete (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:complete $metadata $usage $finish $canonical $soft $now)
   (let* (($evidence (ue:evidence $usage)) ($output (lp:output $evidence)) ($reason (lp:text $finish)))
       (case (nl:valid-settlement $metadata "completed" $canonical)
         ((True
           (lp:complete-owned (nl:field $metadata "id" "") $evidence
             (str:char-slice $reason 0 (math:min 64 (str:char-length $reason)))
             $output (nl:field $metadata "epoch" "") $now
             (lp:append (nl:prune $now)
               (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
                 (nl:settle-statements $metadata "completed" $canonical JsonNull $soft $now True)))))
          ($error $error)))))
(: nl:fail (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:fail $metadata $kind $status $retry $soft $now)
   (let* (($sent (nl:field $metadata "sent" False))
          ($state (if (and $sent (ue:equal $kind "transport-error")) "uncertain" "failed")))
     (case (lp:fail-owned (nl:field $metadata "id" "") $sent $status $retry
             (nl:field $metadata "epoch" "") $now
             (lp:append (nl:prune $now)
               (lp:append ((sqlite:statement "CREATE TEMP TABLE nl_transition(changed INTEGER NOT NULL)" ()))
                 (nl:settle-statements $metadata $state JsonNull $kind $soft $now True))))
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
     (LedgerError "invalid command rejection code")
     (lp:statement-changed (nl:transaction
       ((sqlite:statement "UPDATE native_calls SET reason=? WHERE id=? AND epoch=? AND state='completed' AND purpose='turn'"
          ((str:concat "command-output:" $code) (nl:field $metadata "id" "") (nl:field $metadata "epoch" "")))) $now))))
