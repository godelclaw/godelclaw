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
!(import! &self ./src/coding_io.py)
!(import! &self ./src/coding_delivery.metta)
!(import! &self ./src/process_identity_cetta.metta)

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
(= (work:quote $text) (py-call (shlex.quote $text)))
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
   (let* (($stamp (py-call (time.strftime "%Y%m%d-%H%M%S")))
          ($salt (py-call (secrets.token_hex 2)))
          ($id (format-args "work-{}-{}" ($stamp $salt)))
          ($dir (work:dir $id))
          ($_ (proc:run ("mkdir" "-p" "--" $dir) (system:cwd) inherit 10000 0 "merged"))
          ($where (if (== $workdir "") (system:getenv-or-default "HOME" "/") $workdir))
          ($requester (system:getenv-or-default "METTACLAW_MAIL_NAME" ""))
          ($task-json (JsonObject ((JsonPair "task" (JsonString $task)) (JsonPair "workdir" (JsonString $where))
                                   (JsonPair "requester" (JsonString $requester)) (JsonPair "effort" (JsonString $effort))
                                   (JsonPair "requester_space" (JsonString (system:getenv-or-default "METTACLAW_MAIL_SPACE" "agent-mail")))
                                   (JsonPair "model" (JsonString $model)))))
          ($_ (fs:update-text (path:join $dir "task.json") "replace" (json-stringify $task-json) "" ""))
          ($_ (work:launch $id)))
     (str:join "" ("WORK_STARTED " $id ". It runs on its own and mails you its report when done; "
                   "(work-status \"" $id "\") shows its progress, (work-steer \"" $id "\" \"text\") guides it, "
                   "(work-stop \"" $id "\") stops it."))))

