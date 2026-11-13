; Cognitive turn health: the outcome of the loop's model turn, its durable
; health record, and the pacing of a failed turn.
;
; - Configuration is immutable: the record's path and the pacing base and cap
;   come from the environment the process was started with.
; - The outcome is a phase this process owns. A turn begins pending; the owner
;   of its provider call ends it completed or failed. The host is never asked.
; - A failed turn's wait is a pure function of the failure's facts and the
;   streak of transient failures before it; the streak is owned here too.
; - The record (cognitive-health.json) is the durable receipt that operator
;   status, the failure notice and the rejection gate read. Each update is a
;   pure function of the record, the event and the time, written whole through
;   src/state_file_cetta.metta with the bytes cognitive_health._write gives the
;   same record. Python's cognitive_health keeps the same record for the
;   transports and mode hosts that still run there.
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library path))
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self ./state_file_cetta.metta)

; ---------------------------------------------------------------- configuration

; cognitive_health._path(): the configured record, or the instance's default.
(= (health:path)
   (let $configured (system:getenv-or-default "METTACLAW_COGNITIVE_HEALTH_PATH" "")
     (if (not (== $configured "")) $configured
       (let* (($home (system:getenv-or-default "HOME" "."))
              ($default-state (path:join $home ".local/state"))
              ($state (system:getenv-or-default "XDG_STATE_HOME" $default-state))
              ($instance (system:getenv-or-default "METTACLAW_INSTANCE" "default")))
         (path:join (path:join (path:join $state "pettaclaw") $instance) "cognitive-health.json")))))

; ------------------------------------------------------------------ the phase

; none before any turn, then pending, completed or failed.
(= (health:phase)
   (case (collapse (match &self (TurnOutcomeV1 $phase) $phase))
     ((() none)
      ($phases (car-atom $phases)))))
(= (health:set-phase $phase)
   (let* (($removed (collapse (match &self (TurnOutcomeV1 $old) (remove-atom &self (TurnOutcomeV1 $old)))))
          ($added (add-atom &self (TurnOutcomeV1 $phase))))
     $phase))
; A turn's model call begins: an outcome left by an earlier turn never
; answers for this one.
(= (health:begin-turn) (health:set-phase pending))
(= (health:turn-completed) (health:set-phase completed))
(= (health:turn-failed) (health:set-phase failed))
; The loop's question, answered 1 or 0 as its turn record keeps it.
(= (health:turn-succeeded) (if (== (health:phase) completed) 1 0))

; ----------------------------------------------------------- record values

(= (health:number? $value) (== (get-type $value) Number))
; Equal values of one type; HE refuses == across types.
(= (health:equal? $a $b) (if (== (get-type $a) (get-type $b)) (== $a $b) False))
(= (health:float $value) (+ $value 0.0))
; float(value.get(key, 0) or 0): a number, else 0.
(= (health:stamp $record $key)
   (let $value (sf:get $record $key 0) (if (health:number? $value) $value 0)))
(= (health:set? $record $key) (not (== (health:stamp $record $key) 0)))
; int(value.get(key, 0)), and the same plus one.
(= (health:counted $record $key)
   (let $value (sf:get $record $key 0) (if (health:number? $value) (health:integer $value) 0)))
(= (health:count $record $key) (+ (health:counted $record $key) 1))
(= (health:integer $value) (if (health:number? $value) (trunc-math $value) 0))
(= (health:nonnegative $value) (if (< $value 0) 0 $value))
(= (health:text $value) (if (== (get-type $value) String) $value (format-args "{}" ($value))))

; cognitive_health._label: lower case, letters, digits, '-' and '_' kept, at
; most 64 characters, the default when nothing is kept. Labels are ASCII
; names; Python's isalnum also keeps non-ASCII letters, which no label carries.
(= (health:label $value $default)
   (let* (($text (health:text $value))
          ($kept (if (== $text "") ""
                   (str:from-python-codepoints
                     (health:label-kept (str:python-codepoints (str:python-lower $text)) () 0)))))
     (if (== $kept "") $default $kept)))
; The first 64 kept codes, in order: a loop that stops once it has them.
(= (health:label-kept $codes $kept $count)
   (if (or (== $codes ()) (>= $count 64)) (health:reverse $kept ())
     (let* (($code (car-atom $codes)) ($rest (cdr-atom $codes)))
       (if (health:label-code? $code)
         (let $more (cons-atom $code $kept) (health:label-kept $rest $more (+ $count 1)))
         (health:label-kept $rest $kept $count)))))
