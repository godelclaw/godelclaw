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
!(import! &self (library math))
!(import! &self ./src/coding_values.metta)
!(import! &self ./src/state_file_cetta.metta)
!(import! &self ./src/process_identity_cetta.metta)

;; ------------------------------------------------------------- values

(= (cio:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (cio:last (cdr-atom $items))))
(= (cio:basename $path) (cio:last (str:split "/" $path)))
(= (cio:now) (system:wall-time))
(= (cio:now-json) (JsonNumber (repr (cio:now))))
(= (cio:clip $text $limit)
   (if (<= (str:char-length $text) $limit) $text (str:concat (str:char-slice $text 0 $limit) " …")))

; A recorded count: a JSON integer, otherwise zero.
(= (cio:count $value)
   (case $value
     (((JsonNumber $text)
       (let $digits (if (str:starts-with $text "-") (str:char-slice $text 1 (str:char-length $text)) $text)
         (if (and (!= $digits "") (str:is-all-in digit $digits)) (JsonNumber $text) (JsonNumber "0"))))
      ($other (JsonNumber "0")))))
; A field as the log shows it: its text, or null when absent.
(= (cio:shown $value)
   (case $value (((JsonString $text) $text) ((JsonNumber $text) $text) (JsonNull "null") ($other (repr $other)))))

;; ------------------------------------------------------- publication

; Publish text at path whole. True; a failure before the rename (the old file
; stands); or (SfUncertain failure) when the rename happened but syncing the
; folder failed, so durability of the new file is unconfirmed.
(= (cio:publish $path $text)
   (let $made (fs:make-directories (path:dirname $path) 504)
     (sf:replace $path $text 432 True)))
(= (cio:publish-json $path $value) (cio:publish $path (json-stringify $value)))

; One line appended to a file, written with one append call: 1, or the failure.
(= (cio:append-line $path $text)
   (case (catch (fs:append-text $path (str:concat $text "\n"))) ((() 1) ($failure $failure))))
; A diagnostic for the session's run.log. It never fails its caller.
(= (cio:log $dir $text)
   (let $_ (catch (fs:append-text (path:join $dir "run.log") (str:concat $text "\n"))) 0))

;; -------------------------------------------------------------- lock

; Run body under the session's .ending.lock; the lock is released whatever
; the body answers. The body's first answer is the result.
(: cio:locked (-> %Undefined% Atom %Undefined%))
(= (cio:locked $dir $body)
   (let $made (fs:make-directories $dir 504)
     (sf:locked (path:join $dir ".ending.lock") 432 $body)))

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
          ($runtime (sf:object $runtime-path))
          ($run (coding:field $runtime "run"))
          ($phase (coding:field $runtime "phase"))
          ($pinned (coding:text (coding:field $runtime "root"))))
     (if (or (== $token "") (or (!= $run (JsonString $token)) (!= $phase (JsonString "reserved"))))
       (cio:refuse $dir (format-args "refused run token {}: manifest run={} phase={}"
                          ((if (== $token "") "<empty>" $token) (cio:shown $run) (cio:shown $phase))))
       (if (and (!= $pinned "") (!= (cio:canonical $pinned) (cio:canonical $root)))
         (cio:refuse $dir (format-args "refused run token {}: bundle mismatch manifest root={} worker root={}"
                            ($token $pinned $root)))
         (let* (($prior (sf:object (path:join $dir "state.json")))
                ($state (JsonObject ((JsonPair "status" (JsonString "running"))
                                     (JsonPair "steps" (cio:count (coding:field $prior "steps")))
                                     (JsonPair "progress" (JsonString "started"))
                                     (JsonPair "delivery" (JsonString "none"))
                                     (JsonPair "delivery_version" (JsonNumber "2"))
                                     (JsonPair "worker_owner" (JsonString $owner))
                                     (JsonPair "run" (JsonString $token))
                                     (JsonPair "inbox" (cio:count (coding:field $prior "inbox")))
                                     (JsonPair "updated" (cio:now-json)))))
                ($started (cio:publish-json $runtime-path (sf:put $runtime "phase" (JsonString "started"))))
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
          ($runtime (sf:object $runtime-path))
          ($state (sf:object (path:join $dir "state.json"))))
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
                        (cio:publish-json $runtime-path (sf:put $runtime "phase" (JsonString "ended")))
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
          ($runtime (sf:object $runtime-path))
          ($_ (if (== (coding:field $runtime "phase") (JsonString "reserved"))
                (cio:publish-json $runtime-path (sf:put $runtime "phase" (JsonString "ended")))
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
   (let* (($state (sf:object (path:join $dir "state.json")))
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
          ($state (sf:object $state-path))
          ($runtime (sf:object $runtime-path))
          ($run (coding:field $state "run"))
          ($phase (coding:field $runtime "phase")))
     (if (or (!= (coding:field $state "status") (JsonString "running"))
             (or (!= (coding:text (coding:field $state "worker_owner")) $expected)
                 (and (!= $phase JsonNull)
                      (or (!= $phase (JsonString "started"))
                          (and (!= (coding:text $run) "") (!= (coding:field $runtime "run") $run))))))
       0
       (let* (($marked (sf:put (sf:put (sf:put (sf:put (sf:put (sf:put $state
                          "status" (JsonString $status)) "delivery" (JsonString "pending"))
                          "updated" (cio:now-json)) "progress" (JsonString $report))
                          "delivery_version" (JsonNumber "2")) "delivery_id" (JsonString $identity)))
              ($reported (cio:publish (path:join $dir "report.txt") $report))
              ($written (if (== $reported True) (cio:publish-json $state-path $marked) $reported))
              ($_ (if (and (== $written True) (and (fs:is-file $runtime-path) (!= $runtime (JsonObject ()))))
                    (if (or (== (coding:text $run) "") (== (coding:field $runtime "run") $run))
                      (cio:publish-json $runtime-path (sf:put $runtime "phase" (JsonString "ended"))) True)
                    True)))
         (if (== $written True) 1
           (cio:refuse $dir (format-args "settle error: could not record the {} ending: {}" ($status (repr $written)))))))))

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
         (if (sf:failed? $writer) (ExecutionRefused (repr $writer))
           (let* (($wrote (fs:write-all $writer (json-stringify (JsonObject (
                            (JsonPair "id" (JsonString $id)) (JsonPair "command" (JsonString $command))
                            (JsonPair "status" (JsonString "dispatching")) (JsonPair "started" (cio:now-json)))))))
                  ($_ (fs:close $writer)))
             (if (sf:failed? $wrote) (ExecutionRefused (repr $wrote))
               (ExecutionLog (cio:execution-path $dir $id ".log")))))))))

