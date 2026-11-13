; Operator status, natively: the command responder's /health and /activity.
;
; Both read the records the runtime already keeps and change nothing: they
; wake nothing, spend no budget and call no model.
;
; - /health (rh:report): one runtime-health line. Its problems: the operator
;   control's readiness record or, without one, the channel poll is stale; the
;   cognitive boundary is stale outside a legitimate wait; a required model
;   turn is overdue, stale or has left no receipt; memory is not the native
;   service, or that service does not answer.
; - /activity (rh:activity): what cognition is doing now, derived from the
;   checkpoint (working set), the turn health record, the Telegram client's
;   published rest and input counts, the loop's progress receipt, and the
;   selected fuel, mode and model.
;
; The texts are those runtime_health.report and activity_report gave. A field
; that is not a number reads as absent; scripts/runtime_watch.py, outside the
; agent, keeps runtime_health.status for itself.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./state_file_cetta.metta)
!(import! &self ./turn_health_cetta.metta)
!(import! &self ./mode_state.metta)
!(import! &self ./fuel_cetta.metta)
!(import! &self ./model_selection_cetta.metta)
!(import! &self ./memory_health_cetta.metta)
!(import! &self ./process_identity_cetta.metta)
!(import! &self ./mode_text_cetta.metta)

;; ------------------------------------------------------------- values

(= (rh:env $name $default) (system:getenv-or-default $name $default))
(= (rh:number? $value) (== (get-type $value) Number))
(= (rh:max $a $b) (if (< $a $b) $b $a))
; A JSON value as Python's float() and int() take it: a number, a Boolean as
; 1 or 0, else None.
(= (rh:numeric $value)
   (if (rh:number? $value) $value (case $value ((True 1) (False 0) ($other None)))))
; float(record.get(key, 0) or 0): a number, else 0.
(= (rh:stamp $record $key)
   (let $value (rh:numeric (sf:get $record $key 0)) (if (== $value None) 0 $value)))
; max(0, int(record.get(key, 0) or 0)): a whole number of at least 0, else 0.
(= (rh:count $record $key) (rh:max 0 (rh:count-any $record $key)))
; The age of a stamp in whole seconds, or None for no stamp.
(= (rh:age $stamp $now)
   (if (== $stamp 0) None (rh:max 0 (math:trunc (- $now $stamp)))))
(= (rh:aged? $age $limit) (if (== $age None) False (> $age $limit)))
; Python's truth of a JSON value.
(= (rh:truthy? $value)
   (if (rh:number? $value) (not (== $value 0))
     (case $value
       ((JsonNull False) (False False) ("" False) ((JsonArray ()) False) ((JsonObject ()) False)
        ($other True)))))
; record.get(key) is True: the JSON true itself.
(= (rh:true? $record $key) (case (sf:get $record $key JsonNull) ((True True) ($other False))))
; str() of a value, and of an age: None reads "None".
(= (rh:str $value) (if (== $value None) "None" (erv:str $value)))
(= (rh:join $parts) (str:join "" $parts))
(= (rh:empty-object? $record) (case $record (((JsonObject ()) True) ($other False))))

; METTACLAW_COGNITIVE_STALE_SECONDS as max(30, int(float(text))), 900 for text
; that is not a finite number.
(= (rh:stale-after) (rh:stale-seconds (rh:env "METTACLAW_COGNITIVE_STALE_SECONDS" "900")))
(= (rh:stale-seconds $setting)
   (let $text (str:trim $setting)
     (case (catch (str:string->number $text))
       (((Error $where $why) 900)
        ($seconds (if (and (rh:number? $seconds) (and (== $seconds $seconds) (< (math:abs $seconds) 1.0e18)))
                    (rh:max 30 (math:trunc $seconds))
                    900))))))

;; ------------------------------------------------------------- the records

(= (rh:state-home)
   (rh:env "XDG_STATE_HOME" (path:join (rh:env "HOME" ".") ".local/state")))
(= (rh:instance) (rh:env "METTACLAW_INSTANCE" "default"))
; The Telegram client's health facts, and beside them what it publishes.
(= (rh:channel-path)
   (let $configured (rh:env "METTACLAW_TELEGRAM_HEALTH_PATH" "")
     (if (not (== $configured "")) $configured
       (path:join (path:join (path:join (rh:state-home) "pettaclaw") (rh:instance)) "telegram-health.json"))))
