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
(= (work:clip $text $limit)
   (if (<= (str:char-length $text) $limit) $text (str:concat (str:char-slice $text 0 $limit) " …")))

(= (work $task) (work $task ""))
(= (work $task $workdir)
   (let* (($stamp (py-call (time.strftime "%Y%m%d-%H%M%S")))
          ($salt (py-call (secrets.token_hex 2)))
          ($id (format-args "work-{}-{}" ($stamp $salt)))
          ($dir (work:dir $id))
          ($_ (work:shell (str:concat "mkdir -p " $dir)))
          ($where (if (== $workdir "") (system:getenv-or-default "HOME" "/") $workdir))
          ($requester (system:getenv-or-default "METTACLAW_MAIL_NAME" ""))
          ($task-json (JsonObject ((JsonPair "task" (JsonString $task)) (JsonPair "workdir" (JsonString $where))
                                   (JsonPair "requester" (JsonString $requester)) (JsonPair "effort" (JsonString "high")))))
          ($_ (fs:update-text (path:join $dir "task.json") "replace" (json-stringify $task-json) "" ""))
          ($binary (system:getenv-or-default "CETTA_BIN" "cetta"))
          ;; The session shares the account's ledger and usage tally, as
          ;; Codex sub-agents share rate limits, but keeps its own health,
          ;; last-completion and restart files: its calls are not the agent's turns.
          ($own (str:join "" ("env METTACLAW_COGNITIVE_HEALTH_PATH='" $dir "/health.json'"
                              " METTACLAW_LLM_COMPLETION_PATH='" $dir "/completion.json'"
                              " METTACLAW_RECYCLE_REQUEST_PATH='" $dir "/recycle.requested'"
                              " METTACLAW_WAKE_REQUEST_PATH='" $dir "/wake.requested'"
                              " METTACLAW_OBSERVED_STRATEGY=0 ")))
          ($launch (str:join "" ("systemd-run --user --scope --quiet --collect --unit=" (work:unit $id) " -- " $own
                                 "'" $binary "' --lang petta --import-mode ancestor-walk coding_main.metta -- '" $dir
                                 "' > '" $dir "/run.log' 2>&1 &")))
          ($_ (work:shell $launch)))
     (str:join "" ("WORK_STARTED " $id ". It runs on its own and mails you its report when done; "
                   "(work-status \"" $id "\") shows its progress, (work-steer \"" $id "\" \"text\") guides it, "
                   "(work-stop \"" $id "\") stops it."))))

(= (work:line $id)
   (let* (($state (work:json (path:join (work:dir $id) "state.json")))
          ($task (work:text (work:field (work:json (path:join (work:dir $id) "task.json")) "task"))))
     (str:join "" ($id " · " (work:text (work:field $state "status")) " · " (work:text (work:field $state "steps"))
                   " steps · " (work:clip $task 120) "\n    " (work:clip (work:text (work:field $state "progress")) 240)))))
(= (work:lines $ids $out)
   (if (== $ids ()) $out
     (let $id (car-atom $ids)
       (work:lines (cdr-atom $ids) (if (== (str:trim $id) "") $out (str:join "\n" ($out (work:line $id))))))))
(= (work-status)
   (case (work:shell (str:concat "ls -1 " (work:root) " 2>/dev/null"))
     (((proc:exited 0 $out $err) (let $text (work:lines (str:split "\n" $out) "WORK_SESSIONS")
                                   (if (== $text "WORK_SESSIONS") "WORK_SESSIONS none" $text)))
      ($other "WORK_SESSIONS none"))))
(= (work-status $id)
   (let* (($dir (work:dir $id))
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
              ($_ (work:shell (str:join "" ("systemctl --user stop " (work:unit $id) ".scope 2>/dev/null; true")))))
         (str:concat "WORK_STOPPED " $id))
       (str:concat "work-stop: no session " $id))))
