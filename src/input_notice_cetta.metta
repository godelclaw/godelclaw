; Receipt notices for queued operator messages, for the control responder:
; when a notice is due, that it was submitted, and the scan that bounds what
; is remembered. A notice consumes no input, completes no work and calls no
; model.
;
; - A message's notice is due once the message has stayed pending for the
;   configured delay; at most one notice per lane a minute; none while a turn
;   failure is unresolved (that episode has its own notice; the health record
;   is read once per scan, not per message); none for an update the ledger
;   has processed. The ledger registers the notice under a key derived from
;   the instance and the task, so a restart or a lost reply repeats the same
;   key and the same bytes.
; - The pacing memory (first seen, submitted, lane; the last notice of a lane)
;   is this process's own. A complete scan forgets the tasks it did not see,
;   and a lane without a remembered task forgets its last notice.
; - A scan also yields the operator inbox view: how many operator messages
;   wait and their oldest and newest dates, without text, identities or
;   routes. Only a complete scan from the start is a view; anything unreadable
;   in it leaves no view.
;
; Entries: notice:key, notice:submitted, notice:scan-after, notice:begin-scan,
; notice:input-view, notice:wait.
!(import! &self ./ledger_client.metta)
!(import! &self ./notice_key.metta)
!(import! &self ./turn_health_cetta.metta)
!(import! &self ./utils_cetta.metta)
!(import! &self (library system))
!(import! &self (library str))
!(import! &self (library json))

;; ------------------------------------------------------------ configuration