; Record how a command ended, then answer its result.
(= (cio:execution-finish $dir $id $status $code $seconds)
   (let* (($path (cio:execution-path $dir $id ".json"))
          ($record (sf:object $path))
          ($_ (if (== $record (JsonObject ())) ()
                (cio:publish-json $path
                  (sf:put (sf:put (sf:put (sf:put $record
                    "status" (JsonString $status)) "code" (JsonNumber (repr $code)))
                    "seconds" (JsonNumber (repr $seconds))) "ended" (cio:now-json))))))
     (cio:execution-result $dir $id)))

; A command's result as the model reads it: exit code, wall time, where the
; whole output is, and the output, keeping its first and last 20000 bytes
; when it is longer than 40000. A record with no recorded ending was not run
; to its end by this runtime, and is said to be uncertain.
(= (cio:execution-result $dir $id)
   (let $record (if (cio:execution-id? $id) (sf:object (cio:execution-path $dir $id ".json")) (JsonObject ()))
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
; Malformed bytes read as U+FFFD, one per maximal subpart, in all parts.
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
     (if (sf:failed? $reader) ""
       (let* (($bytes (cio:read-up-to $reader $limit ()))
              ($_ (fs:close $reader))
              ($decoder (cio:replacing-decoder)))
         (case (str:utf8-feed $decoder $bytes True)
           (((Utf8DecodedV1 $text $next) $text) ($other "")))))))
; U+FFFD for malformed bytes; an engine without the replace policy drops
; them instead, rather than losing the head of the output.
(= (cio:replacing-decoder)
   (let $decoder (str:utf8-new replace)
     (case $decoder (((Utf8DecoderV1 $policy $tail $seen) $decoder) ($refused (str:utf8-new ignore))))))
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

;; ---------------------------------------------------------- freezing
; A session runs on a frozen copy of the runtime files the release selects
; (config/runtime-files.json), stored under the digest of their contents in
; memory/coding-runtimes/, so every resume runs the program the session was
; pinned to. The digest is that of the canonical JSON object mapping each
; selected name to its file's SHA-256, as earlier runtimes computed it, so
; existing pins and cached runtimes keep their names.

(= (cio:append-list $left $right)
   (if (== $left ()) $right (cons-atom (car-atom $left) (cio:append-list (cdr-atom $left) $right))))
(= (cio:replace $text $old $new) (str:join $new (str:split $old $text)))

; The program root: the given root (the agent's working directory) when it
; holds a runtime selection, as an agent's and a worker's always does; else
; METTACLAW_RUNTIME_ROOT, for a caller elsewhere; else the nearest ancestor
; of the entry program's directory that holds one.
(= (cio:program-root $root)
   (if (fs:is-file (path:join $root "config/runtime-files.json")) $root
     (let $named (system:getenv-or-default "METTACLAW_RUNTIME_ROOT" "")
       (if (and (!= $named "") (fs:is-file (path:join $named "config/runtime-files.json"))) $named
         (cio:selection-ancestor (system:entry-directory))))))
(= (cio:selection-ancestor $dir)
   (if (fs:is-file (path:join $dir "config/runtime-files.json")) $dir
     (let $parent (path:dirname $dir)
       (if (== $parent $dir) $dir (cio:selection-ancestor $parent)))))

(= (cio:selection $root)
   (case (cio:json-any (path:join $root "config/runtime-files.json"))
     (((JsonArray $items) (cio:selected-names $items ()))
      ((JsonObject $pairs) (case (coding:field (JsonObject $pairs) "files")
                             (((JsonArray $items) (cio:selected-names $items ()))
                              ($other (CioFreezeFailed "the runtime selection has no file list")))))
      ($other (CioFreezeFailed "the runtime selection is unreadable")))))
(= (cio:json-any $path)
   (if (fs:is-file $path) (catch (json-parse (fs:read-text $path))) JsonNull))
(= (cio:selected-names $items $names)
   (if (== $items ()) (sort-strings $names)
     (case (car-atom $items)
       (((JsonString $name)
         (if (or (str:starts-with $name "/") (cio:member ".." (str:split "/" $name)))
           (CioFreezeFailed "runtime selection must be relative")
           (cio:selected-names (cdr-atom $items) (cons-atom $name $names))))
        ($other (CioFreezeFailed "runtime selection names must be text"))))))
(= (cio:member $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (cio:member $item (cdr-atom $items)))))

(= (cio:file-sha256 $path)
   (case (fs:sha256-file $path) (((fs:hashed-v1 $hex $info) $hex) ($other (CioFreezeFailed (repr $other))))))
; (name: hex, ...) as Python's json.dumps(..., sort_keys=True) renders it.
(= (cio:digest-entries $root $names $entries)
   (if (== $names ()) (cio:reverse $entries ())
     (let* (($name (car-atom $names)) ($hex (cio:file-sha256 (path:join $root $name))))
       (if (!= (get-metatype $hex) Grounded) $hex
         (cio:digest-entries $root (cdr-atom $names)
           (cons-atom (str:join "" ((json-stringify (JsonString $name)) ": \"" $hex "\"")) $entries))))))
