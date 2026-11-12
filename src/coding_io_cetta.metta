; Coding-session files, shared by the agent (coding_work.metta) and the
; session's worker (coding_main.metta, coding_session.metta).
;
; Every read-modify-write of runtime.json or state.json happens under the
; session folder's .ending.lock (flock, so it also excludes any older worker
; still running a Python runtime). Every state file is published whole: a new
; file beside it, written and synced, renamed over the old one, and the folder
; synced. A reader sees the old file or the new one, never a mixture.
;
; Run tokens: a launch reserves a fresh token in runtime.json (phase
; "reserved"). The worker consumes it under the lock before any other work
; (phase "started"); a token is consumed once, and only while it is the newest
; reservation. Worker writes are conditional on owning the started run.
; Endings are recorded once: settling marks only a run that is still recorded
; as running by the owner the caller observed.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self ./src/coding_values.metta)
!(import! &self ./src/process_identity_cetta.metta)

;; ------------------------------------------------------------- values

(= (cio:failed? $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) True) ($other False))))
(= (cio:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (cio:last (cdr-atom $items))))
(= (cio:basename $path) (cio:last (str:split "/" $path)))
(= (cio:now) (system:wall-time))
(= (cio:now-json) (JsonNumber (repr (cio:now))))
(= (cio:clip $text $limit)
   (if (<= (str:char-length $text) $limit) $text (str:concat (str:char-slice $text 0 $limit) " …")))

; A JSON object file, or the empty object when it is missing or is not an
; object. Parse failures are values, not errors.
(= (cio:json $path)
   (if (fs:is-file $path)
     (case (catch (json-parse (fs:read-text $path)))
       (((JsonObject $pairs) (JsonObject $pairs))
        ($other (JsonObject ()))))
     (JsonObject ())))
(= (cio:put-pairs $pairs $key $value)
   (if (== $pairs ()) ((JsonPair $key $value))
     (let (JsonPair $name $old) (car-atom $pairs)
       (if (== $name $key) (cons-atom (JsonPair $key $value) (cdr-atom $pairs))
         (cons-atom (JsonPair $name $old) (cio:put-pairs (cdr-atom $pairs) $key $value))))))
(= (cio:put $object $key $value)
   (case $object
     (((JsonObject $pairs) (JsonObject (cio:put-pairs $pairs $key $value)))
      ($other (JsonObject ((JsonPair $key $value)))))))
; A recorded count: a JSON integer, otherwise zero.
(= (cio:count $value)
   (case $value
     (((JsonNumber $text) (if (str:is-all-in digit $text) (JsonNumber $text) (JsonNumber "0")))
      ($other (JsonNumber "0")))))
; A field as the log shows it: its text, or null when absent.
(= (cio:shown $value)
   (case $value (((JsonString $text) $text) ((JsonNumber $text) $text) (JsonNull "null") ($other (repr $other)))))

;; ------------------------------------------------------- publication

; Publish text at path whole. True; a failure before the rename (the old file
; stands); or (CioUncertain failure) when the rename happened but syncing the
; folder failed, so durability of the new file is unconfirmed.
(= (cio:publish $path $text)
   (let* (($dir (path:dirname $path))
          ($made (fs:make-directories $dir 504))
          ($temporary (path:join $dir (str:join "" ("." (cio:basename $path) "-" (system:uuid4-hex)))))
          ($writer (fs:open-write $temporary ("create" "exclusive") 432)))
     (if (cio:failed? $writer) $writer
       (let* (($moded (fs:set-mode $writer 432))
              ($wrote (fs:write-all $writer $text))
              ($synced (if (cio:failed? $wrote) $wrote (fs:sync $writer)))
              ($closed (fs:close $writer)))
         (if (cio:failed? $synced) (let $_ (fs:remove $temporary) $synced)
           (let $renamed (fs:rename $temporary $path)
             (if (cio:failed? $renamed) (let $_ (fs:remove $temporary) $renamed)
               (let $flushed (cio:sync-directory $dir)
                 (if (== $flushed True) True (CioUncertain $flushed))))))))))
(= (cio:sync-directory $dir)
   (let $reader (fs:open-read $dir)
     (if (cio:failed? $reader) $reader
       (let* (($synced (fs:sync $reader)) ($_ (fs:close $reader)))
         (if (cio:failed? $synced) $synced True)))))
