; Coding sessions started from the agent loop. Each runs as its own systemd
; scope, so it outlives the agent process's restarts, and mails its report
; to the requester when it ends (coding_main.metta). The agent keeps talking
; while it works, and can watch, steer or stop it.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library system))
!(import! &self (library str))
!(import! &self (library json))
!(import! &self ./src/coding_estate_cetta.metta)
!(import! &self ./src/coding_io_cetta.metta)
!(import! &self ./src/coding_guidance.metta)
!(import! &self ./src/coding_delivery.metta)
!(import! &self ./src/process_identity_cetta.metta)
!(import! &self ./src/settings_registry.metta)

(SettingSpec 1 "work" "context" "open" "mode" (Choice ("open" "summary" "off")) "persistent"
  "Coding-session context source mode: open lists due open sessions, summary shows counts and verbs, off omits the source."
  work-setting work-setting)
(SettingSpec 1 "work" "resume" "pinned" "mode" (Choice ("pinned" "current")) "persistent"
  "Coding-session default resume mode: pinned resumes on the session's pin, current re-pins to the current runtime after checking a copy of the checkpoint."
  work-setting work-setting)

(= (work-setting) (settings "work"))
(= (work-setting $key)
   (if (or (== $key "context") (== $key "resume"))
     (cio:work-setting (system:cwd) $key)
     (settings:validate "work" $key "")))
(= (work-setting $key $raw)
   (let* (($value (case (settings:spec "work" $key)
                    (((SettingSpec 1 $ns $key $default $unit $domain $life $description $reader $writer)
                      (settings:coerce $domain $raw))
                     ($other $raw))))
          ($check (settings:validate "work" $key $value)))
     (if (== $check True)
       (let $saved (cio:set-work-setting (system:cwd) $key $value)
         (WorkConfigured $key $value (WorkSaved $saved)))
       $check)))

(= (work:root) (path:join (system:cwd) "memory/coding"))
(= (work:dir $id) (path:join (work:root) $id))
(= (work:unit $id) (str:join "" ((system:getenv-or-default "METTACLAW_INSTANCE" "mettaclaw") "-" $id)))
(= (work:field $value $key)
   (case $value (((JsonObject $pairs) (work:member $pairs $key)) ($other JsonNull))))
(= (work:member $pairs $key)
   (if (== $pairs ()) JsonNull
     (let (JsonPair $name $value) (car-atom $pairs)
       (if (== $name $key) $value (work:member (cdr-atom $pairs) $key)))))
(= (work:text $value)
   (case $value (((JsonString $text) $text) ((JsonNumber $text) $text) ($other ""))))
(= (work:json $path) (if (fs:is-file $path) (json-parse (fs:read-text $path)) (JsonObject ())))
(= (work:shell $command) (proc:run ("bash" "-c" $command) (system:cwd) inherit 30000 0 "merged"))
(= (work:quote $text) (cio:shell-quote $text))
(= (work:clip $text $limit)
   (if (<= (str:char-length $text) $limit) $text (str:concat (str:char-slice $text 0 $limit) " …")))

;; A session keeps the model and effort it starts with: the agent's own model
;; and "high" unless the call names others. Switching the agent's model later
;; does not change a running or resumed session.
(= (work:agent-model) (format-args "{}" ((py-call (synthetic_llm.current_model)))))
(= (work:efforts) ("default" "minimal" "low" "medium" "high" "xhigh" "max"))
(= (work:known? $item $items)
   (if (== $items ()) False
     (if (== (car-atom $items) $item) True (work:known? $item (cdr-atom $items)))))
(= (work:choice-problem $model $effort)
   (let* (($problem (format-args "{}" ((py-call (synthetic_llm.session_model_error $model)))))
          ($efforts (work:efforts)))
     (if (== $problem "")
       (if (work:known? $effort $efforts) ""
         (str:join "" ("unknown effort \"" $effort "\"; use one of " (repr $efforts))))
       $problem)))
(= (work $task) (work $task ""))
(= (work $task $workdir) (work:start $task $workdir (work:agent-model) "high"))
(= (work $task $workdir $model) (work $task $workdir $model "high"))
(= (work $task $workdir $model $effort)
   (let* (($chosen (if (== $model "") (work:agent-model) $model))
          ($level (if (== $effort "") "high" $effort))
          ($problem (work:choice-problem $chosen $level)))
     (if (== $problem "") (work:start $task $workdir $chosen $level) (str:concat "work: " $problem))))
(= (work:start $task $workdir $model $effort)
   (let $where (if (== $workdir "") (system:getenv-or-default "HOME" "/") $workdir)
     (case (guidance:workspace $where)
       (((GuidanceWorkspace $canonical)
         (case (guidance:configuration)
           (((GuidanceOptions $options) (work:start-at $task $canonical $model $effort $options))
            ((GuidanceFailed $why) (str:concat "work: " $why)))))
        ((GuidanceFailed $why) (str:concat "work: " $why))))))