(= (rh:client-path)
   (let $health (rh:channel-path)
     (str:concat (if (str:ends-with $health ".json") (str:byte-slice $health 0 (- (str:byte-length $health) 5)) $health)
                 ".client.json")))
(= (rh:working-path) (rh:env "METTACLAW_WORKING_SET_PATH" "memory/working_set.json"))
(= (rh:mode-path) (rh:env "METTACLAW_LOOP_MODE_PATH" "memory/loop_mode.json"))
; The responder's readiness record, read only beside an engine state path.
(= (rh:control-path)
   (let $engine (rh:env "METTACLAW_ENGINE_STATE_PATH" "")
     (if (== $engine "") ""
       (let $root (rh:env "METTACLAW_MODE_STATE_ROOT" "")
         (path:join (if (== $root "") (path:join (path:dirname $engine) "modes") $root) "control-status.json")))))
(= (rh:control)
   (let $path (rh:control-path) (if (== $path "") (JsonObject ()) (sf:native-object $path))))

;; ------------------------------------------------------------- the turn

; (pending obligation): the in-flight call's start and the open obligation's.
; A record before schema 2 kept one pending_since for both: a failed turn's is
; the obligation, any other the call.
(= (rh:turn-stamps $cognition)
   (let* (($pending (rh:stamp $cognition "in_flight_since"))
          ($obligation (rh:stamp $cognition "obligation_since"))
          ($schema-value (rh:numeric (sf:get $cognition "schema" 1)))
          ($schema (if (== $schema-value None) 1 (if (== $schema-value 0) 1 (math:trunc $schema-value)))))
     (if (>= $schema 2) ($pending $obligation)
       (let $legacy (rh:stamp $cognition "pending_since")
         (if (== (sf:get $cognition "last_outcome" JsonNull) "failed")
           ($pending (if (== $obligation 0) $legacy $obligation))
           ((if (== $pending 0) $legacy $pending) $obligation))))))

;; ------------------------------------------------------------- /health

; The facts of one status, from the records read at NOW:
; (RhStatusV1 PROBLEMS POLL-AGE CONTROL CONTROL-AGE LOOP WORKING-AGE TURN-AGE PENDING-AGE MEMORY-MODE).
; CONTROL is the readiness record, (JsonObject ()) when there is none.
(= (rh:status $now $channel $working $mode $cognition $enabled $control $memory)
   (let* (($poll-age (rh:age (rh:stamp $channel "last_poll_ok_at") $now))
          ($saved (rh:stamp $working "saved_at"))
          ($waiting-until (rh:stamp $channel "waiting_until"))
          ($legitimate-wait (if (== (sf:get $channel "loop_status" JsonNull) "waiting") (>= $waiting-until (- $now 10)) False))
          (($pending $obligation) (rh:turn-stamps $cognition))
          ($pending-age (rh:age $pending $now))
          ($turn-age (rh:age (rh:stamp $cognition "last_completed_at") $now))
          ($loops (rh:count $working "loops"))
          ($active (loop-mode:canonical (sf:get $mode "mode" "default")))
          ($autonomous (rh:member? $active (loop-mode:names)))
          ($upstream (or (== $active "iter") (== $active "omega")))
          ($settled (rh:stamp $cognition "last_settled_at"))
          ; Upstream modes checkpoint their own history, not the working set.
          ($working-age (if (and $upstream (not (== $settled 0))) (rh:age $settled $now) (rh:age $saved $now)))
          ($has-control (not (rh:empty-object? $control)))
          ($control-age (if $has-control (rh:age-from (rh:stamp $control "observed_at") $now) None))
          ; An autonomous mode expects cognition, but a rest in flight is a
          ; legitimate reason for there to be none.
          ($required (if $enabled
                       (or (or (not (== $pending 0)) (not (== $obligation 0)))
                           (or (> $loops 0) (and $autonomous (not $legitimate-wait))))
                       False))
          ($stale-after (rh:stale-after))
          ($channel-problems
            (if $has-control
              (rh:append (if (> $control-age 30) ("operator-control-stale") ())
                         (if (rh:truthy? (sf:get $control "channel_ready" JsonNull)) () ("channel-service-unavailable")))
              (if (or (== $poll-age None) (rh:aged? $poll-age 120)) ("telegram-poll-stale") ())))
          ($boundary-problems
            (if (and $enabled (and (not $legitimate-wait) (or (== $working-age None) (rh:aged? $working-age 900))))
              ("cognitive-boundary-stale") ()))
          ($turn-problems (rh:turn-problem $required $pending-age $turn-age $stale-after $cognition $channel $saved $now))
          ($memory-problems (rh:memory-problem $memory))
          ($problems (rh:append $channel-problems (rh:append $boundary-problems (rh:append $turn-problems $memory-problems)))))
     (RhStatusV1 $problems $poll-age $control $control-age
                 (if $legitimate-wait "waiting" (sf:get $channel "loop_status" "unknown"))
                 $working-age $turn-age $pending-age (rh:memory-mode $memory))))