; The ledger socket ("" without a ledger), whether observed strategy is on,
; the instance name, and the delay in seconds (none: no notices).
(= (notice:config)
   (NoticeConfigV1
     (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "")
     (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1")
     (system:getenv-or-default "METTACLAW_INSTANCE" "default")
     (notice:delay (system:getenv-or-default "METTACLAW_INPUT_ACK_SECONDS" "15"))))
; A positive finite number of seconds. Text that is no number means 15; zero,
; a negative number, a number too large to hold, "inf" and "nan" mean none.
(= (notice:delay $text)
   (let $trimmed (str:trim $text)
     (case (catch (str:string->number $trimmed))
       (((Error $where (StrNotANumberV1 $at)) (if (notice:unbounded-word? (str:lower $trimmed)) none 15))
        ((Error $where $why) none)
        ($seconds (if (> $seconds 0) $seconds none))))))
(= (notice:unbounded-word? $word)
   (case $word
     (("inf" True) ("+inf" True) ("-inf" True) ("infinity" True) ("+infinity" True) ("-infinity" True)
      ("nan" True) ("+nan" True) ("-nan" True) ($other False))))
(= (notice:monotonic) (/ (system:monotonic-ns) 1000000000.0))

;; ------------------------------------------------------------ pacing memory

; Each kind of record has a space of its own, apart from the program's atoms:
; - a lookup by task stays constant-time, as it does not among the program's
;   own atoms;
; - a lookup of one record (the lane's last notice, the failure, the cursor)
;   does not pass over the remembered tasks;
; - removing a remembered task does not slow the next lookup of a seen task:
;   in one space, after a removal, a lookup of a whole record that finds
;   nothing passes over the space again.
!(bind! &notice-pending (new-space))
!(bind! &notice-seen (new-space))
!(bind! &notice-state (new-space))

; (first seen, submitted, lane) of a remembered task, or none.
(= (notice:pending $task)
   (case (collapse (match &notice-pending (NoticePendingV1 $task $since $submitted $lane) ($since $submitted $lane)))
     ((() none) ($entries (car-atom $entries)))))
(= (notice:remember $task $now $lane)
   (case (notice:pending $task)
     ((none (let $_ (add-atom &notice-pending (NoticePendingV1 $task $now False $lane)) ($now False $lane)))
      ($entry $entry))))
(= (notice:forget $task)
   (case (notice:pending $task)
     ((none ()) (($since $submitted $lane) (remove-atom &notice-pending (NoticePendingV1 $task $since $submitted $lane))))))
(= (notice:last $lane)
   (case (collapse (match &notice-state (NoticeLastV1 $lane $at) $at))
     ((() none) ($times (car-atom $times)))))
(= (notice:forget-last $lane)
   (case (notice:last $lane) ((none ()) ($at (remove-atom &notice-state (NoticeLastV1 $lane $at))))))
(= (notice:set-last $lane $now)
   (let $_ (notice:forget-last $lane) (add-atom &notice-state (NoticeLastV1 $lane $now))))
(= (notice:pending-count) (size-atom (collapse (match &notice-pending (NoticePendingV1 $task $since $submitted $lane) $task))))

; Whether a turn failure was unresolved at the last scan; before any scan,
; whether one is now.
(= (notice:failure?)
   (case (collapse (match &notice-state (NoticeFailureV1 $pending) $pending))
     ((() (health:failure-pending)) ($facts (car-atom $facts)))))
(= (notice:set-failure $pending)
   (let $_ (collapse (match &notice-state (NoticeFailureV1 $old) (remove-atom &notice-state (NoticeFailureV1 $old))))
     (add-atom &notice-state (NoticeFailureV1 $pending))))

;; ------------------------------------------------------------ the key

; A task or lane name: 1 to 128 characters.
(= (notice:name? $text)
   (and (== (get-type $text) String)
        (let $length (str:char-length $text) (and (>= $length 1) (<= $length 128)))))
; The update's id as text: a number as written, text as it is, "" otherwise.
(= (notice:update-id $update)
   (case (sf:get $update "update_id" JsonNull)
     (((JsonNumber $raw) $raw) ((JsonString $text) $text) ($other ""))))
; Whether the ledger has processed the update; an unanswered ledger is an Error.
(= (notice:processed $socket $update-id)
   (if (or (== $socket "") (== $update-id "")) False
     (case (catch (ledger-client:call $socket "committed" (($update-id))))
       ((True True) (true True) (False False) (false False)
        ((Error $where $why) (Error $where $why))
        ($other (Error notice:key (unexpected-answer committed $other)))))))

; The key under which the notice for $task is sent now, or "" when none is
; due. $update is the task's parsed update. A ledger that cannot answer gives
; an Error and leaves the decision to the next scan.
(= (notice:key $task $lane $kind $role $update)
   (notice:key-at (notice:config) (notice:monotonic) $task $lane $kind $role $update))
(= (notice:key-at (NoticeConfigV1 $socket $observed $instance $delay) $now $task $lane $kind $role $update)
   (if (not (and $observed (and (== $kind "message") (== $role "operator")))) ""
     (if (or (== $delay none) (notice:failure?)) ""
       (if (not (and (notice:name? $task) (notice:name? $lane))) ""
         (let $update-id (notice:update-id $update)
           (case (notice:processed $socket $update-id)
             ((True "")
              (False (notice:paced $socket $instance $delay $now $task $lane $update-id))
              ($error $error))))))))
(= (notice:paced $socket $instance $delay $now $task $lane $update-id)
   (let ($since $submitted $captured) (notice:remember $task $now $lane)
     (if (or $submitted (or (not (== $lane $captured)) (or (< (- $now $since) $delay) (notice:lane-recent? $lane $now)))) ""
       (notice:register $socket $task $update-id $lane (input-ledger:send-key $instance $lane $task)))))
(= (notice:lane-recent? $lane $now)
   (case (notice:last $lane) ((none False) ($at (< (- $now $at) 60)))))
; The ledger registers the notice unless the update is processed or another
; key holds the task; it answers the key, or "".
(= (notice:register $socket $task $update-id $lane $key)
   (if (== $socket "") $key
     (case (catch (ledger-client:call $socket "register" ($task $update-id $lane $key (system:wall-time))))
       (((Error $where $why) (Error $where $why))
        ($answer (if (or (== $answer "") (== $answer $key)) $answer
                   (Error notice:key (unexpected-answer register $answer))))))))

; The notice for $task was handed to the channel service: remembered here
; first, then recorded in the ledger. 1, or the ledger's Error.
(= (notice:submitted $task)
   (notice:submitted-at (notice:config) (notice:monotonic) $task))
(= (notice:submitted-at (NoticeConfigV1 $socket $observed $instance $delay) $now $task)
   (let $_ (case (notice:pending $task)
             ((none ())
              (($since $submitted $lane)
               (let* (($removed (remove-atom &notice-pending (NoticePendingV1 $task $since $submitted $lane)))
                      ($added (add-atom &notice-pending (NoticePendingV1 $task $since True $lane))))
                 (notice:set-last $lane $now)))))
     (if (== $socket "") 1
       (case (catch (ledger-client:call $socket "submitted" ($task)))
         ((True 1) (true 1)
          ((Error $where $why) (Error $where $why))
          ($other (Error notice:submitted (unexpected-answer submitted $other))))))))

;; ------------------------------------------------------------ the scan

; Where the next page of the scan starts: "" for a new scan, else the last
; task of the incomplete page before.
(= (notice:scan-after)
   (case (collapse (match &notice-state (NoticeScanV1 $after) $after))
     ((() "") ($cursors (car-atom $cursors)))))
(= (notice:set-scan $after)
   (let $_ (collapse (match &notice-state (NoticeScanV1 $old) (remove-atom &notice-state (NoticeScanV1 $old))))
     (if (== $after "") () (add-atom &notice-state (NoticeScanV1 $after)))))
(= (notice:forget-seen)
   (notice:remove-seen (collapse (match &notice-seen (NoticeSeenV1 $task) $task))))
(= (notice:remove-seen $tasks)
   (if (== $tasks ()) ()
     (let* (($task (car-atom $tasks)) ($_ (remove-atom &notice-seen (NoticeSeenV1 $task))))
       (notice:remove-seen (cdr-atom $tasks)))))
(= (notice:seen? $task)
   (not (== (collapse (match &notice-seen (NoticeSeenV1 $task) True)) ())))

; One page of tasks, each ($task $body): its tasks are seen; the answer is
; whether the page ends the scan (no "runtime-scan-unavailable" entry) and its
; last task.
(= (notice:see $tasks $complete $last)
   (if (== $tasks ()) (ScanPageV1 $complete $last)
     (case (car-atom $tasks)
       ((($task $body)
         (if (== $task "runtime-scan-unavailable") (notice:see (cdr-atom $tasks) False $last)
           (let $_ (if (notice:seen? $task) () (add-atom &notice-seen (NoticeSeenV1 $task)))
             (notice:see (cdr-atom $tasks) $complete $task))))
        ($other (notice:see (cdr-atom $tasks) $complete $last))))))

(= (notice:forget-unseen $tasks)
   (if (== $tasks ()) ()
     (let* (($task (car-atom $tasks)) ($_ (if (notice:seen? $task) () (notice:forget $task))))
       (notice:forget-unseen (cdr-atom $tasks)))))
(= (notice:forget-idle-lanes $lanes)
   (if (== $lanes ()) ()
     (let* (($lane (car-atom $lanes))
            ($held (collapse (match &notice-pending (NoticePendingV1 $task $since $submitted $lane) True)))
            ($_ (if (== $held ()) (notice:forget-last $lane) ())))
       (notice:forget-idle-lanes (cdr-atom $lanes)))))

; The responder observed one page of the service's pending inputs: $tasks is
; the page as fetched and $ordinary its conversational inputs, each entry
; ($task $body). It neither acknowledges them nor calls a model.
(= (notice:begin-scan $ordinary $tasks)
   (notice:begin-scan-at (system:wall-time) $ordinary $tasks))
(= (notice:begin-scan-at $wall $ordinary $tasks)
   (let* (($after (notice:scan-after))
          ($_ (let $pending (health:failure-pending) (notice:set-failure $pending)))
          ($_ (if (== $after "") (notice:forget-seen) ()))
          ((ScanPageV1 $complete $last) (notice:see $tasks True ""))
          ($_ (notice:set-scan (if $complete "" $last)))
          ($_ (notice:set-view (if (and $complete (== $after "")) (notice:view $ordinary $wall) JsonNull)))
          ($_ (if $complete
                (notice:forget-unseen (collapse (match &notice-pending (NoticePendingV1 $task $since $submitted $lane) $task)))
                ()))
          ($_ (notice:forget-idle-lanes (collapse (match &notice-state (NoticeLastV1 $lane $at) $lane)))))
     1))

;; ------------------------------------------------------------ the inbox view

; The view of the last scan: a JSON object, or JsonNull when there is none.
(= (notice:input-view)
   (case (collapse (match &notice-state (NoticeViewV1 $view) $view))
     ((() JsonNull) ($views (car-atom $views)))))
(= (notice:set-view $view)
   (let $_ (collapse (match &notice-state (NoticeViewV1 $old) (remove-atom &notice-state (NoticeViewV1 $old))))
     (if (== $view JsonNull) () (add-atom &notice-state (NoticeViewV1 $view)))))

(= (notice:view $ordinary $wall) (notice:view-fold $ordinary 0 True none none $wall))
; $dated: every counted message carries a usable date; $oldest and $newest
; are (value raw) of the first smallest and the first largest date.
(= (notice:view-fold $tasks $count $dated $oldest $newest $wall)
   (if (== $tasks ())
     (let $known (and $dated (> $count 0))
       (JsonObject ((JsonPair "pending" (JsonNumber (repr $count)))
                    (JsonPair "newestMessageAt" (if $known (notice:raw $newest) JsonNull))
                    (JsonPair "oldestMessageAt" (if $known (notice:raw $oldest) JsonNull))
                    (JsonPair "observedAt" (JsonNumber (repr $wall))))))
     (case (notice:task-fact (car-atom $tasks))
       ((unknown JsonNull)
        (other (notice:view-fold (cdr-atom $tasks) $count $dated $oldest $newest $wall))
        (undated (notice:view-fold (cdr-atom $tasks) (+ $count 1) False $oldest $newest $wall))
        ((dated $value $raw)
         (notice:view-fold (cdr-atom $tasks) (+ $count 1) $dated
           (case $oldest ((none ($value $raw)) (($least $_) (if (< $value $least) ($value $raw) $oldest))))
           (case $newest ((none ($value $raw)) (($most $_) (if (> $value $most) ($value $raw) $newest))))
           $wall))))))
(= (notice:raw ($value $raw)) (JsonNumber $raw))

; What one task says about the inbox: an operator message with a date, one
; without, another known kind of task, or something unreadable.
(= (notice:task-fact $entry)
   (case $entry
     ((($task $body)
       (if (== $task "runtime-scan-unavailable") unknown
         (case (catch (json-parse $body))
           (((JsonArray $items) (if (== $items ()) unknown (notice:value-fact (car-atom $items) $items)))
            ($other unknown)))))
      ($other unknown))))
(= (notice:value-fact $head $items)
   (case $head
     (((JsonString "input")
       (case $items
         ((($_ $lane (JsonString "message") (JsonString "operator") $update) (notice:update-fact $update))
          (($_ $lane $kind $role $update) other)
          ($other unknown))))
      ((JsonString "command") other) ((JsonString "callback") other) ((JsonString "delivery") other)
      ((JsonString "rejected") other) ((JsonString "released") other)
      ($other unknown))))
; The message is the update's "message", or its "edited_message" when that is
; absent or empty.
(= (notice:update-fact $update)
   (case $update
     (((JsonObject $pairs)
       (let $first (sf:get-last $pairs "message" JsonNull)
         (notice:message-fact (if (notice:present? $first) $first (sf:get-last $pairs "edited_message" JsonNull)))))
      ($other unknown))))
(= (notice:message-fact $message)
   (case $message
     (((JsonObject $pairs)
       (case (sf:get-last $pairs "date" JsonNull)
         (((JsonNumber $raw) (notice:stamp $raw)) ($other undated))))
      ($other undated))))
; A date is a number above 0 and at most 10^12.
(= (notice:stamp $raw)
   (case (catch (str:string->number $raw))
     (((Error $where $why) undated)
      ($value (if (and (> $value 0) (<= $value 1000000000000)) (dated $value $raw) undated)))))
(= (notice:present? $value)
   (case $value
     ((JsonNull False) ((JsonBool false) False) ((JsonString "") False) ((JsonArray ()) False) ((JsonObject ()) False)
      ((JsonNumber $raw) (case (catch (str:string->number $raw)) (((Error $where $why) True) ($number (not (== $number 0))))))
      ($other True))))

;; ------------------------------------------------------------ the wait

; The pause between two scans: two seconds, on a timer of its own (sleep), so
; other clients' completions stay theirs. It stands in for a wake on a change
; of the service's directories, which needs a directory-change handle in the
; engine; the bound is the one the Python change watch kept as its fallback.
(= (notice:wait) (sleep 2))