(= (work:start-at $task $workdir $model $effort $guidance)
   (let* (($stamp (system:format-time "%Y%m%d-%H%M%S" (system:wall-time) "local"))
          ($salt (str:char-slice (system:uuid4-hex) 0 4))
          ($id (format-args "work-{}-{}" ($stamp $salt)))
          ($dir (work:dir $id))
          ($_ (proc:run ("mkdir" "-p" "--" $dir) (system:cwd) inherit 10000 0 "merged"))
          ($where $workdir)
          ($requester (system:getenv-or-default "METTACLAW_MAIL_NAME" ""))
          ($task-json (JsonObject ((JsonPair "task" (JsonString $task)) (JsonPair "workdir" (JsonString $where))
                                   (JsonPair "requester" (JsonString $requester)) (JsonPair "effort" (JsonString $effort))
                                   (JsonPair "requester_space" (JsonString (system:getenv-or-default "METTACLAW_MAIL_SPACE" "agent-mail")))
                                   (JsonPair "model" (JsonString $model)) (JsonPair "guidance" $guidance))))
          ($_ (fs:update-text (path:join $dir "task.json") "replace" (json-stringify $task-json) "" "")))
     (case (work:launch $id)
       (((WorkLaunched $launched)
         (str:join "" ("WORK_STARTED " $id ". It runs on its own and mails you its report when done; "
                       "(work-status \"" $id "\") shows its progress, (work-steer \"" $id "\" \"text\") guides it, "
                       "(work-stop \"" $id "\") stops it.")))
        ((WorkNotLaunched $why) (str:join "" ("work: session " $id " could not launch: " $why)))))))

(= (work:launch $id)
   (work:launch-at $id (cio:freeze-runtime (system:cwd) (work:dir $id) NoNote)))
;; A reservation launches; anything else names why it did not.
(= (work:launch-at $id $desc)
   (case $desc
     (((CodingLaunch reserved $runtime $binary $token $detail) (work:launch-reserved $id $runtime $binary $token))
      ((CodingLaunch $status $runtime $binary $token $detail) (WorkNotLaunched (str:concat $status (str:concat ": " $detail))))
      ($other (WorkNotLaunched (repr $other))))))
(= (work:launch-reserved $id $runtime $binary $token)
   (let* (($dir (work:dir $id))
          ($tmp (path:join $dir "tmp"))
          ($_ (proc:run ("mkdir" "-p" "--" $tmp) (system:cwd) inherit 10000 0 "merged"))
          ;; The session reads its model from its own state file, never the
          ;; agent's; a session from before models were recorded keeps the
          ;; agent's model of the moment it resumes.
          ($model (cio:session-model $dir (work:agent-model)))
          ($pythonpath (cio:runtime-pythonpath $runtime (system:cwd)))
          ;; The session shares the account's ledger and usage tally, as
          ;; Codex sub-agents share rate limits, but keeps its own health,
          ;; last-completion and restart files: its calls are not the agent's turns.
          ($own (str:join " " ("env" (work:quote (str:concat "PYTHONPATH=" $pythonpath))
                                    (work:quote (str:concat "METTACLAW_ACTIVITY_CALL_SCOPE=" $id))
                                    (work:quote (str:concat "METTACLAW_STREAM_PARENT="
                                      (system:getenv-or-default "METTACLAW_INSTANCE" "agent")))
                                    "METTACLAW_ACTIVITY_CALL_LANE=background"
                                    (work:quote (str:concat "METTACLAW_COGNITIVE_HEALTH_PATH=" (path:join $dir "health.json")))
                                    (work:quote (str:concat "METTACLAW_LLM_COMPLETION_PATH=" (path:join $dir "completion.json")))
                                    (work:quote (str:concat "METTACLAW_PROVIDER_OBSERVATION_PATH=" (path:join $dir "providers.json")))
                                    (work:quote (str:concat "METTACLAW_RECYCLE_REQUEST_PATH=" (path:join $dir "recycle.requested")))
                                    (work:quote (str:concat "METTACLAW_WAKE_REQUEST_PATH=" (path:join $dir "wake.requested")))
                                    (work:quote (str:concat "TMPDIR=" $tmp))
                                    (work:quote (str:concat "SYNTHETIC_MODEL=" $model))
                                    (work:quote (str:concat "METTACLAW_MODEL_STATE_PATH=" (path:join $dir "model.metta")))
                                    (work:quote (str:concat "METTACLAW_CODING_RUN_TOKEN=" $token))
                                    "METTACLAW_CODING_SESSION=1"
                                    "METTACLAW_OBSERVED_STRATEGY=0")))
          ($launch (str:join "" ("cd " (work:quote $runtime) " || exit 1; systemd-run --user --scope --quiet --collect --unit=" (work:quote (work:unit $id)) " -- " $own " "
                                 (work:quote $binary) " --lang petta --import-mode ancestor-walk coding_main.metta -- " (work:quote $dir)
                                 " >> " (work:quote (path:join $dir "run.log")) " 2>&1 &")))
          ($_ (work:shell $launch)))
     (WorkLaunched $id)))