; An age that is never None: a missing stamp counts from the epoch.
(= (rh:age-from $stamp $now) (rh:max 0 (math:trunc (- $now $stamp))))
(= (rh:member? $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (rh:member? $item (cdr-atom $items)))))
(= (rh:append $front $back)
   (if (== $front ()) $back
     (let* (($head (car-atom $front)) ($rest (rh:append (cdr-atom $front) $back))) (cons-atom $head $rest))))

; The model turn's problem, at most one: an overdue call (a received answer
; still being stored is blocked locally), else a stale completion, else no
; receipt at all long after the channel or the checkpoint started.
(= (rh:turn-problem $required $pending-age $turn-age $stale-after $cognition $channel $saved $now)
   (if (not $required) ()
     (if (not (== $pending-age None))
       (if (> $pending-age $stale-after)
         (if (rh:true? $cognition "local_commit_pending") ("local-completion-blocked") ("model-turn-overdue"))
         ())
       (if (rh:aged? $turn-age $stale-after) ("model-turn-stale")
         (if (not (rh:empty-object? $cognition)) ()
           (let* (($channel-start (rh:stamp $channel "started_at"))
                  ($started (if (== $channel-start 0) $saved $channel-start)))
             (if (and (not (== $started 0)) (> (- $now $started) $stale-after)) ("model-turn-receipt-missing") ())))))))

; The memory backend as (RhMemoryV1 MODE REACHABLE); only a native one is probed.
(= (rh:memory)
   (let* (($configured (memory-health:backend (system:cwd)))
          ($mode (memory-health:field $configured "mode" "unconfigured")))
     (if (== $mode "native")
       (RhMemoryV1 $mode (memory-health:reachable? (memory-health:field $configured "socket" JsonNull)))
       (RhMemoryV1 $mode False))))
(= (rh:memory-mode (RhMemoryV1 $mode $reachable)) $mode)
(= (rh:memory-problem (RhMemoryV1 $mode $reachable))
   (if (not (== $mode "native")) ("memory-not-native")
     (if $reachable () ("memory-service-unreachable"))))

; The checkout's commit, cut to 12 characters, else METTACLAW_DEPLOYED_COMMIT's.
(= (rh:generation)
   (case (proc:run ("git" "rev-parse" "HEAD") (system:cwd) inherit 2000 65536)
     (((proc:exited 0 $out $err) (rh:prefix (str:trim $out) 12))
      ($failed (rh:prefix (rh:env "METTACLAW_DEPLOYED_COMMIT" "unknown") 12)))))
(= (rh:prefix $text $n) (if (> (str:char-length $text) $n) (str:char-slice $text 0 $n) $text))

; The receipt run.sh writes when the engine last exited unasked: "none", or
; its reason and status. Its lines are KEY=VALUE, the last of a key kept.
(= (rh:engine-failure-path)
   (let $configured (rh:env "METTACLAW_ENGINE_FAILURE_PATH" "")
     (if (not (== $configured "")) $configured
       (path:join (path:join (rh:state-home) (rh:instance)) "engine-failure"))))