(= (health:reverse $items $done)
   (if (== $items ()) $done
     (let* (($head (car-atom $items)) ($tail (cdr-atom $items)) ($more (cons-atom $head $done)))
       (health:reverse $tail $more))))
(= (health:label-code? $code)
   (or (or (and (>= $code 48) (<= $code 57)) (and (>= $code 97) (<= $code 122)))
       (or (and (>= $code 65) (<= $code 90)) (or (== $code 45) (== $code 95)))))

; ------------------------------------------------------ record transitions

; expect_turn: a model-turn obligation, never refreshing an older one.
(= (health:expected $record $mode $budget $now)
   (let* (($new (not (health:set? $record "obligation_since")))
          ($legacy (health:stamp $record "pending_since"))
          ($since (if $new (sf:put $record "obligation_since"
                             (if (== $legacy 0) (health:float $now) (health:float $legacy)))
                    $record))
          ($updated (sf:put-all $since ((JsonPair "schema" 2) (JsonPair "last_expected_at" (health:float $now))
                                        (JsonPair "mode" (health:label $mode "unknown")))))
          ($counted (if $new (sf:put $updated "expected_count" (health:count $record "expected_count")) $updated)))
     (sf:put $counted "budget_at_start" (health:nonnegative (health:integer $budget)))))

; turn_started: the provider call's own start is the age a hung-call check uses.
(= (health:started $record $provider $model $now)
   (let $since (if (health:set? $record "obligation_since") $record
                 (sf:put $record "obligation_since" (health:float $now)))
     (sf:put-all $since
       ((JsonPair "schema" 2) (JsonPair "pending_since" (health:float $now))
        (JsonPair "in_flight_since" (health:float $now)) (JsonPair "last_started_at" (health:float $now))
        (JsonPair "retry_at" 0) (JsonPair "started_count" (health:count $record "started_count"))
        (JsonPair "provider" (health:label $provider "unknown")) (JsonPair "model" (health:text $model))
        (JsonPair "response_bytes" 0) (JsonPair "transport_quiet_ms" 0)
        (JsonPair "soft_wait_elapsed" False) (JsonPair "local_commit_pending" False)))))

; provider_progress: transport observations of an admitted call; they never
; imply a completed turn.
(= (health:progressed $record $bytes $quiet $soft $hard $now)
   (if (not (health:set? $record "in_flight_since")) $record
     (sf:put-all $record
       ((JsonPair "transport_observed_at" (health:float $now))
        (JsonPair "response_bytes" (health:integer $bytes)) (JsonPair "transport_quiet_ms" (health:integer $quiet))
        (JsonPair "soft_wait_elapsed" (health:truthy? $soft))
        (JsonPair "hard_deadline_ms" (health:integer $hard))))))

; result_recording: a received answer awaits local storage.
(= (health:recording $record $attempt)
   (sf:put-all $record ((JsonPair "local_commit_pending" True)
                        (JsonPair "local_commit_attempts" (health:nonnegative (health:integer $attempt))))))

; turn_completed: the provider request ends with an answer. The input
; obligation is discharged later, when history and acknowledgement commit.
(= (health:completed $record $bytes $now)
   (let* (($admitted (health:set? $record "in_flight_since"))
          ($repaired (sf:drop $record "provider_context_repair"))
          ($resolved (case (sf:get $repaired "failure_episode" JsonNull)
                       (((JsonObject $pairs)
                         (sf:put $repaired "failure_episode" (sf:put (JsonObject $pairs) "resolved_at" (health:float $now))))
                        ($other $repaired))))
          ($updated (sf:put-all $resolved
                      ((JsonPair "schema" 2) (JsonPair "pending_since" 0) (JsonPair "in_flight_since" 0)
                       (JsonPair "last_completed_at" (health:float $now)) (JsonPair "retry_at" 0)
                       (JsonPair "last_response_bytes" (health:nonnegative (health:integer $bytes)))
                       (JsonPair "last_outcome" "completed") (JsonPair "local_commit_pending" False)
                       (JsonPair "failure_streak" 0))))
          ($field (if $admitted "completed_count" "completion_without_admission_count")))
     (sf:put $updated $field (health:count $record $field))))