(= (cio:bundle-digest $root $names)
   (case (cio:digest-entries $root $names ())
     (((CioFreezeFailed $why) (CioFreezeFailed $why))
      ($entries (str:sha256 (str:join "" ("{" (str:join ", " $entries) "}")))))))

; (FrozenBundle root digest), or (CioFreezeFailed why).
(= (cio:freeze-bundle $root $cache)
   (let $names (cio:selection $root)
     (case $names
       (((CioFreezeFailed $why) (CioFreezeFailed $why))
        ($list
         (case (cio:bundle-digest $root $list)
           (((CioFreezeFailed $why) (CioFreezeFailed $why))
            ($digest
             (let $target (path:join $cache $digest)
               (if (fs:exists $target) (FrozenBundle $target $digest)
                 (cio:stage-bundle $root $cache $list $target $digest)))))))))))
; Copy into a staging folder, then rename it into place: a cached runtime is
; either absent or complete. A concurrent freeze of the same digest may win
; the rename; its copy is the same.
(= (cio:stage-bundle $root $cache $names $target $digest)
   (let* (($_ (fs:make-directories $cache 504))
          ($stage (path:join $cache (str:concat ".stage-" (system:uuid4-hex))))
          ($_ (fs:make-directories $stage 504))
          ($copied (cio:copy-files $root $stage $names))
          ($moved (if (== $copied True) (fs:rename $stage $target) $copied))
          ($_ (if (fs:exists $stage) (proc:run ("rm" "-rf" "--" $stage) $cache inherit 60000 0 "merged") ())))
     (if (fs:exists (path:join $target "coding_main.metta")) (FrozenBundle $target $digest)
       (CioFreezeFailed (format-args "could not stage the runtime: {}" ((repr $moved)))))))
(= (cio:copy-files $root $stage $names)
   (if (== $names ()) True
     (let $copied (cio:copy-file (path:join $root (car-atom $names)) (path:join $stage (car-atom $names)))
       (if (== $copied True) (cio:copy-files $root $stage (cdr-atom $names)) $copied))))
; The bytes and the permission bits, as shutil.copymode copies them.
(= (cio:copy-file $source $target)
   (case (fs:read-text-result $source 0 "raw")
     ((("ok" $bytes)
       (let* (($_ (fs:make-directories (path:dirname $target) 504))
              ($writer (fs:open-write $target ("create" "exclusive") 384)))
         (if (sf:failed? $writer) $writer
           (let* (($wrote (fs:write-all $writer $bytes)) ($_ (fs:close $writer)))
             (if (sf:failed? $wrote) $wrote
               (case (fs:metadata $source follow-permissions)
                 (((fs:info $kind $size $identity (fs:permissions $bits))
                   (let $moded (fs:set-mode $target $bits) (if (sf:failed? $moded) $moded True)))
                  ($other $other))))))))
      ($other (CioFreezeFailed (format-args "cannot read {}: {}" ($source (repr $other))))))))

; The engine a session runs on, pinned by its resolved file and content: a
; resume after an upgrade or a link flip must not run a different engine.
(= (cio:engine-binary)
   (let $named (system:getenv-or-default "CETTA_BIN" "cetta")
     (cio:canonical (case (str:find $named "/")
                      (((TextNotFound $slash) (cio:which $named (str:split ":" (system:getenv-or-default "PATH" ""))))
                       ($at $named))))))
(= (cio:which $name $dirs)
   (if (== $dirs ()) $name
     (let $candidate (path:join (car-atom $dirs) $name)
       (if (and (fs:is-file $candidate) (fs:access $candidate ("execute"))) $candidate
         (cio:which $name (cdr-atom $dirs))))))
(= (cio:engine-sha256 $binary)
   (case (fs:sha256-file $binary) (((fs:hashed-v1 $hex $info) (JsonString $hex)) ($other JsonNull))))

;; ------------------------------------------------------------ launch
; A launch is (CodingLaunch status root binary run detail): status reserved
; with a fresh single-use run token, running when the session is live (its
; manifest is unchanged), or failed with the reason.

(= (cio:instance) (system:getenv-or-default "METTACLAW_INSTANCE" "mettaclaw"))
(= (cio:scope-unit $dir) (str:join "" ((cio:instance) "-" (cio:basename $dir) ".scope")))
(= (cio:scope-active? $dir)
   (case (proc:run ("systemctl" "--user" "is-active" "--quiet" (cio:scope-unit $dir))
                   $dir inherit 5000 0 "merged")
     (((proc:exited 0 $out $err) True) ($other False))))
; A scope outlives its run when a command left something running: a
; background job belongs to the scope, not to the command or the worker that
; started it. Once a claimed run is over (its manifest says started or ended,
; and the worker its state records is gone), whatever its scope still holds
; belongs to nobody, and the scope is stopped. A reserved launch and a living
; worker are never touched. Under the session lock, so that no launch is
; reserved meanwhile. True when a scope was stopped.
(= (cio:end-leftovers $dir) (cio:locked $dir (cio:end-leftovers-locked $dir)))
(= (cio:end-leftovers-locked $dir)
   (let* (($phase (coding:field (sf:object (path:join $dir "runtime.json")) "phase"))
          ($owner (coding:text (coding:field (sf:object (path:join $dir "state.json")) "worker_owner"))))
     (if (and (cio:member $phase ((JsonString "started") (JsonString "ended")))
              (and (!= $owner "") (and (not (process:alive? $owner)) (cio:scope-active? $dir))))
       (let* (($stopped (proc:run ("systemctl" "--user" "stop" (cio:scope-unit $dir)) $dir inherit 10000 0 "merged"))
              ($_ (cio:log $dir (format-args "ended what the run left in {}: {}" ((cio:scope-unit $dir) (repr $stopped))))))
         True)
       False)))