(= (rh:engine-failure)
   (case (fs:read-text-result (rh:engine-failure-path) 0 "raw")
     ((("ok" $text)
       (let $fields (rh:fields (str:split "\n" $text) ())
         (let $reason (rh:field $fields "reason" "")
           (if (== $reason "") "none"
             (rh:join ($reason " (status " (rh:field $fields "status" "unknown") ")"))))))
      ($unreadable "none"))))
(= (rh:fields $lines $fields)
   (if (== $lines ()) $fields
     (let* (($line (rh:without-return (car-atom $lines)))
            ($more (case (str:find $line "=")
                     (((TextNotFound $needle) $fields)
                      ($at (cons-atom ((str:byte-slice $line 0 $at) (str:byte-slice $line (+ $at 1) (str:byte-length $line))) $fields))))))
       (rh:fields (cdr-atom $lines) $more))))
(= (rh:without-return $line)
   (if (str:ends-with $line "\r") (str:byte-slice $line 0 (- (str:byte-length $line) 1)) $line))
; The newest field of a key: fields are kept newest first.
(= (rh:field $fields $key $default)
   (if (== $fields ()) $default
     (let ($name $value) (car-atom $fields)
       (if (== $name $key) $value (rh:field (cdr-atom $fields) $key $default)))))

(= (rh:report-text (RhStatusV1 $problems $poll-age $control $control-age $loop $working-age $turn-age $pending-age $memory)
                   $lifecycle $generation $engine-failure)
   (rh:join ("runtime-health: " (if (== $problems ()) "ok" "problem")
             " | lifecycle=" $lifecycle
             " | generation=" $generation
             " | telegram="
             (if (rh:empty-object? $control)
               (rh:join ("poll-age=" (rh:str $poll-age) "s"))
               (rh:join ("service-ready=" (rh:str (sf:get $control "channel_ready" JsonNull))
                         ", control-age=" (rh:str $control-age) "s (polling owned by transport)")))
             " | loop=" (rh:str $loop)
             " | working-set-age=" (rh:str $working-age) "s"
             " | model-turn-age=" (rh:str $turn-age) "s"
             " | model-turn-pending=" (rh:str $pending-age) "s"
             " | memory=" (rh:str $memory)
             " | engine-failure=" $engine-failure
             " | problems=" (if (== $problems ()) "none" (str:join "," $problems)))))

; /health at NOW. A lifecycle that cannot be read is the answer's Error.
(= (rh:report-at $now)
   (let $enabled (lifecycle:enabled)
     (if (rh:error? $enabled) $enabled
       (let $lifecycle (lifecycle:view)
         (if (rh:error? $lifecycle) $lifecycle
           (rh:report-text
             (rh:status $now (sf:native-object (rh:channel-path)) (sf:native-object (rh:working-path))
                        (sf:native-object (rh:mode-path)) (health:read) (== $enabled 1) (rh:control) (rh:memory))
             $lifecycle (rh:generation) (rh:engine-failure)))))))
(= (rh:report) (rh:report-at (system:wall-time)))
(= (rh:error? $value) (case $value (((Error $where $why) True) ($other False))))

;; ------------------------------------------------------------- /activity

; What the Telegram client published: (RhClientV1 SLEEP-UNTIL PENDING PREPARED).
(= (rh:client)
   (let $published (sf:native-object (rh:client-path))
     (RhClientV1 (rh:stamp $published "sleep_until") (rh:count-any $published "pending_count")
                 (rh:count-any $published "prepared_count"))))

; The loop's progress receipt: METTACLAW_PROGRESS_PATH, else progress.json
; beside the engine state; "" without either.
(= (rh:progress-path)
   (let $explicit (rh:env "METTACLAW_PROGRESS_PATH" "")
     (if (not (== $explicit "")) $explicit
       (let $engine (rh:env "METTACLAW_ENGINE_STATE_PATH" "")
         (if (== $engine "") "" (path:join (path:dirname $engine) "progress.json"))))))