(= (work:launch $id)
   (let* (($dir (work:dir $id))
          ($tmp (path:join $dir "tmp"))
          ($_ (proc:run ("mkdir" "-p" "--" $tmp) (system:cwd) inherit 10000 0 "merged"))
          ($runtime (py-call (coding_io.freeze_runtime (system:cwd) $dir)))
          ;; The session reads its model from its own state file, never the
          ;; agent's; a session from before models were recorded keeps the
          ;; agent's model of the moment it resumes.
          ($model (format-args "{}" ((py-call (coding_io.session_model $dir (work:agent-model))))))
          ($binary (py-call (coding_io.runtime_binary $dir)))
          ($pythonpath (py-call (coding_io.runtime_pythonpath $runtime (system:cwd))))
          ($ledger (py-call (builtins.str (py-call (llm_policy.ledger_path)))))
          ;; The session shares the account's ledger and usage tally, as
          ;; Codex sub-agents share rate limits, but keeps its own health,
          ;; last-completion and restart files: its calls are not the agent's turns.
          ($own (str:join " " ("env" (work:quote (str:concat "METTACLAW_LLM_LEDGER_PATH=" $ledger))
                                    (work:quote (str:concat "PYTHONPATH=" $pythonpath))
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
                                    "METTACLAW_CODING_SESSION=1"
                                    "METTACLAW_OBSERVED_STRATEGY=0")))
          ($launch (str:join "" ("cd " (work:quote $runtime) " || exit 1; systemd-run --user --scope --quiet --collect --unit=" (work:quote (work:unit $id)) " -- " $own " "
                                 (work:quote $binary) " --lang petta --import-mode ancestor-walk coding_main.metta -- " (work:quote $dir)
                                 " > " (work:quote (path:join $dir "run.log")) " 2>&1 &")))
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
; The named systemd scope prevents a second worker for the same session.
(= (work-resume $id)
   (let* (($dir (work:dir $id)) ($task-path (path:join $dir "task.json")))
     (if (not (fs:is-file $task-path)) (str:concat "work-resume: no session " $id)
       (if (work:running? $id) (str:concat "WORK_RUNNING " $id)
       (let $engine (format-args "{}" ((py-call (coding_io.engine_problem $dir))))
       (if (not (== $engine "")) (str:join "" ("work-resume: " $engine "; start a new session for this task"))
         (let* (($stop (path:join $dir "stop"))
                ($recycle (path:join $dir "recycle.requested"))
                ($_ (delivery:preserve $dir))
                ($_ (if (fs:exists $stop) (proc:run ("rm" "--" $stop) $dir inherit 10000 0 "merged") ()))
                ($_ (if (fs:exists $recycle) (proc:run ("rm" "--" $recycle) $dir inherit 10000 0 "merged") ()))
                ;; The last ending's report stays as evidence, out of the way
                ;; of this run's own report.
                ($report (path:join $dir "report.txt"))
                ($_ (if (fs:is-file $report) (proc:run ("mv" "--" $report (path:join $dir "report.previous.txt")) $dir inherit 10000 0 "merged") ()))
                ($line (json-stringify (JsonString "Resume this session from its existing evidence and plan. Verify uncertain effects before choosing new actions.")))
                ($_ (py-call (coding_io.append_line (path:join $dir "inbox.jsonl") $line)))
                ($_ (work:launch $id)))
           (str:concat "WORK_RESUMING " $id))))))))

(= (work:line $id)
   (let* (($state (work:json (path:join (work:dir $id) "state.json")))
          ($task (work:text (work:field (work:json (path:join (work:dir $id) "task.json")) "task"))))
     (str:join "" ($id " · " (work:text (work:field $state "status")) " · " (work:text (work:field $state "steps"))
                   " steps · " (work:session-model $id) " · report=" (delivery:status (work:dir $id)) " · " (work:clip $task 120)
                   "\n    " (work:clip (work:text (work:field $state "progress")) 240)))))
(= (work:session-model $id)
   (let $task (work:json (path:join (work:dir $id) "task.json"))
     (str:join "" ((work:text (work:field $task "model")) " " (work:text (work:field $task "effort"))))))
(= (work:lines $ids $out)
   (if (== $ids ()) $out
     (let $id (car-atom $ids)
       (work:lines (cdr-atom $ids) (if (== (str:trim $id) "") $out (str:join "\n" ($out (work:line $id))))))))
(= (work-status)
   (let $_ (work:reap) (work:status-all)))
(= (work:status-all)
   (case (proc:run ("ls" "-1" "--" (work:root)) (system:cwd) inherit 10000 0 "merged")
     (((proc:exited 0 $out $err) (let $text (work:lines (str:split "\n" $out) "WORK_SESSIONS")
                                   (if (== $text "WORK_SESSIONS") "WORK_SESSIONS none" $text)))
      ($other "WORK_SESSIONS none"))))
(= (work-status $id)
   (let* (($_ (work:reap))
          ($dir (work:dir $id))
          ($plan (if (fs:is-file (path:join $dir "plan.json")) (fs:read-text (path:join $dir "plan.json")) "none"))
          ($report (if (fs:is-file (path:join $dir "report.txt")) (fs:read-text (path:join $dir "report.txt")) "")))
     (if (fs:is-file (path:join $dir "task.json"))
       (str:join "" ((work:line $id) "\nplan: " (work:clip $plan 2000)
                     (if (== $report "") "" (str:concat "\nreport:\n" (work:clip $report 6000)))))
       (str:concat "work-status: no session " $id))))
(= (work-steer $id $text)
   (let* (($dir (work:dir $id)) ($path (path:join $dir "inbox.jsonl")) ($line (json-stringify (JsonString $text))))
     (if (fs:is-file (path:join $dir "task.json"))
       (let $_ (py-call (coding_io.append_line $path $line)) (str:concat "WORK_STEERED " $id))
       (str:concat "work-steer: no session " $id))))
(= (work-stop $id)
   (let* (($dir (work:dir $id)))
     (if (fs:is-file (path:join $dir "task.json"))
       (let* (($_ (fs:update-text (path:join $dir "stop") "replace" "" "" ""))
              ($_ (proc:run ("systemctl" "--user" "stop" (str:concat (work:unit $id) ".scope"))
                            (system:cwd) inherit 10000 0 "merged"))
              ;; Every ending has the same report handover, including a stop
              ;; that ends the scope before its worker can write a report.
              ($_ (if (fs:is-file (path:join $dir "report.txt")) ()
                    (work:ended $id $dir "stopped" "Stopped by the requester."))))
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
(= (work:reap-one $id)
   (let* (($dir (work:dir $id))
          ($state (work:json (path:join $dir "state.json")))
          ($_ (delivery:preserve $dir))
          ($_ (delivery:flush $dir))
          ($status (work:text (work:field $state "status"))))
     (if (== $status "running")
         (if (or (!= (work:scope-state $id) inactive)
                 (process:alive? (work:text (work:field $state "worker_owner")))) 0
           (let* (($ending (if (fs:is-file (path:join $dir "stop")) "stopped" "interrupted"))
                  ($_ (work:ended $id $dir $ending
                        (if (== $ending "stopped") "Stopped by the requester."
                          "The session ended without finishing its recorded ending: its process is gone (it was stopped, killed or crashed).")))
                  ($_ (work:notify $id $dir $ending)))
             1))
       0)))
(= (work:ended $id $dir $status $why)
   (let* (($state (work:json (path:join $dir "state.json")))
          ($progress (work:text (work:field $state "progress")))
          ($plan (if (fs:is-file (path:join $dir "plan.json")) (work:clip (fs:read-text (path:join $dir "plan.json")) 2000) "none recorded"))
          ($saved (if (fs:is-file (path:join $dir "report.txt")) (fs:read-text (path:join $dir "report.txt")) ""))
          ($report (str:join "" ($why "\n\nLast progress:\n" (if (== $progress "") "none recorded" $progress)
                                 (if (== $saved "") "" (str:concat "\n\nSaved report:\n" $saved))
                                 "\n\nPlan:\n" $plan "\n\nResume without replaying uncertain effects:\n(work-resume \"" $id "\")")))
          ($identity (delivery:identity $dir $state $status))
          ($_ (py-call (coding_io.mark_state $dir $status $report $identity))))
     $status))
; The same announcement coding:notify (coding_session.metta) makes when a
; session ends by itself.
(= (work:notify $id $dir $status)
   (let $_ (coding:announce $dir $status) $status))