; Live: its recorded worker is alive, or (when the folder holds a session
; that could have a scope) its scope is active.
(= (cio:live? $dir $check-scope)
   (let $state (sf:object (path:join $dir "state.json"))
     (if (and (== (coding:field $state "status") (JsonString "running"))
              (process:alive? (coding:text (coding:field $state "worker_owner")))) True
       (if $check-scope (cio:scope-active? $dir) False))))
; Live, once what an earlier run left in the scope has been ended: a leftover
; is not a running session. Under the session lock.
(= (cio:occupied? $dir $check-scope)
   (let $_ (if $check-scope (cio:end-leftovers-locked $dir) False) (cio:live? $dir $check-scope)))
(= (cio:running-launch $manifest)
   (CodingLaunch running (coding:text (coding:field $manifest "root")) (coding:text (coding:field $manifest "binary"))
                 (coding:text (coding:field $manifest "run")) "session is already running"))
(= (cio:put-default $object $key $value)
   (if (== (coding:field $object $key) JsonNull)
     (case $object (((JsonObject $pairs) (if (cio:has-key $pairs $key) $object (sf:put $object $key $value)))))
     $object))
(= (cio:has-key $pairs $key)
   (if (== $pairs ()) False
     (let (JsonPair $name $value) (car-atom $pairs) (if (== $name $key) True (cio:has-key (cdr-atom $pairs) $key)))))

; Reserve a launch on the session's pin, or pin a new session to a frozen copy
; of the current runtime. Note is the guidance a resume leaves the session, or
; NoNote. Under the session lock, after settling a dead earlier run.
(= (cio:freeze-runtime $root $dir $note)
   (cio:locked $dir (cio:freeze-runtime-locked (cio:program-root $root) $dir $note)))
(= (cio:freeze-runtime-locked $root $dir $note)
   (let* (($record (path:join $dir "runtime.json"))
          ($pinned (fs:is-file $record))
          ($old (sf:object $record))
          ($existing (or $pinned (or (fs:exists (path:join $dir "state.json"))
                                     (or (fs:exists (path:join $dir "messages.jsonl")) (!= $note NoNote))))))
     (if (cio:occupied? $dir $existing) (cio:running-launch $old)
       (let $_ (cio:settle-ending-locked $dir Observed RequireDead)
         (if $pinned (cio:reserve-pinned $dir $record $old $note)
           (cio:reserve-new $root $dir $record $existing $note))))))
(= (cio:reserve-pinned $dir $record $old $note)
   (let $runtime (coding:text (coding:field $old "root"))
     (if (not (fs:is-file (path:join $runtime "coding_main.metta")))
       (CodingLaunch failed "" "" "" "session runtime is missing; restore it before resuming")
       (let* (($token (system:uuid4-hex))
              ($manifest (sf:put (sf:put (sf:put (cio:put-default (cio:put-default $old "checkpoint" JsonNull) "previous" JsonNull)
                            "run" (JsonString $token)) "phase" (JsonString "reserved")) "at" (cio:now-json)))
              ($published (cio:publish-json $record $manifest)))
         (if (== $published True)
           (case (cio:prepare-resume $dir $note)
             ((True (CodingLaunch reserved $runtime (coding:text (coding:field $old "binary")) $token ""))
              ((CioPrepareFailed $why) (CodingLaunch failed "" "" "" (str:concat "could not prepare the resume: " $why)))))
           (CodingLaunch failed "" "" "" (repr $published)))))))
(= (cio:reserve-new $root $dir $record $existing $note)
   (case (cio:freeze-bundle $root (path:join (path:dirname (path:dirname $dir)) "coding-runtimes"))
     (((FrozenBundle $target $digest)
       (let* (($binary (cio:engine-binary))
              ($token (system:uuid4-hex))
              ($manifest (JsonObject ((JsonPair "root" (JsonString $target)) (JsonPair "digest" (JsonString $digest))
                                      (JsonPair "binary" (JsonString $binary)) (JsonPair "binary_sha256" (cio:engine-sha256 $binary))
                                      (JsonPair "checkpoint" JsonNull) (JsonPair "previous" JsonNull)
                                      (JsonPair "run" (JsonString $token)) (JsonPair "phase" (JsonString "reserved"))
                                      (JsonPair "at" (cio:now-json)))))
              ($published (cio:publish-json $record $manifest)))
         (if (== $published True)
           (case (if $existing (cio:prepare-resume $dir $note) True)
             ((True (CodingLaunch reserved $target $binary $token ""))
              ((CioPrepareFailed $why) (CodingLaunch failed "" "" "" (str:concat "could not prepare the resume: " $why)))))
           (CodingLaunch failed "" "" "" (repr $published)))))
      ((CioFreezeFailed $why) (CodingLaunch failed "" "" "" $why)))))

; A resumed run starts clean: no stop or recycle request, the last report kept
; as report.previous.txt, no hold or archive label, and the resume's note in
; the inbox. True, or (CioPrepareFailed why) at the first step that failed.
(= (cio:prepare-resume $dir $note)
   (cio:then (cio:remove-flag (path:join $dir "stop"))
     (cio:then (cio:remove-flag (path:join $dir "recycle.requested"))
       (cio:then (cio:keep-report $dir)
         (cio:then (cio:clear-lifecycle $dir)
           (if (== $note NoNote) True
             (cio:then (cio:append-line (path:join $dir "inbox.jsonl") (json-stringify (JsonString $note))) True)))))))
; Run the next step only after this one succeeded.
(: cio:then (-> %Undefined% Atom %Undefined%))
(= (cio:then $done $next)
   (let $ok (cio:ok $done)
     (if (== $ok True) (eval $next) (CioPrepareFailed (repr $ok)))))