; A cgroup v2 account: (RhMemoryAccountV1 BYTES HIGH MAX), each a number or
; None; all None when the process's group cannot be read.
(= (rh:account $pid)
   (case (fs:read-text-result (rh:join ("/proc/" (repr $pid) "/cgroup")) 0 "raw")
     ((("ok" $text)
       (case (rh:unified-group (str:split "\n" $text))
         ((None (RhMemoryAccountV1 None None None))
          ($relative
           (let* (($group (path:join "/sys/fs/cgroup" (rh:strip-slashes $relative)))
                  ($current (rh:cgroup-number (path:join $group "memory.current"))))
             (if (== $current None) (RhMemoryAccountV1 None None None)
               (let* (($high (rh:cgroup-limit (path:join $group "memory.high")))
                      ($max (rh:cgroup-limit (path:join $group "memory.max"))))
                 (if (or (== $high Unreadable) (== $max Unreadable)) (RhMemoryAccountV1 None None None)
                   (RhMemoryAccountV1 $current $high $max)))))))))
      ($unreadable (RhMemoryAccountV1 None None None)))))
(= (rh:unified-group $lines)
   (if (== $lines ()) None
     (let $line (car-atom $lines)
       (if (str:starts-with $line "0::") (str:byte-slice $line 3 (str:byte-length $line))
         (rh:unified-group (cdr-atom $lines))))))
(= (rh:strip-slashes $text)
   (if (str:starts-with $text "/") (rh:strip-slashes (str:byte-slice $text 1 (str:byte-length $text))) $text))
(= (rh:cgroup-number $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text) (rh:whole (str:trim $text)))
      ($unreadable None))))
; A limit: a whole number, None for "max", Unreadable otherwise.
(= (rh:cgroup-limit $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (let $trimmed (str:trim $text)
         (if (== $trimmed "max") None
           (case (rh:whole $trimmed) ((None Unreadable) ($n $n))))))
      ($unreadable Unreadable))))
(= (rh:whole $text)
   (if (and (not (== $text "")) (str:is-all-in digit $text)) (str:string->number $text) None))

; The progress state at NOW: "memory-pressure", "waiting", "stalled", "fresh",
; or "unknown" when there is no readable receipt of a live owner. MONOTONIC is
; the clock the receipt's own wait deadline is written in.
(= (rh:progress $now $monotonic)
   (let $path (rh:progress-path)
     (if (== $path "") "unknown"
       (case (fs:read-text-result $path 0 "raw")
         ((("ok" $text)
           (case (catch (json:parse $text))
             (((JsonObject $pairs) (rh:progress-of (JsonObject $pairs) $now $monotonic))
              ($other "unknown"))))
          ($unreadable "unknown"))))))
(= (rh:progress-of $receipt $now $monotonic)
   (let $owner (sf:get $receipt "owner" "")
     (if (not (if (== (get-type $owner) String) (process:alive? $owner) False)) "unknown"
       (let* (($at (rh:numeric (sf:get $receipt "progress_at" JsonNull)))
              ($pid (rh:numeric (sf:get $receipt "pid" JsonNull))))
         (if (or (== $at None) (== $pid None)) "unknown"
           (if (rh:pressed? (rh:account (math:trunc $pid))) "memory-pressure"
             (let $wait (rh:numeric (sf:get $receipt "wait_until" 0))
               (if (== $wait None) "unknown"
                 (if (>= $wait $monotonic) "waiting"
                   (let $stale (rh:seconds-setting "METTACLAW_PROGRESS_STALE_SECONDS" "180")
                     (if (== $stale None) "unknown"
                       (if (> (rh:max 0 (- $now $at)) $stale) "stalled" "fresh"))))))))))))
(= (rh:pressed? (RhMemoryAccountV1 $bytes $high $max))
   (let $ceiling (if (rh:limit? $high) $high $max)
     (if (and (rh:limit? $ceiling) (not (== $bytes None))) (> $bytes $ceiling) False)))
(= (rh:limit? $value) (if (rh:number? $value) (not (== $value 0)) False))
; A setting read as float(text): the number, or None when it is not one.
(= (rh:seconds-setting $name $default)
   (case (catch (str:string->number (str:trim (rh:env $name $default))))
     (((Error $where $why) None)
      ($seconds (if (rh:number? $seconds) $seconds None)))))

; " ".join(text.split()): Python's whitespace runs as single spaces, trimmed.
(= (rh:words $text)
   (str:from-python-codepoints (rh:words-of (str:python-codepoints $text) () False)))