; turn_failed: the request ends; its input obligation stays outstanding. A
; failure episode spans failures of one provider and model until a completion
; resolves it; a new episode takes the given identity.
(= (health:failed $record $kind $now $retry $identity)
   (let* (($admitted (health:set? $record "in_flight_since"))
          ($failure (health:label $kind "provider-error"))
          ($previous (sf:get $record "last_failure_type" JsonNull))
          ($held (health:stamp $record "obligation_since"))
          ($pending (health:stamp $record "pending_since"))
          ($obligation (health:float (if (== $held 0) (if (== $pending 0) $now $pending) $held)))
          ($provider (if $admitted (sf:get $record "provider" "unknown") "local-runtime"))
          ($model (if $admitted (sf:get $record "model" "") ""))
          ($episode (health:episode (sf:get $record "failure_episode" (JsonObject ())) $provider $model
                                    $failure $now $identity))
          ($updated (sf:put-all (sf:put $record "failure_episode" $episode)
                      ((JsonPair "schema" 2) (JsonPair "pending_since" 0) (JsonPair "in_flight_since" 0)
                       (JsonPair "obligation_since" $obligation) (JsonPair "last_failed_at" (health:float $now))
                       (JsonPair "retry_at" (+ (health:float $now) (health:nonnegative (health:float $retry))))
                       (JsonPair "last_failure_type" $failure)
                       (JsonPair "failure_streak"
                         (if (health:equal? $previous $failure) (health:count $record "failure_streak") 1))
                       (JsonPair "last_outcome" "failed"))))
          ($field (if $admitted "failed_count" "local_failure_count")))
     (sf:put $updated $field (health:count $record $field))))
(= (health:episode $episode $provider $model $failure $now $identity)
   (let* (($open (case $episode
                   (((JsonObject ()) False)
                    ((JsonObject $pairs)
                     (and (not (health:truthy? (sf:get $episode "resolved_at" JsonNull)))
                          (and (health:equal? (sf:get $episode "provider" JsonNull) $provider)
                               (health:equal? (sf:get $episode "model" JsonNull) $model))))
                    ($other False))))
          ($current (if $open $episode
                      (JsonObject ((JsonPair "id" $identity) (JsonPair "first_at" (health:float $now))
                                   (JsonPair "provider" $provider) (JsonPair "model" $model)
                                   (JsonPair "kind" $failure) (JsonPair "count" 0))))))
     (sf:put-all $current ((JsonPair "last_at" (health:float $now)) (JsonPair "last_kind" $failure)
                           (JsonPair "count" (health:count $current "count"))))))
(= (health:truthy? $value)
   (case $value
     ((JsonNull False) (False False) (0 False) (0.0 False) ("" False) ((JsonObject ()) False)
      ($other True))))

; Whether a failure episode is open: one recorded and not yet resolved. The
; receipt notice stays silent meanwhile; the episode has its own notice.
(= (health:failure-pending)
   (case (sf:get (health:read) "failure_episode" JsonNull)
     (((JsonObject $pairs)
       (and (not (== $pairs ())) (not (health:truthy? (sf:get-last $pairs "resolved_at" JsonNull)))))
      ($other False))))

; turn_settled: history and input acknowledgement committed the turn.
(= (health:settled $record $now)
   (let $updated (sf:put-all $record ((JsonPair "schema" 2) (JsonPair "obligation_since" 0)
                                      (JsonPair "last_settled_at" (health:float $now))))
     (if (health:set? $record "obligation_since")
       (sf:put $updated "settled_count" (health:count $record "settled_count"))
       $updated)))

; ------------------------------------------------------------ the receipt

; Read, update and replace the record; 1 when it is durable, else 0. A
; health write never fails the turn it describes.
(= (health:read) (sf:native-object (health:path)))
(= (health:write $record)
   (let* (($path (health:path))
          ($dir (path:dirname $path))
          ($made (if (fs:exists $dir) True (fs:make-directories $dir 511)))
          ($written (sf:replace $path (sf:python-json $record) 384 True)))
     (case $written ((True 1) ($failed 0)))))
(= (health:expect $mode $budget)
   (health:write (health:expected (health:read) $mode $budget (system:wall-time))))
(= (health:start $provider $model)
   (health:write (health:started (health:read) $provider $model (system:wall-time))))
(= (health:progress $bytes $quiet $soft $hard)
   (let $record (health:read)
     (if (health:set? $record "in_flight_since")
       (health:write (health:progressed $record $bytes $quiet $soft $hard (system:wall-time)))
       1)))
(= (health:record-wait $attempt)
   (health:write (health:recording (health:read) $attempt)))
(= (health:complete $bytes)
   (health:write (health:completed (health:read) $bytes (system:wall-time))))