(= (cio:ok $result) (if (or (== $result True) (== $result 1)) True (if (sf:failed? $result) $result (case $result ((() True) ($other $other))))))
(= (cio:remove-flag $path) (if (fs:exists $path) (fs:remove $path) True))
(= (cio:keep-report $dir)
   (let $report (path:join $dir "report.txt")
     (if (fs:is-file $report)
       (let $moved (fs:rename $report (path:join $dir "report.previous.txt"))
         (if (sf:failed? $moved) $moved (sf:sync-directory $dir)))
       True)))

; Hold and archive labels live in lifecycle.json under .lifecycle.lock, which
; stays writable by the shared group (0660) whatever the umask.
(: cio:lifecycle-locked (-> %Undefined% Atom %Undefined%))
(= (cio:lifecycle-locked $dir $body)
   (sf:locked (path:join $dir ".lifecycle.lock") 432 $body))
(= (cio:clear-lifecycle $dir)
   (cio:lifecycle-locked $dir
     (let $path (path:join $dir "lifecycle.json")
       (if (fs:is-file $path)
         (let $_ (fs:rename $path (path:join $dir "lifecycle.previous.json")) (sf:sync-directory $dir))
         True))))

; The model a session keeps, recorded in its own model.metta so the agent's
; model file never reaches the session: task.json's model; for a session
; from before models were recorded, the one model.metta already names, or
; else the fallback.
(= (cio:session-model $dir $fallback)
   (let* (($recorded (coding:text (coding:field (sf:object (path:join $dir "task.json")) "model")))
          ($state (path:join $dir "model.metta"))
          ($saved (if (and (== $recorded "") (fs:is-file $state)) (cio:active-model (fs:read-text $state)) ""))
          ($model (if (!= $recorded "") $recorded (if (!= $saved "") $saved $fallback)))
          ($text (format-args "(active-model {})\n" ($model)))
          ($_ (if (and (fs:is-file $state) (== (fs:read-text $state) $text)) () (fs:write $state $text))))
     $model))
(= (cio:active-model $text)
   (case (str:find $text "(active-model")
     (((TextNotFound $missing) "")
      ($at (let $rest (str:split-whitespace (car-atom (str:split ")" (str:char-slice $text (+ $at 13) (str:char-length $text)))))
             (if (== $rest ()) "" (car-atom $rest)))))))

; The session's Python module path: its runtime's own source folders, then the
; agent's other entries, without the agent's own source folders.
(= (cio:runtime-pythonpath $runtime $original)
   (let* (($own ((path:join $runtime "src") (path:join $runtime "channels") (path:join $runtime "modes")))
          ($skip ((path:join $original "src") (path:join $original "channels") (path:join $original "modes")))
          ($inherited (cio:kept-entries (str:split ":" (system:getenv-or-default "PYTHONPATH" "")) $skip)))
     (str:join ":" (cio:append-list $own $inherited))))
(= (cio:kept-entries $entries $skip)
   (if (== $entries ()) ()
     (let $entry (car-atom $entries)
       (if (or (== $entry "") (cio:member $entry $skip)) (cio:kept-entries (cdr-atom $entries) $skip)
         (cons-atom $entry (cio:kept-entries (cdr-atom $entries) $skip))))))

; A word for sh: single-quoted, with each single quote spelled '"'"'.
(= (cio:shell-quote $text) (str:join "" ("'" (cio:replace $text "'" "'\"'\"'") "'")))

;; ------------------------------------------------------------ re-pin
; A session moves to the current runtime only after that runtime has read a
; copy of its checkpoint (coding_check.metta runs no tools and calls no
; provider), and only if the session did not change during the check.

(= (cio:note-pinned)
   "Resume this session from its existing evidence and plan. Verify uncertain effects before choosing new actions.")
(= (cio:note-repinned)
   "Resume this session from its existing evidence and plan. It now runs on a newer runtime than its last run. Verify uncertain effects before choosing new actions.")

; The checkpoint's revision: task, plan, guidance, transcript and execution
; records, by name, size and content digest. Files are hashed as streams.
(= (cio:checkpoint-revision $dir)
   (str:sha256 (str:join "\n" (cio:revision-lines $dir (cio:checkpoint-names $dir)))))
(= (cio:checkpoint-names $dir)
   (let* (($top (cio:existing $dir ("guidance.json" "messages.jsonl" "plan.json" "task.json")))
          ($executions (path:join $dir "executions"))
          ($records (if (fs:exists $executions) (cio:record-names $executions (fs:entries $executions)) ())))
     (sort-strings (cio:append-list $top $records))))
(= (cio:existing $dir $names)
   (if (== $names ()) ()
     (let $rest (cio:existing $dir (cdr-atom $names))
       (if (fs:is-file (path:join $dir (car-atom $names))) (cons-atom (car-atom $names) $rest) $rest))))
(= (cio:record-names $executions $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names)) ($rest (cio:record-names $executions (cdr-atom $names))))
       (if (and (str:ends-with $name ".json") (fs:is-file (path:join $executions $name)))
         (cons-atom (str:concat "executions/" $name) $rest) $rest))))
(= (cio:revision-lines $dir $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names))
            ($line (case (fs:sha256-file (path:join $dir $name))
                     (((fs:hashed-v1 $hex (fs:info-v2 $kind $size $identity $modified $changed $permissions $blocks))
                       (format-args "{}\t{}\t{}" ($name $size $hex)))
                      ($other (str:concat $name "\tunreadable"))))))
       (cons-atom $line (cio:revision-lines $dir (cdr-atom $names))))))

;; The check runs on a copy, never the live folder: everything but tmp/,
;; run.log and the lock; of executions/, only the records.
(= (cio:copy-tree $from $to $skip)
   (let $_ (fs:make-directories $to 504) (cio:copy-entries $from $to (fs:entries $from) $skip)))