(= (rh:words-of $points $kept $gap)
   (if (== $points ()) (rh:reverse $kept ())
     (let* (($point (car-atom $points)) ($rest (cdr-atom $points)))
       (if (mode-text:space? $point) (rh:words-of $rest $kept (not (== $kept ())))
         (rh:words-of $rest (cons-atom $point (if $gap (cons-atom 32 $kept) $kept)) False)))))
(= (rh:reverse $items $done)
   (if (== $items ()) $done (rh:reverse (cdr-atom $items) (cons-atom (car-atom $items) $done))))

; "never", or the age in seconds.
(= (rh:since $age) (if (== $age None) "never" (rh:join ((repr $age) "s"))))

; The activity line at NOW from its inputs. CLIENT is (RhClientV1 ...), PROGRESS
; the progress state, SELECTED (FUEL MODE MODEL).
(= (rh:activity-text $now $working $cognition $enabled $client $progress $selected)
   (let* ((($fuel $mode $model) $selected)
          ((RhClientV1 $sleep-until $pending-inputs $prepared-inputs) $client)
          ($loops (rh:count $working "loops"))
          ($banked (rh:count $working "banked_loops"))
          ($checkpoint-age (rh:age (rh:stamp $working "saved_at") $now))
          ($turn-age (rh:age (rh:stamp $cognition "last_completed_at") $now))
          (($pending $obligation) (rh:turn-stamps $cognition))
          ($pending-age (rh:age $pending $now))
          ($obligation-age (rh:age $obligation $now))
          ($continuation (rh:truthy? (sf:get $working "continuation_pending" JsonNull)))
          ($left (- $sleep-until $now))
          ($resting (> $left 0))
          ($rest-left (if $resting (math:ceil $left) 0))
          ($recording (rh:true? $cognition "local_commit_pending"))
          ($failed (== (sf:get $cognition "last_outcome" JsonNull) "failed"))
          ($held (rh:held $enabled $cognition $pending-age $recording $loops $banked $continuation $resting))
          ($retried (rh:retried $held $pending-age $failed $cognition $now))
          ($observed (rh:observed $retried $enabled $progress $checkpoint-age $pending-age))
          ((RhActivityV1 $state $wake $steps $source) $observed)
          ($turn (if (not (== $pending-age None))
                   (rh:join ((if $recording "recording-result:" "pending:") (rh:since $pending-age)))
                   (if $failed
                     (rh:join ("failed:" (rh:since (rh:age (rh:stamp $cognition "last_failed_at") $now)) "-ago"))
                     (rh:join ("completed:" (rh:since $turn-age) "-ago")))))
          ($intent (rh:words (rh:text-of (sf:get $working "intent" ""))))
          ($rest (if $resting
                   (rh:join ((repr $rest-left) "s-left" (if (== $intent "") "" (str:concat ":" (rh:prefix $intent 120)))))
                   "none"))
          ($input (if (not (== $prepared-inputs 0)) (rh:join ("prepared:" (repr $prepared-inputs) "/pending:" (repr $pending-inputs)))
                    (if (not (== $pending-inputs 0)) (rh:join ("pending:" (repr $pending-inputs))) "none"))))
     (rh:join ("activity: " $state " | steps=" (repr $steps) "@" $source " | fuel=" $fuel
               " | mode=" $mode " | model=" $model " | wake=" $wake " | rest=" $rest
               " | continuation=" (if $continuation "pending" "none")
               " | checkpoint-age=" (rh:since $checkpoint-age) " | turn=" $turn
               " | last-success=" (if (== $turn-age None) "never" (rh:join ((repr $turn-age) "s-ago")))
               " | input=" $input
               " | obligation=" (if (== $obligation-age None) "none" (rh:join ("retry:" (rh:since $obligation-age))))
               " | failure-streak=" (repr (rh:count-any $cognition "failure_streak"))
               " | last-outcome=" (rh:str (sf:get $cognition "last_outcome" "none"))
               " | progress=" $progress
               " | background=use model-jobs for archived results"
               (rh:rejection (sf:get $cognition "provider_rejection" JsonNull))))))
; str(value or "") of a field.
(= (rh:text-of $value) (if (rh:truthy? $value) (rh:str $value) ""))
; int(record.get(key, 0) or 0), negative kept.
(= (rh:count-any $record $key)
   (let $value (rh:numeric (sf:get $record $key 0)) (if (== $value None) 0 (math:trunc $value))))

