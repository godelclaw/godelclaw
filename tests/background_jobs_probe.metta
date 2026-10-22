!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The skill as the agent calls it: a background job starts at once, and once
;; it has ended the job list shows its exit status.
!(py-eval "__import__('os').environ.__setitem__('METTACLAW_JOBS_DIR', __import__('tempfile').mkdtemp(prefix='jobs-probe-'))")
!(py-eval "__import__('helper').__dict__.__setitem__('_probe_has', lambda text, part: int(str(part) in str(text)))")
!(py-eval "__import__('helper').__dict__.__setitem__('_probe_job_id', lambda text: str(text).split()[1])")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

!(let* (($started (background "echo probe-output"))
        ($_ (assertEqual (py-call (helper._probe_has $started "started")) 1))
        ($_ (py-eval "__import__('time').sleep(2.5)"))
        ($listed (jobs))
        ($_ (assertEqual (py-call (helper._probe_has $listed "exit 0")) 1))
        ($id (py-call (helper._probe_job_id $started)))
        ($output (job-output $id))
        ($_ (assertEqual (py-call (helper._probe_has $output "probe-output")) 1))
        ($_ (cut)))
    (println! BACKGROUND_JOBS_OK))
