!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The skill as the agent calls it: a background job starts at once, and once
;; it has ended the job list shows its exit status.
;; The runner gives the probe its own METTACLAW_JOBS_DIR.
(= (jobs-probe:has $text $part) (case (str:find $text $part) (((TextNotFound $_) False) ($at True))))
!(initMemory)
!(configure commchannel telegram)

!(let* (($started (background "echo probe-output"))
        ($_ (assertEqual (jobs-probe:has $started "started") True))
        ($_ (host-native:sleep-ms 2500))
        ($listed (jobs))
        ($_ (assertEqual (jobs-probe:has $listed "exit 0") True))
        ($id (index-atom (str:split " " $started) 1))
        ($output (job-output $id))
        ($_ (assertEqual (jobs-probe:has $output "probe-output") True))
        ($_ (cut)))
    (println! BACKGROUND_JOBS_OK))