(= (cio:publish-json $path $value) (cio:publish $path (json-stringify $value)))

; One line appended to a file; the line is written with one append call.
(= (cio:append-line $path $text)
   (let $_ (fs:append-text $path (str:concat $text "\n")) 1))
; A diagnostic for the session's run.log. It never fails its caller.
(= (cio:log $dir $text)
   (let $_ (catch (fs:append-text (path:join $dir "run.log") (str:concat $text "\n"))) 0))

;; -------------------------------------------------------------- lock

; Run body under the session's .ending.lock; the lock is released whatever
; the body answers. The body's first answer is the result.
(: cio:locked (-> %Undefined% Atom %Undefined%))
(= (cio:locked $dir $body)
   (let* (($made (fs:make-directories $dir 504))
          ($lock (fs:open-write (path:join $dir ".ending.lock") ("create" "append") 432)))
     (if (cio:failed? $lock) (CioLockFailed $lock)
       (let* (($held (fs:lock $lock exclusive))
              ($result (collapse (catch (eval $body))))
              ($released (fs:unlock $lock))
              ($closed (fs:close $lock)))
         (if (== $result ()) (CioNoAnswer) (car-atom $result))))))

;; -------------------------------------------------------- run tokens

(= (cio:canonical $path)
   (case (fs:try-canonical-path $path) (((FS:CanonicalPath $real) $real) ($fault $path))))
(= (cio:refuse $dir $why) (let $_ (cio:log $dir $why) 0))

; The worker's claim: consume the single-use reserved token, at most once and
; only from the bundle the reservation names. 1 when this worker owns the run.
(= (cio:consume-run $dir $token $owner $root)
   (cio:locked $dir (cio:consume-run-locked $dir $token $owner $root)))
(= (cio:consume-run-locked $dir $token $owner $root)
   (let* (($runtime-path (path:join $dir "runtime.json"))
          ($runtime (cio:json $runtime-path))
          ($run (coding:field $runtime "run"))
          ($phase (coding:field $runtime "phase"))
          ($pinned (coding:text (coding:field $runtime "root"))))
     (if (or (== $token "") (or (!= $run (JsonString $token)) (!= $phase (JsonString "reserved"))))
       (cio:refuse $dir (format-args "refused run token {}: manifest run={} phase={}"
                          ((if (== $token "") "<empty>" $token) (cio:shown $run) (cio:shown $phase))))
       (if (and (!= $pinned "") (!= (cio:canonical $pinned) (cio:canonical $root)))
         (cio:refuse $dir (format-args "refused run token {}: bundle mismatch manifest root={} worker root={}"
                            ($token $pinned $root)))
         (let* (($prior (cio:json (path:join $dir "state.json")))
                ($state (JsonObject ((JsonPair "status" (JsonString "running"))
                                     (JsonPair "steps" (cio:count (coding:field $prior "steps")))
                                     (JsonPair "progress" (JsonString "started"))
                                     (JsonPair "delivery" (JsonString "none"))
                                     (JsonPair "delivery_version" (JsonNumber "2"))
                                     (JsonPair "worker_owner" (JsonString $owner))
                                     (JsonPair "run" (JsonString $token))
                                     (JsonPair "inbox" (cio:count (coding:field $prior "inbox")))
                                     (JsonPair "updated" (cio:now-json)))))
                ($started (cio:publish-json $runtime-path (cio:put $runtime "phase" (JsonString "started"))))
                ($published (if (== $started True) (cio:publish-json (path:join $dir "state.json") $state) $started)))
           (if (== $published True) 1
             (cio:refuse $dir (format-args "uncertain publication for run token {}: {}" ($token (repr $published))))))))))

; A worker's progress or ending, written only while this worker owns the
; started run. A terminal status also ends the run in runtime.json. Report is
; the report text, or NoReport. 1 when written.
(= (cio:worker-state $dir $token $owner $status $steps $progress $inbox $delivery-id $report)
   (cio:locked $dir (cio:worker-state-locked $dir $token $owner $status $steps $progress $inbox $delivery-id $report)))