(= (cio:copy-entries $from $to $names $skip)
   (if (== $names ()) True
     (let* (($name (car-atom $names))
            ($copied (if (cio:member $name $skip) True (cio:copy-entry $from $to $name))))
       (if (== $copied True) (cio:copy-entries $from $to (cdr-atom $names) $skip) $copied))))
(= (cio:copy-entry $from $to $name)
   (let* (($source (path:join $from $name)) ($target (path:join $to $name)))
     (case (fs:metadata $source follow)
       (((fs:info directory $size $identity)
         (if (== $name "executions") (cio:copy-records $source $target (fs:entries $source))
           (cio:copy-tree $source $target ())))
        ((fs:info regular $size $identity) (cio:copy-file $source $target))
        ($other True)))))
(= (cio:copy-records $from $to $names)
   (let $_ (fs:make-directories $to 504)
     (if (== $names ()) True
       (let* (($name (car-atom $names))
              ($copied (if (str:ends-with $name ".json") (cio:copy-file (path:join $from $name) (path:join $to $name)) True)))
         (if (== $copied True) (cio:copy-records $from $to (cdr-atom $names)) $copied)))))

(= (cio:check-environment $bundle $tmp)
   (let* (($base (("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin"))
                  ("HOME" (system:getenv-or-default "HOME" "/"))
                  ("LANG" (system:getenv-or-default "LANG" "C.UTF-8"))
                  ("TMPDIR" $tmp) ("METTACLAW_CODING_CHECK" "1")
                  ("PYTHONPATH" (cio:runtime-pythonpath $bundle (cio:program-root (system:cwd)))))))
     (cio:append-list $base (cio:present-env ("PYTHONHOME" "LD_LIBRARY_PATH" "CETTA_ROOT" "PETTA_PY_ENV" "PETTA_ROOT")))))
(= (cio:present-env $names)
   (if (== $names ()) ()
     (let* (($value (system:getenv-or-default (car-atom $names) "")) ($rest (cio:present-env (cdr-atom $names))))
       (if (== $value "") $rest (cons-atom ((car-atom $names) $value) $rest)))))

; (CheckpointPassed) or (CheckpointRefused why). The verdict is the check's
; last output line, exactly (CheckpointReady n) with success, or
; (CheckpointFailed "why").
(= (cio:check-checkpoint $dir $bundle $binary)
   (let $entry (path:join $bundle "coding_check.metta")
     (if (not (fs:is-file $entry)) (CheckpointRefused "coding_check.metta is missing from the target runtime")
       (let* (($checks (path:join (path:dirname (path:dirname $dir)) "coding-checks"))
              ($copy (path:join $checks (str:join "" ((cio:basename $dir) "-" (str:char-slice (system:uuid4-hex) 0 16)))))
              ($copied (cio:copy-tree $dir $copy ("tmp" "run.log" ".ending.lock")))
              ($verdict (if (== $copied True) (cio:run-check $copy $bundle $binary $entry)
                          (CheckpointRefused (format-args "could not copy the checkpoint: {}" ((repr $copied))))))
              ($_ (proc:run ("rm" "-rf" "--" $copy) $checks inherit 60000 0 "merged")))
         $verdict))))
(= (cio:run-check $copy $bundle $binary $entry)
   (let* (($tmp (path:join $copy "tmp")) ($_ (fs:make-directories $tmp 504)))
     (case (proc:run ($binary "--lang" "petta" "--import-mode" "ancestor-walk" $entry "--" $copy)
                     $bundle (cio:check-environment $bundle $tmp) 120000 1048576)
       (((proc:exited $code $out $err) (cio:check-verdict $code (cio:last-line (str:split "\n" $out) "")))
        ((proc:timed-out $out $err) (CheckpointRefused "checkpoint check timed out"))
        ($other (CheckpointRefused "the check produced no valid verdict"))))))
(= (cio:last-line $lines $last)
   (if (== $lines ()) $last
     (let $line (str:trim (car-atom $lines))
       (cio:last-line (cdr-atom $lines) (if (== $line "") $last $line)))))
(= (cio:check-verdict $code $line)
   (if (and (str:starts-with $line "(CheckpointReady ") (str:ends-with $line ")"))
     (let $count (str:char-slice $line 17 (- (str:char-length $line) 1))
       (if (and (!= $count "") (str:is-all-in digit $count))
         (if (== $code 0) (CheckpointPassed) (CheckpointRefused "the check produced no valid verdict"))
         (CheckpointRefused "the check produced no valid verdict")))
     (if (and (str:starts-with $line "(CheckpointFailed \"") (str:ends-with $line "\")"))
       (let $quoted (str:char-slice $line 18 (- (str:char-length $line) 1))
         (case (json-parse $quoted)
           (((JsonString $why) (CheckpointRefused $why))
            ($other (CheckpointRefused (str:char-slice $quoted 1 (- (str:char-length $quoted) 1)))))))
       (CheckpointRefused "the check produced no valid verdict"))))

; runs.jsonl: one record per re-pin. Rewritten whole; lines that are not JSON
; objects, a torn last line among them, are dropped.
(= (cio:append-run $dir $record)
   (let* (($path (path:join $dir "runs.jsonl"))
          ($kept (if (fs:is-file $path) (cio:object-lines (str:split "\n" (fs:read-text $path))) ())))
     (cio:publish $path (str:concat (str:join "\n" (cio:append-list $kept ((json-stringify $record)))) "\n"))))
(= (cio:object-lines $lines)
   (if (== $lines ()) ()
     (let $rest (cio:object-lines (cdr-atom $lines))
       (case (catch (json-parse (car-atom $lines)))
         (((JsonObject $pairs) (cons-atom (json-stringify (JsonObject $pairs)) $rest))
          ($other $rest))))))
(= (cio:without $object $key)
   (case $object (((JsonObject $pairs) (JsonObject (cio:without-pairs $pairs $key))) ($other $object))))