(= (work:scope-state $id)
   (case (proc:run ("systemctl" "--user" "is-active" "--quiet" (str:concat (work:unit $id) ".scope"))
                  (system:cwd) inherit 10000 0 "merged")
     (((proc:exited 0 $out $err) active)
      ((proc:exited 3 $out $err) inactive)
      ((proc:exited 4 $out $err) inactive)
      ($other unknown))))
(= (work:running? $id) (== (work:scope-state $id) active))

; Resumption keeps this session's task, transcript, plan and inbox identities.
; Resume file preparation happens inside .ending.lock when the reservation is made.
(= (work:finish-resume $id $dir $desc)
   (let* (($_ (delivery:preserve $dir))
          ($_ (work:launch-at $id $desc)))
     (str:concat "WORK_RESUMING " $id)))

(= (work-resume $id)
   (work-resume $id (work-setting "resume")))
(= (work-resume $id $mode)
   (let* (($dir (work:dir $id)) ($task-path (path:join $dir "task.json")))
     (if (not (fs:is-file $task-path)) (str:concat "work-resume: no session " $id)
       (if (work:running? $id) (str:concat "WORK_RUNNING " $id)
         (if (== $mode "pinned")
           (let $problem (let (EstateView $facts $session) (estate:session-view (system:cwd) $id)
                           (estate:resume-problem $facts $session))
             (if (not (== $problem ""))
               (if (str:ends-with $problem "Try again later.")
                 (str:concat "work-resume: " $problem)
                 (str:join "" ("work-resume: " $problem "; this session cannot resume on its pin. Move it to the current runtime with (work-resume \"" $id "\" \"current\"), start a new session with (work \"...\"), or set it aside with (work-archive \"" $id "\" \"why\").")))
               (let* (($_ (delivery:preserve $dir))
                      ($desc (cio:freeze-runtime (system:cwd) $dir (cio:note-pinned))))
                 (case $desc
                   (((CodingLaunch running $runtime $binary $token $detail) (str:concat "WORK_RUNNING " $id))
                    ((CodingLaunch failed $runtime $binary $token $detail) (str:concat "work-resume: " $detail))
                    ($reserved (work:finish-resume $id $dir $reserved)))))))
           (if (== $mode "current")
             (let* (($_ (delivery:preserve $dir))
                    ($outcome (cio:repin (system:cwd) $dir)))
               (case $outcome
                 (((CodingLaunch running $runtime $binary $token $detail) (str:concat "WORK_RUNNING " $id))
                  ((CodingLaunch failed $runtime $binary $token $detail)
                   (str:join "" ("work-resume: the current runtime cannot read this session's checkpoint: " $detail "; its pin is unchanged. Start a new session for the task with (work \"...\") or set it aside with (work-archive \"" $id "\" \"why\").")))
                  ((CodingLaunch uncertain $runtime $binary $token $detail)
                   (str:join "" ("work-resume: re-pinning could not confirm durability after updating runtime.json: " $detail "; its pin may have changed and the next resume will recheck it.")))
                  ((CodingLaunch changed $runtime $binary $token $detail) (str:concat "work-resume: " $detail))
                  ((CodingLaunch $moved $runtime $binary $token $detail)
                   (work:finish-resume $id $dir (CodingLaunch reserved $runtime $binary $token ""))))))
             (str:join "" ("work-resume: unknown resume mode \"" $mode "\"; use \"pinned\" or \"current\"."))))))))

(= (work:line $id)
   (let (EstateView $facts $session) (estate:session-view (system:cwd) $id)
     (work:line $id $facts $session)))
(= (work:line $id $facts $session)
   (let* (($dir (work:dir $id))
          ($state (work:json (path:join $dir "state.json")))
          ($status (estate:effective-status $session (work:running? $id)))
          ($task (work:text (work:field (work:json (path:join $dir "task.json")) "task")))
          ($estate (estate:status-suffix $facts $session)))
     (str:join "" ($id " · " $status " · " (work:text (work:field $state "steps"))
                   " steps · " (work:session-model $id) " · report=" (delivery:status $dir) $estate " · " (work:clip $task 120)
                   "\n    " (work:clip (work:text (work:field $state "progress")) 240)))))