(= (cio:worker-state-locked $dir $token $owner $status $steps $progress $inbox $delivery-id $report)
   (let* (($runtime-path (path:join $dir "runtime.json"))
          ($pinned (fs:is-file $runtime-path))
          ($runtime (cio:json $runtime-path))
          ($state (cio:json (path:join $dir "state.json"))))
     (if (and (or $pinned (!= $token ""))
              (or (== $token "")
                  (or (!= (coding:field $runtime "run") (JsonString $token))
                      (or (!= (coding:field $runtime "phase") (JsonString "started"))
                          (or (!= (coding:field $state "status") (JsonString "running"))
                              (!= (coding:text (coding:field $state "worker_owner")) $owner))))))
       0
       (let* (($running (== $status "running"))
              ($updated (cio:now-json))
              ($base ((JsonPair "status" (JsonString $status)) (JsonPair "steps" (JsonNumber (repr $steps)))
                      (JsonPair "progress" (JsonString $progress))
                      (JsonPair "delivery" (JsonString (if $running "none" "pending")))
                      (JsonPair "delivery_version" (JsonNumber "2")) (JsonPair "worker_owner" (JsonString $owner))
                      (JsonPair "run" (JsonString $token)) (JsonPair "inbox" (JsonNumber (repr $inbox)))
                      (JsonPair "updated" $updated)))
              ($identity (if $running ""
                           (if (!= $delivery-id "") $delivery-id
                             (cio:ending-identity $dir $status $updated (JsonNumber (repr $steps))))))
              ($next (if $running (JsonObject $base)
                       (JsonObject (cio:append-pair $base (JsonPair "delivery_id" (JsonString $identity))))))
              ($reported (if (== $report NoReport) True (cio:publish (path:join $dir "report.txt") $report)))
              ($written (if (== $reported True) (cio:publish-json (path:join $dir "state.json") $next) $reported))
              ($ended (if (and (== $written True) (and (not $running) (and $pinned (!= $runtime (JsonObject ())))))
                        (cio:publish-json $runtime-path (cio:put $runtime "phase" (JsonString "ended")))
                        $written)))
         (if (and (== $written True) (== $ended True)) 1 0)))))
(= (cio:append-pair $pairs $pair)
   (if (== $pairs ()) ($pair) (cons-atom (car-atom $pairs) (cio:append-pair (cdr-atom $pairs) $pair))))

; One identity per recorded ending: the session, the ending, and the time and
; step count it was recorded with.
(= (cio:ending-identity $dir $status $updated $steps)
   (str:char-slice
     (str:sha256 (json-stringify (JsonArray ((JsonString (cio:basename $dir)) (JsonString $status) (JsonString "")
                                             $updated $steps))))
     0 32))

;; ----------------------------------------------------------- endings

; work-stop: the stop flag, and a reservation not yet consumed is revoked.
(= (cio:stop-session $dir)
   (cio:locked $dir (cio:stop-session-locked $dir)))
(= (cio:stop-session-locked $dir)
   (let* (($flag (fs:write (path:join $dir "stop") ""))
          ($runtime-path (path:join $dir "runtime.json"))
          ($runtime (cio:json $runtime-path))
          ($_ (if (== (coding:field $runtime "phase") (JsonString "reserved"))
                (cio:publish-json $runtime-path (cio:put $runtime "phase" (JsonString "ended")))
                True)))
     1))

; Record the ending of a run whose worker can no longer record it: stopped
; when the stop flag is set, otherwise interrupted. Only a run still recorded
; as running, by the expected owner (Observed when the caller saw none), is
; marked; with RequireDead, only when that owner is gone. The ending (its
; status) or "" when nothing was marked.
(= (cio:settle-ending $dir) (cio:ending $dir (cio:locked $dir (cio:settle-ending-locked $dir Observed Alive))))
(= (cio:settle-ending $dir $owner) (cio:ending $dir (cio:locked $dir (cio:settle-ending-locked $dir $owner Alive))))
; Only an ending is an ending: a failure to take the lock is logged, and
; nothing was marked.
(= (cio:ending $dir $result)
   (if (or (== $result "") (or (== $result "stopped") (== $result "interrupted"))) $result
     (let $_ (cio:log $dir (format-args "settle error: {}" ((repr $result)))) "")))