; The state the receipts imply before progress is consulted:
; (RhActivityV1 STATE WAKE STEPS STEPS-SOURCE).
(= (rh:held $enabled $cognition $pending-age $recording $loops $banked $continuation $resting)
   (if (not $enabled) (RhActivityV1 "stopped" "/start" 0 "operator-latch")
     (if (rh:truthy? (sf:get $cognition "provider_rejection" JsonNull))
       (RhActivityV1 "blocked" "input-or-configuration" $loops "checkpoint")
       (if (not (== $pending-age None))
         (RhActivityV1 (if $recording "recording-result" "working") (if $recording "local-storage" "now")
                       (rh:budget-at-start $cognition $loops) "turn-start")
         (if (> $loops 0) (RhActivityV1 "working" "now" $loops "checkpoint")
           (if (or $continuation $resting)
             ; Rest banks the budget rather than spending it, so the energy
             ; the agent wakes owing is the honest number while it sleeps.
             (RhActivityV1 (if $continuation "scheduled" "resting") "timer-or-human" (rh:max $loops $banked)
                           (if (> $banked $loops) "banked" "checkpoint"))
             (RhActivityV1 "idle" "human-or-heartbeat" $loops "checkpoint")))))))
(= (rh:budget-at-start $cognition $loops)
   (let $value (rh:numeric (sf:get $cognition "budget_at_start" $loops))
     (if (== $value None) $loops (rh:max 0 (math:trunc $value)))))

; Work whose last call failed, with none in flight, is a retry.
(= (rh:retried (RhActivityV1 $state $wake $steps $source) $pending-age $failed $cognition $now)
   (if (and (== $state "working") (and (== $pending-age None) $failed))
     (let $delay (rh:max 0 (math:trunc (- (rh:stamp $cognition "retry_at") $now)))
       (RhActivityV1 "retrying" (if (> $delay 0) (rh:join ("human-or-" (repr $delay) "s")) "retry-ready") $steps $source))
     (RhActivityV1 $state $wake $steps $source)))

; Work in progress as the progress receipt sees it.
(= (rh:observed (RhActivityV1 $state $wake $steps $source) $enabled $progress $checkpoint-age $pending-age)
   (if (not (and $enabled (rh:member? $state ("working" "retrying" "recording-result"))))
     (RhActivityV1 $state $wake $steps $source)
     (if (or (== $progress "stalled") (== $progress "memory-pressure")) (RhActivityV1 $progress "supervisor" $steps $source)
       (if (== $state "recording-result") (RhActivityV1 $state $wake $steps $source)
         (if (== $progress "waiting") (RhActivityV1 "waiting" "provider-or-deadline" $steps $source)
           (if (and (== $progress "unknown")
                    (and (rh:aged? $checkpoint-age 180) (or (== $pending-age None) (rh:aged? $pending-age 180))))
             (RhActivityV1 "unconfirmed" "check-supervisor" $steps $source)
             (RhActivityV1 $state $wake $steps $source)))))))

; The provider's retained rejection, its status, code and message, cut to 240 characters.
(= (rh:rejection $rejection)
   (if (not (rh:truthy? $rejection)) ""
     (rh:prefix (rh:join (" | provider-rejection=HTTP " (rh:str (sf:get $rejection "status" "?"))
                          " " (rh:str (sf:get $rejection "code" "")) ": " (rh:str (sf:get $rejection "message" ""))))
                240)))

; /activity at NOW. A lifecycle, fuel or mode that cannot be read is the
; answer's Error.
(= (rh:activity-at $now $monotonic)
   (let* (($enabled (lifecycle:enabled)) ($fuel (fuel:current)) ($mode (loop-mode:current)))
     (if (rh:error? $enabled) $enabled
       (if (rh:error? $fuel) $fuel
         (if (rh:error? $mode) $mode
           (rh:activity-text $now (sf:native-object (rh:working-path)) (health:read) (== $enabled 1) (rh:client)
                             (rh:progress $now $monotonic) ($fuel $mode (model:current "syn:large:text"))))))))
(= (rh:activity) (rh:activity-at (system:wall-time) (/ (system:monotonic-ns) 1000000000.0)))
