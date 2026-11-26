; Background jobs: a completion wakes the agent and a committed acknowledgement
; survives a restart; a signal is the agent's choice and its result stays
; observable; with job wake off, the selected rest is kept; a restart reports
; an unknown exit and keeps old numeric ids and their evidence.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (nj:environment $dir)
   (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("OPENAI_API_KEY" "test-only-unused")
                 ("METTACLAW_JOBS_DIR" $dir) ("METTACLAW_TELEGRAM_CLIENT" "metta") ("METTACLAW_TELEGRAM_SERVICE_SOCKET" "")
                 ("METTACLAW_MAIL_SOCKET" "") ("METTACLAW_MAIL_NAME" "") ("METTACLAW_INSTANCE" "native-jobs-test")
                 ("METTACLAW_WAKE_REQUEST_PATH" "") ("METTACLAW_RECYCLE_REQUEST_PATH" "") ("METTACLAW_LIFECYCLE_PATH" "")
                 ("METTACLAW_ENGINE_STATE_PATH" "") ("METTACLAW_LOOP_MODE_PATH" ""))
                (nt:environment $dir)))
; Run $code over the job channel; True when it completed with JOBS_OK.
(= (nj:guest $repo $home $dir $code)
   (let* (($program (path:join $home (str:concat "jobs-" (str:concat (str:byte-slice (system:uuid4-hex) 0 8) ".metta"))))
          ($_ (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n"
                                               "!(import! &self " (path:join $repo "src/utils_cetta.metta") ")\n"
                                               "!(import! &self " (path:join $repo "channels/telegram.metta") ")\n" $code)))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo (nj:environment $dir) 20000 1048576)
       (((proc:exited 0 $out $err) (and (nt:contains $out "JOBS_OK") (not (nt:contains $err "Assertion failed"))))
        ($bad (progn (println! (JOBS_GUEST_FAILED $bad)) False))))))
(= (nj:records $dir)
   (map-atom (filter-atom (fs:entries $dir) $n (str:ends-with $n ".json")) $n (json:parse (fs:read-text (path:join $dir $n)))))
(= (nj:has? $record $key) (case $record (((JsonObject $pairs) (nj:key-in? $pairs $key)) ($o False))))
(= (nj:key-in? $pairs $key)
   (if (== $pairs ()) False (let (JsonPair $k $v) (car-atom $pairs) (if (== $k $key) True (nj:key-in? (cdr-atom $pairs) $key)))))
(= (nj:dir $home $tag) (let $d (path:join $home $tag) (progn (fs:make-directories $d 448) $d)))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; A completion wakes the agent; the committed acknowledgement survives a restart.
         ($done (nj:dir $home "done"))
         ($_ (nt:require a-completion-wakes-the-agent-and-is-acknowledged
               (nj:guest $repo $home $done (str:join "\n" (
                 "!(println! (background \"printf native-body; sleep .07; exit 7\"))"
                 "!(assert (== (tg:sleep-until-message 30 True) \"woken by job\"))"
                 "!(println! (jobs:receive))"
                 "!(assert (jobs:active?))"
                 "!(assert (jobs:pending?))"
                 "!(assert (== (jobs:ack) 1))"
                 "!(assert (not (jobs:pending?)))"
                 "!(println! JOBS_OK)" "")))))
         ($records (nj:records $done))
         ($_ (nt:require the-job-record-keeps-exit-notice-and-log
               (and (== (size-atom $records) 1)
                    (let $record (car-atom $records)
                      (and (== (json:get $record "exit") 7)
                           (and (== (json:get $record "notified") True)
                                (== (fs:read-text (json:get $record "log")) "native-body")))))))
         ($_ (nt:require the-acknowledgement-survives-a-restart
               (nj:guest $repo $home $done "!(jobs:reset)\n!(assert (not (jobs:pending?)))\n!(println! (jobs 0))\n!(println! JOBS_OK)\n")))
         ; A signal is the agent's choice; the result stays observable.
         ($stopped (nj:dir $home "stopped"))
         ($_ (nt:require a-stopped-job-wakes-and-reports
               (nj:guest $repo $home $stopped (str:join "\n" (
                 "!(background \"sleep 30\")"
                 "!(let $ids (collapse (match &self (job:live $id $_) $id))"
                 "   (println! (job-stop (car-atom $ids) 9)))"
                 "!(assert (== (tg:sleep-until-message 30 True) \"woken by job\"))"
                 "!(println! (jobs:receive))"
                 "!(println! JOBS_OK)" "")))))
         ($_ (nt:require the-signal-is-recorded
               (let $record (car-atom (nj:records $stopped))
                 (and (== (json:get $record "state") "signaled") (== (json:get $record "exit") 9)))))
         ; With job wake off, the selected rest is kept.
         ($rest (nj:dir $home "rest"))
         ($_ (nt:require a-disabled-job-wake-keeps-the-rest
               (nj:guest $repo $home $rest (str:join "\n" (
                 "!(background \"sleep .02\")"
                 "!(let* (($before (get_time)) ($answer (tg:sleep-until-message 0.08 False)) ($after (get_time)))"
                 "   (progn (assert (>= (- $after $before) 0.07)) (println! $answer)))"
                 "!(assert (jobs:pending?))"
                 "!(println! JOBS_OK)" "")))))
         ; A restart reports an unknown exit and keeps old ids and evidence.
         ($old (nj:dir $home "old")) ($log (path:join $old "12.log"))
         ($_ (fs:write $log "retained evidence"))
         ($_ (fs:write (path:join $old "12.json")
               (json-stringify (JsonObject ((JsonPair "id" (JsonString "12")) (JsonPair "command" (JsonString "old job"))
                                            (JsonPair "state" (JsonString "running")) (JsonPair "pid" (JsonNumber "0"))
                                            (JsonPair "started" (JsonNumber "1")) (JsonPair "log" (JsonString $log)))))))
         ($_ (nt:require a-restart-reports-the-old-job
               (nj:guest $repo $home $old "!(jobs:reset)\n!(assert (jobs:pending?))\n!(println! (jobs:receive))\n!(println! (job-output 12 0))\n!(println! JOBS_OK)\n")))
         ($recovered (json:parse (fs:read-text (path:join $old "12.json"))))
         ($_ (nt:require the-exit-is-unknown-and-the-evidence-kept
               (and (nt:contains (json:get $recovered "state") "exit unknown")
                    (and (not (nj:has? $recovered "exit")) (== (fs:read-text $log) "retained evidence"))))))
   (println! UNIT_NATIVE_JOBS_OK))