(= (cio:settle-ending-locked $dir $expected $liveness)
   (let* (($state (cio:json (path:join $dir "state.json")))
          ($owner (coding:text (coding:field $state "worker_owner")))
          ($expect (if (== $expected Observed) $owner $expected)))
     (if (or (!= (coding:field $state "status") (JsonString "running")) (!= $owner $expect)) ""
       (if (and (== $liveness RequireDead) (process:alive? $owner)) ""
         (let* (($ending (if (fs:is-file (path:join $dir "stop")) "stopped" "interrupted"))
                ($why (if (== $ending "stopped") "Stopped by the requester."
                        "The session ended without finishing its recorded ending: its process is gone (it was stopped, killed or crashed)."))
                ($progress (coding:text (coding:field $state "progress")))
                ($plan-path (path:join $dir "plan.json"))
                ($plan (if (fs:is-file $plan-path) (cio:clip (fs:read-text $plan-path) 2000) "none recorded"))
                ($report-path (path:join $dir "report.txt"))
                ($saved (if (fs:is-file $report-path) (fs:read-text $report-path) ""))
                ($report (str:join "" ($why "\n\nLast progress:\n" (if (== $progress "") "none recorded" $progress)
                                       (if (== $saved "") "" (str:concat "\n\nSaved report:\n" $saved))
                                       "\n\nPlan:\n" $plan
                                       "\n\nResume without replaying uncertain effects:\n(work-resume \""
                                       (cio:basename $dir) "\")")))
                ($identity (cio:ending-identity $dir $ending (coding:field $state "updated") (coding:field $state "steps"))))
           (if (== (cio:mark-state-locked $dir $ending $report $identity $expect) 1) $ending ""))))))

; Mark the run ending conditionally: the state still says running, names the
; expected owner, and the manifest (if it has a phase) is the started run.
(= (cio:mark-state-locked $dir $status $report $identity $expected)
   (let* (($state-path (path:join $dir "state.json"))
          ($runtime-path (path:join $dir "runtime.json"))
          ($state (cio:json $state-path))
          ($runtime (cio:json $runtime-path))
          ($run (coding:field $state "run"))
          ($phase (coding:field $runtime "phase")))
     (if (or (!= (coding:field $state "status") (JsonString "running"))
             (or (!= (coding:text (coding:field $state "worker_owner")) $expected)
                 (and (!= $phase JsonNull)
                      (or (!= $phase (JsonString "started"))
                          (and (!= (coding:text $run) "") (!= (coding:field $runtime "run") $run))))))
       0
       (let* (($marked (cio:put (cio:put (cio:put (cio:put (cio:put (cio:put $state
                          "status" (JsonString $status)) "delivery" (JsonString "pending"))
                          "updated" (cio:now-json)) "progress" (JsonString $report))
                          "delivery_version" (JsonNumber "2")) "delivery_id" (JsonString $identity)))
              ($reported (cio:publish (path:join $dir "report.txt") $report))
              ($written (if (== $reported True) (cio:publish-json $state-path $marked) $reported))
              ($_ (if (and (== $written True) (and (fs:is-file $runtime-path) (!= $runtime (JsonObject ()))))
                    (if (or (== (coding:text $run) "") (== (coding:field $runtime "run") $run))
                      (cio:publish-json $runtime-path (cio:put $runtime "phase" (JsonString "ended"))) True)
                    True)))
         (if (== $written True) 1 0)))))

;; -------------------------------------------------------- executions
; A command the worker runs is recorded before it starts and again when it
; ends. The record is evidence, never permission to run the command again.

(= (cio:execution-path $dir $id $suffix)
   (path:join (path:join $dir "executions") (str:concat $id $suffix)))
(= (cio:execution-id? $id)
   (and (== (str:char-length $id) 32) (and (str:is-all-in xdigit $id) (== (str:lower $id) $id))))

