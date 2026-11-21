; Coding sessions started from the agent loop (eager entrypoint for direct importers).
!(import! &self ./src/coding_work_core.metta)

(SettingSpec 1 "work" "context" "open" "mode" (Choice ("open" "summary" "off")) "persistent"
  "Coding-session context source mode: open lists due open sessions, summary shows counts and verbs, off omits the source."
  work-setting work-setting)
(SettingSpec 1 "work" "resume" "pinned" "mode" (Choice ("pinned" "current")) "persistent"
  "Coding-session default resume mode: pinned resumes on the session's pin, current re-pins to the current runtime after checking a copy of the checkpoint."
  work-setting work-setting)

(= (work-setting) (work-core:setting-0))
(= (work-setting $key) (work-core:setting-1 $key))
(= (work-setting $key $raw) (work-core:setting-2 $key $raw))
(= (work $task) (work-core:work-1 $task))
(= (work $task $workdir) (work-core:work-2 $task $workdir))
(= (work $task $workdir $model) (work-core:work-3 $task $workdir $model))
(= (work $task $workdir $model $effort) (work-core:work-4 $task $workdir $model $effort))
(= (work-resume $id) (work-core:resume-1 $id))
(= (work-resume $id $mode) (work-core:resume-2 $id $mode))
(= (work-status) (work-core:status-0))
(= (work-status $id) (work-core:status-1 $id))
(= (work-hold $id $why) (work-core:hold-2 $id $why))
(= (work-hold $id $why $review) (work-core:hold-3 $id $why $review))
(= (work-archive $id $why) (work-core:archive-2 $id $why))
(= (work-steer $id $text) (work-core:steer-2 $id $text))
(= (work-stop $id) (work-core:stop-1 $id))
(= (work:reap) (work-core:reap))