(= (health:fail $kind $retry)
   (health:write (health:failed (health:read) $kind (system:wall-time) $retry (system:uuid4-hex))))
(= (health:settle)
   (health:write (health:settled (health:read) (system:wall-time))))

; Completions the record has counted, admitted or not. A host transport that
; keeps the record itself proves a completed turn by moving this count.
(= (health:completions)
   (let $record (health:read)
     (+ (health:counted $record "completed_count") (health:counted $record "completion_without_admission_count"))))

; ------------------------------------------------------------- the pacing

; HE has no min and max of numbers.
(= (health:min $a $b) (if (< $b $a) $b $a))
(= (health:max $a $b) (if (< $a $b) $b $a))

; The configured backoff in seconds, never below 0: SYNTHETIC_EMPTY_BACKOFF
; (15) and SYNTHETIC_EMPTY_BACKOFF_CAP (300). Text that is not a number keeps
; the default, as synthetic_llm._float_env does.
(= (health:env-seconds $name $default)
   (let $text (system:getenv-or-default $name "")
     (if (== $text "") $default
       (let $parsed (json:parse $text)
         (if (health:number? $parsed) (health:max (health:float $parsed) 0.0) $default)))))

; A client error the provider would give again for the same request; a
; timeout, conflict, too-early or rate limit can pass on a later try.
(= (health:rejected-status? $status)
   (if (and (>= $status 400) (< $status 500))
     (not (or (or (== $status 408) (== $status 409)) (or (== $status 425) (== $status 429))))
     False))

; The pacing of a failed turn, a pure function of its facts and the streak
; of transient failures before it; it answers (wait streak).
; - A transient failure backs off from base, doubling up to cap; any other
;   failure waits base and ends the streak. Zero delay would turn one
;   deterministic fault into an unbounded model and ledger loop.
; - The provider's retry-after (-1 for none) replaces that wait.
; - A deterministic rejection waits at least cap.
; - A retained rejection block waits 0: corrective input, not time, ends it.
(= (health:failure-wait $transient $streak $base $cap $retry $status $blocked)
   (let* (($next (if $transient (+ $streak 1) 0))
          ($backoff (if (and $transient (> $base 0))
                      (health:min $cap (* $base (pow-math 2 (health:min (- $next 1) 20))))
                      $base))
          ($stated (if (>= $retry 0) (health:float $retry) $backoff))
          ($floored (if (health:rejected-status? $status) (health:max $stated $cap) $stated))
          ($wait (if $blocked 0 $floored)))
     ($wait $next)))

; The streak of transient failures this process has paced.
(= (health:streak)
   (case (collapse (match &self (TurnPacingV1 $streak) $streak))
     ((() 0)
      ($streaks (car-atom $streaks)))))
(= (health:set-streak $streak)
   (let* (($removed (collapse (match &self (TurnPacingV1 $old) (remove-atom &self (TurnPacingV1 $old)))))
          ($added (add-atom &self (TurnPacingV1 $streak))))
     $streak))
; A real answer ends the streak.
(= (health:answered) (health:set-streak 0))

; A failed turn the host classified, as synthetic_llm.failure_facts gives
; it: the native owner paces it, records it, fails the phase and reports it,
; then answers the empty command list. Any other answer passes through.
(= (health:paced $answer)
   (case $answer
     ((("failed" $code $status $transient $retry $blocked $preparation)
       (let* (($base (health:env-seconds "SYNTHETIC_EMPTY_BACKOFF" 15.0))
              ($cap (health:env-seconds "SYNTHETIC_EMPTY_BACKOFF_CAP" 300.0))
              (($wait $streak) (health:failure-wait $transient (health:streak) $base $cap $retry $status $blocked))
              ($kept (health:set-streak $streak))
              ($recorded (health:fail $code $wait))
              ($phase (health:turn-failed))
              ($line (health:failure-line $code $wait $blocked $preparation))
              ($reported (system:write stderr $line)))
         "()"))
      ($other $other))))
; Only the code and the wait: exception text may carry URLs or bodies.
(= (health:failure-line $code $wait $blocked $preparation)
   (let* (($label (if $preparation "[context] preparation withheld: " "[synthetic_llm] failed provider turn: "))
          ($seconds (str:format-fixed $wait 0))
          ($tail (if $blocked "; awaiting corrective input or configuration\n"
                   (str:join "" ("; retry in " $seconds "s\n")))))
     (str:join "" ($label $code $tail))))