(= (cio:without-pairs $pairs $key)
   (if (== $pairs ()) ()
     (let (JsonPair $name $value) (car-atom $pairs)
       (if (== $name $key) (cio:without-pairs (cdr-atom $pairs) $key)
         (cons-atom (JsonPair $name $value) (cio:without-pairs (cdr-atom $pairs) $key))))))
; After the manifest was replaced, a resume whose preparation failed is
; uncertain: the pin may have changed, and the next resume rechecks it.
(= (cio:prepared-launch $dir $note $launch)
   (case (cio:prepare-resume $dir $note)
     ((True $launch)
      ((CioPrepareFailed $why) (CodingLaunch uncertain "" "" "" $why)))))
(= (cio:published-launch $published $status $root $binary $token)
   (case $published
     ((True (CodingLaunch $status $root $binary $token $token))
      ((SfUncertain $failure) (CodingLaunch uncertain "" "" "" (repr $failure)))
      ($failed (CodingLaunch failed "" "" "" (repr $failed))))))

; Re-pin to the current runtime: same (the pin already is the current runtime;
; a fresh token), repinned (checked and moved), running, changed (the session
; changed during the check), failed (pin unchanged) or uncertain (the manifest
; was replaced but its durability is unconfirmed).
(= (cio:repin $root $dir)
   (case (cio:freeze-bundle (cio:program-root $root) (path:join (path:dirname (path:dirname $dir)) "coding-runtimes"))
     (((CioFreezeFailed $why) (CodingLaunch failed "" "" "" $why))
      ((FrozenBundle $target $digest)
       (let* (($binary (cio:engine-binary)) ($engine (cio:engine-sha256 $binary)))
         (case (cio:locked $dir (cio:repin-observe $dir $target $digest $binary $engine))
           (((RepinObserved $revision $run $phase)
             (case (cio:check-checkpoint $dir $target $binary)
               (((CheckpointRefused $why) (CodingLaunch failed "" "" "" $why))
                ((CheckpointPassed)
                 (cio:locked $dir (cio:repin-commit $dir $target $digest $binary $engine $revision $run $phase))))))
            ($decided $decided))))))))
(= (cio:repin-observe $dir $target $digest $binary $engine)
   (let* (($record (path:join $dir "runtime.json")) ($old (sf:object $record)) ($phase (coding:field $old "phase")))
     (if (cio:occupied? $dir True) (cio:running-launch $old)
       (if (and (!= $old (JsonObject ()))
                (and (== (coding:field $old "digest") (JsonString $digest))
                     (and (== (coding:field $old "binary") (JsonString $binary))
                          (and (== (coding:field $old "binary_sha256") $engine)
                               (and (cio:member $phase ((JsonString "reserved") (JsonString "started") (JsonString "ended")))
                                    (fs:is-file (path:join (coding:text (coding:field $old "root")) "coding_main.metta")))))))
         (let* (($_ (cio:settle-ending-locked $dir Observed RequireDead))
                ($token (system:uuid4-hex))
                ($manifest (sf:put (sf:put (sf:put (cio:put-default (cio:put-default $old "checkpoint" JsonNull) "previous" JsonNull)
                              "run" (JsonString $token)) "phase" (JsonString "reserved")) "at" (cio:now-json)))
                ($launch (cio:published-launch (cio:publish-json $record $manifest) same
                           (coding:text (coding:field $old "root")) (coding:text (coding:field $old "binary")) $token)))
           (case $launch
             (((CodingLaunch same $r $b $t $d) (cio:prepared-launch $dir (cio:note-pinned) $launch))
              ($other $other))))
         (RepinObserved (cio:checkpoint-revision $dir) (coding:field $old "run") $phase)))))
(= (cio:repin-commit $dir $target $digest $binary $engine $revision $run $phase)
   (let* (($record (path:join $dir "runtime.json")) ($current (sf:object $record)))
     (if (cio:occupied? $dir True) (cio:running-launch $current)
       (if (or (!= (cio:checkpoint-revision $dir) $revision)
               (or (!= (coding:field $current "run") $run) (!= (coding:field $current "phase") $phase)))
         (CodingLaunch changed "" "" "" "the session changed during the check; try again.")
         (let* (($_ (cio:settle-ending-locked $dir Observed RequireDead))
                ($settled (sf:object $record))
                ($token (system:uuid4-hex)) ($now (cio:now-json))
                ($manifest (JsonObject ((JsonPair "root" (JsonString $target)) (JsonPair "digest" (JsonString $digest))
                                        (JsonPair "binary" (JsonString $binary)) (JsonPair "binary_sha256" $engine)
                                        (JsonPair "checkpoint" (JsonString $revision))
                                        (JsonPair "previous" (if (== $settled (JsonObject ())) JsonNull (cio:without $settled "previous")))
                                        (JsonPair "run" (JsonString $token)) (JsonPair "phase" (JsonString "reserved"))
                                        (JsonPair "at" $now))))
                ($launch (cio:published-launch (cio:publish-json $record $manifest) repinned $target $binary $token)))
           (case $launch
             (((CodingLaunch repinned $r $b $t $d)
               (let $logged (cio:append-run $dir (JsonObject ((JsonPair "at" $now) (JsonPair "from" (coding:field $settled "digest"))
                                                               (JsonPair "to" (JsonString $digest)) (JsonPair "run" (JsonString $token)))))
                 (if (== $logged True)
                   (cio:prepared-launch $dir (cio:note-repinned) $launch)
                   (CodingLaunch uncertain "" "" "" (repr $logged)))))
              ($other $other))))))))

;; -------------------------------------------------- context accounting
; A request's size in tokens, for compaction: four bytes a token, as Codex
; estimates new items, or the rate the provider counted for this session's
; last request when that was more.

; A token count the provider reported: a non-negative JSON integer.
(= (cio:token-count $value)
   (case $value
     (((JsonNumber $text) (if (and (!= $text "") (str:is-all-in digit $text)) (str:string->number $text) None))
      ($other None))))