; The log a new command writes to: (ExecutionLog path), or (ExecutionRefused
; why). A checkpoint check (METTACLAW_CODING_CHECK=1) never starts commands,
; and an identity is used once.
(= (cio:execution-create $dir $id $command)
   (if (== (system:getenv-or-default "METTACLAW_CODING_CHECK" "") "1")
     (ExecutionRefused "commands are refused while a checkpoint is checked")
     (if (not (cio:execution-id? $id)) (ExecutionRefused "invalid execution identity")
       (let* (($record (cio:execution-path $dir $id ".json"))
              ($_ (fs:make-directories (path:dirname $record) 504))
              ($writer (fs:open-write $record ("create" "exclusive") 432)))
         (if (cio:failed? $writer) (ExecutionRefused (repr $writer))
           (let* (($wrote (fs:write-all $writer (json-stringify (JsonObject (
                            (JsonPair "id" (JsonString $id)) (JsonPair "command" (JsonString $command))
                            (JsonPair "status" (JsonString "dispatching")) (JsonPair "started" (cio:now-json)))))))
                  ($_ (fs:close $writer)))
             (if (cio:failed? $wrote) (ExecutionRefused (repr $wrote))
               (ExecutionLog (cio:execution-path $dir $id ".log")))))))))

; Record how a command ended, then answer its result.
(= (cio:execution-finish $dir $id $status $code $seconds)
   (let* (($path (cio:execution-path $dir $id ".json"))
          ($record (cio:json $path))
          ($_ (if (== $record (JsonObject ())) ()
                (cio:publish-json $path
                  (cio:put (cio:put (cio:put (cio:put $record
                    "status" (JsonString $status)) "code" (JsonNumber (repr $code)))
                    "seconds" (JsonNumber (repr $seconds))) "ended" (cio:now-json))))))
     (cio:execution-result $dir $id)))

; A command's result as the model reads it: exit code, wall time, where the
; whole output is, and the output, keeping its first and last 20000 bytes
; when it is longer than 40000. A record with no recorded ending was not run
; to its end by this runtime, and is said to be uncertain.
(= (cio:execution-result $dir $id)
   (let $record (if (cio:execution-id? $id) (cio:json (cio:execution-path $dir $id ".json")) (JsonObject ()))
     (case (coding:field $record "status")
       (((JsonString $status)
         (let* (($log (cio:execution-path $dir $id ".log"))
                ($outcome (if (== $status "exited") (cio:shown-or (coding:field $record "code") "?")
                            (if (or (== $status "dispatching") (== $status "running"))
                              "uncertain: this runtime does not own the execution; it was not replayed" $status))))
           (str:join "" ("Exit code: " $outcome "\nWall time: " (cio:shown-or (coding:field $record "seconds") "?")
                         " seconds\nExecution ID: " $id "\nOutput artifact: " $log "\nOutput:\n" (cio:log-excerpt $log)))))
        ($missing "Unknown execution ID; nothing was started.")))))
(= (cio:shown-or $value $default)
   (case $value (((JsonNumber $text) $text) ((JsonString $text) $text) ($other $default))))

; The output, bounded: whole up to 40000 bytes, otherwise its first and last
; 20000 bytes. Only the bytes shown are read, whatever the log's size.
(= (cio:log-excerpt $log)
   (case (fs:metadata $log follow)
     (((fs:info regular $size $identity)
       (if (<= $size 40000)
         (case (fs:read-text-result $log 0 "replace") ((("ok" $text) $text) ($other "")))
         (str:join "" ((cio:head-text $log 20000) "\n... tokens truncated; full output in artifact ...\n"
                       (case (fs:tail-text $log 20000) (((fs:tail $total $cut $text) $text) ($other "")))))))
      ($other ""))))
(= (cio:head-text $path $limit)
   (let $reader (fs:open-read $path)
     (if (cio:failed? $reader) ""
       (let* (($bytes (cio:read-up-to $reader $limit ()))
              ($_ (fs:close $reader))
              ($decoder (str:utf8-new ignore)))
         (case (str:utf8-feed $decoder $bytes True)
           (((Utf8DecodedV1 $text $next) $text) ($other "")))))))
(= (cio:read-up-to $reader $left $chunks)
   (if (<= $left 0) (str:join "" (cio:reverse $chunks ()))
     (case (fs:read-chunk $reader $left)
       (((fs:chunk $bytes $offset)
         (let $count (str:byte-length $bytes)
           (if (== $count 0) (str:join "" (cio:reverse $chunks ()))
             (cio:read-up-to $reader (- $left $count) (cons-atom $bytes $chunks)))))
        ($end (str:join "" (cio:reverse $chunks ())))))))
(= (cio:reverse $items $out)
   (if (== $items ()) $out (cio:reverse (cdr-atom $items) (cons-atom (car-atom $items) $out))))
