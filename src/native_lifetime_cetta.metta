; Native ownership/settlement over the existing provider ledger. OS facts
; are observed before a transaction; guarded updates recheck the DB facts.
!(import! &self ./llm_policy_cetta.metta)

(: nl:field (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:field $metadata $key $default) (lp:field $metadata $key $default))
; Retention is explicit maintenance; ordinary calls never erase old receipts.
(: nl:prune (-> %Undefined% %Undefined%))
(= (nl:prune $now) ())
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

(: nl:claim (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim $metadata $now)
   (let $native (nl:claim-native $metadata $now)
     $native))

; The native claim: recovery, then the validation nl:claim-ready makes (its result travels
; with the claim, so recovery commits even when it refuses), eligibility and the purpose.
(: nl:claim-native (-> %Undefined% %Undefined% %Undefined%))
(= (nl:claim-native $metadata $now)
   (if (ue:equal (lp:socket) "") (LedgerError (Error lp:native "METTACLAW_INPUT_LEDGER_SOCKET is not set"))
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
          $native)))))
(: nl:progress (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:progress $metadata $bytes $quiet $soft $now)
   (case (nl:stored-integers ($bytes $quiet $soft))
     (((LedgerError $error) (LedgerError $error))
      (($count $wait $elapsed)
        (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" "")) ($request (lp:request-id))
               ($native (lp:native "llm-progress" ($id $epoch $count $wait $elapsed $now $request))))
          $native)))))

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
         $native))
      ($error $error))))

; Request, usage receipt and native settlement commit together. Epoch checks
; apply to both the usage observation and the authority to complete a turn.
(: nl:completion-state (-> %Undefined% %Undefined% %Undefined%))
(= (nl:completion-state $metadata $canonical)
   (let* (($id (nl:field $metadata "id" "")) ($epoch (nl:field $metadata "epoch" ""))
          ($native (lp:native "llm-completion-state" ($id $epoch $canonical))))
     $native))

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
             $native))
          ($error $error)))))
(: nl:fail (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (nl:fail $metadata $kind $status $retry $soft $now)
   (let* (($sent (nl:field $metadata "sent" False))
          ($state (if (and $sent (ue:equal $kind "transport-error")) "uncertain" "failed")))
     (case (let $native (lp:fail-native (nl:field $metadata "id" "") $sent $status $retry (nl:field $metadata "epoch" "")
                                         $state $kind $soft (nl:field $metadata "purpose" "turn") $now)
             $native)
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
       $native)))