(= (cio:count-or-zero $value) (let $count (cio:token-count $value) (if (== $count None) 0 $count)))
; The input the provider counted for a request: its prompt (or input) tokens,
; plus the cache reads and writes a provider that reports them separately
; (Anthropic's cache_read_input_tokens) leaves out of that number. None when
; nothing positive was reported.
(= (cio:reported-input $usage)
   (let $base (cio:token-count (if (!= (coding:field $usage "prompt_tokens") JsonNull)
                                 (coding:field $usage "prompt_tokens") (coding:field $usage "input_tokens")))
     (if (== $base None) None
       (let $total (if (== (coding:field $usage "cache_read_input_tokens") JsonNull) $base
                     (+ $base (+ (cio:count-or-zero (coding:field $usage "cache_read_input_tokens"))
                                 (cio:count-or-zero (coding:field $usage "cache_creation_input_tokens")))))
         (if (> $total 0) $total None)))))
; Remember what the provider counted for the request just answered (reply.json).
(= (cio:context-observe $dir $request-bytes)
   (let $tokens (cio:reported-input (coding:field (sf:object (path:join $dir "reply.json")) "usage"))
     (if (or (== $tokens None) (<= $request-bytes 0)) 0
       (if (== (cio:publish-json (path:join $dir "context.json")
                 (JsonObject ((JsonPair "bytes" (JsonNumber (repr $request-bytes)))
                              (JsonPair "tokens" (JsonNumber (repr $tokens))))))
               True) 1 0))))
(= (cio:context-tokens $dir $request-bytes)
   (let* (($record (sf:object (path:join $dir "context.json")))
          ($bytes (cio:token-count (coding:field $record "bytes")))
          ($tokens (cio:token-count (coding:field $record "tokens")))
          ($quarter (math:floor (/ $request-bytes 4))))
     (if (or (== $bytes None) (or (== $tokens None) (== $bytes 0))) $quarter
       (max $quarter (math:ceil (/ (* $request-bytes $tokens) $bytes))))))

;; ------------------------------------------------------ hold, archive
; A hold or archive label is the agent's own note about a session; a resume
; sets it aside (cio:prepare-resume). Written whole under .lifecycle.lock.

(= (cio:leap? $year) (or (and (== (% $year 4) 0) (!= (% $year 100) 0)) (== (% $year 400) 0)))
(= (cio:month-days $year $month)
   (if (== $month 2) (if (cio:leap? $year) 29 28)
     (if (cio:member $month (4 6 9 11)) 30 31)))
; A calendar date written YYYY-MM-DD.
(= (cio:review-date? $text)
   (let $parts (str:split "-" $text)
     (if (!= (size-atom $parts) 3) False
       (let ($y $m $d) $parts
         (if (not (and (and (== (str:byte-length $y) 4) (== (str:byte-length $m) 2))
                       (and (== (str:byte-length $d) 2)
                            (and (str:is-all-in digit $y) (and (str:is-all-in digit $m) (str:is-all-in digit $d))))))
           False
           (let* (($year (str:string->number $y)) ($month (str:string->number $m)) ($day (str:string->number $d)))
             (and (and (>= $month 1) (<= $month 12))
                  (and (>= $day 1) (<= $day (cio:month-days $year $month))))))))))
(= (cio:label $dir $label)
   (let $written (cio:lifecycle-locked $dir (cio:publish-json (path:join $dir "lifecycle.json") $label))
     (if (== $written True) True (CioLabelFailed $written))))
(= (cio:hold-session $dir $why $review)
   (let $id (cio:basename $dir)
     (if (not (fs:is-file (path:join $dir "task.json"))) (str:concat "work-hold: no session " $id)
       (if (and (!= $review "") (not (cio:review-date? $review)))
         (str:join "" ("work-hold: invalid review date \"" $review "\"; use YYYY-MM-DD"))
         (let $written (cio:label $dir (JsonObject ((JsonPair "hold" (JsonObject (
                         (JsonPair "why" (JsonString $why))
                         (JsonPair "review" (if (== $review "") JsonNull (JsonString $review)))
                         (JsonPair "at" (cio:now-json))))))))
           (if (!= $written True) (str:concat "work-hold: " (repr $written))
             (if (== $review "") (str:concat "WORK_HELD " $id) (str:join "" ("WORK_HELD_UNTIL " $review " " $id)))))))))
(= (cio:archive-session $dir $why)
   (let $id (cio:basename $dir)
     (if (not (fs:is-file (path:join $dir "task.json"))) (str:concat "work-archive: no session " $id)
       (let $written (cio:label $dir (JsonObject ((JsonPair "archive" (JsonObject (
                       (JsonPair "why" (JsonString $why)) (JsonPair "at" (cio:now-json))))))))
         (if (!= $written True) (str:concat "work-archive: " (repr $written)) (str:concat "WORK_ARCHIVED " $id))))))

;; ----------------------------------------------------------- settings
; The agent's coding settings, in memory/coding-settings.json: the context
; source mode (open, summary or off) and the default resume mode (pinned or
; current). An unknown recorded value reads as the default.
(= (cio:settings-path $root) (path:join $root "memory/coding-settings.json"))
(= (cio:work-setting $root $key)
   (let $value (coding:text (coding:field (sf:object (cio:settings-path $root)) $key))
     (if (== $key "context") (if (cio:member $value ("open" "summary" "off")) $value "open")
       (if (== $key "resume") (if (cio:member $value ("pinned" "current")) $value "pinned") $value))))
(= (cio:set-work-setting $root $key $value)
   (let* (($path (cio:settings-path $root))
          ($written (cio:publish-json $path (sf:put (sf:object $path) $key (JsonString $value)))))
     (if (== $written True) $path (CioSettingFailed $written))))