(= (work:session-model $id)
   (let $task (work:json (path:join (work:dir $id) "task.json"))
     (str:join "" ((work:text (work:field $task "model")) " " (work:text (work:field $task "effort"))))))
(= (work-status)
   (let $_ (work:reap) (work:status-all)))
; Sessions in name order. One observation of every session serves the whole
; listing, so the ledger's backend selection is asked at most once.
(= (work:status-all)
   (let (EstateSessions $facts $sessions) (estate:sessions-view (system:cwd))
     (if (== $sessions ()) "WORK_SESSIONS none"
       (str:join "\n" (cons-atom "WORK_SESSIONS"
                         (map-atom $sessions $session
                           (work:line (estate:field $session "id" "") $facts $session)))))))
(= (work-status $id)
   (let* (($_ (work:reap))
          ($dir (work:dir $id)))
     (if (fs:is-file (path:join $dir "task.json"))
       (let* (((EstateView $facts $session) (estate:session-view (system:cwd) $id))
              ($detail (estate:status-detail $facts $session))
              ($plan (if (fs:is-file (path:join $dir "plan.json")) (fs:read-text (path:join $dir "plan.json")) "none"))
              ($report (if (fs:is-file (path:join $dir "report.txt")) (fs:read-text (path:join $dir "report.txt")) "")))
         (str:join "" ((work:line $id $facts $session) $detail "\nplan: " (work:clip $plan 2000)
                       (if (== $report "") "" (str:concat "\nreport:\n" (work:clip $report 6000))))))
       (str:concat "work-status: no session " $id))))
(= (work-hold $id $why) (cio:hold-session (work:dir $id) $why ""))
(= (work-hold $id $why $review) (cio:hold-session (work:dir $id) $why $review))
(= (work-archive $id $why) (cio:archive-session (work:dir $id) $why))
(= (work-steer $id $text)
   (let* (($dir (work:dir $id)) ($path (path:join $dir "inbox.jsonl")) ($line (json-stringify (JsonString $text))))
     (if (fs:is-file (path:join $dir "task.json"))
       (let $_ (cio:append-line $path $line) (str:concat "WORK_STEERED " $id))
       (str:concat "work-steer: no session " $id))))
(= (work-stop $id)
   (let* (($dir (work:dir $id)))
     (if (fs:is-file (path:join $dir "task.json"))
       (let* (($_ (cio:stop-session $dir))
              ($_ (proc:run ("systemctl" "--user" "stop" (str:concat (work:unit $id) ".scope"))
                            (system:cwd) inherit 10000 0 "merged"))
              ;; Every ending has the same report handover, including a stop
              ;; that ends the scope before its worker can record its ending.
              ;; Settling is a no-op unless the state still says running; a
              ;; report the worker wrote before it was stopped is kept in it.
              ($_ (work:ended $id $dir)))
         (str:concat "WORK_STOPPED " $id))
       (str:concat "work-stop: no session " $id))))

; A session marked running whose scope is gone ended without its report: it
; was stopped, killed or crashed, and so could not say so itself. Its report
; records that, and its requester is told as for any other ending, once.
(= (work:reap)
   (case (proc:run ("ls" "-1" "--" (work:root)) (system:cwd) inherit 10000 0 "merged")
     (((proc:exited 0 $out $err) (work:reap-ids (str:split "\n" $out) 0))
      ($other 0))))
(= (work:reap-ids $ids $count)
   (if (== $ids ()) $count
     (let $id (str:trim (car-atom $ids))
       (work:reap-ids (cdr-atom $ids) (+ $count (if (== $id "") 0 (work:reap-one $id)))))))
(= (work:reap-error $dir $where $why)
   (cio:log $dir (format-args "reap error ({}): {}" ((repr $where) (repr $why)))))
(= (work:reap-one $id)
   (let* (($dir (work:dir $id))
          ($state (work:json (path:join $dir "state.json")))
          ($_ (delivery:preserve $dir))
          ($_ (delivery:flush $dir))
          ($status (work:text (work:field $state "status")))
          ($owner (work:text (work:field $state "worker_owner"))))
     (if (== $status "running")
         (if (or (!= (work:scope-state $id) inactive)
                 (process:alive? $owner)) 0
           (case (catch (work:ended $id $dir $owner))
             (((Error $where $why)
               (let $_ (work:reap-error $dir $where $why) 0))
              ($ending
               (if (!= $ending "")
                 (let $_ (work:notify $id $dir $ending) 1)
                 0)))))
       0)))
(= (work:ended $id $dir) (cio:settle-ending $dir))
(= (work:ended $id $dir $owner) (cio:settle-ending $dir $owner))
; The same announcement coding:notify (coding_session.metta) makes when a
; session ends by itself.
(= (work:notify $id $dir $status)
   (let $_ (coding:announce $dir $status) $status))
