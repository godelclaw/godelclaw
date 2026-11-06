; Check-only entry point for (work-resume <id> "current").
; Runs on a copy in memory/coding-checks/ under METTACLAW_CODING_CHECK=1 and
; never imports provider or tool dispatchers.
!(import! &self (library lib_import))
!(import! &self (library system))
!(import! &self ./src/coding_checkpoint.metta)
!(let $dir (system:arg (- (system:argc) 1))
   (println! (coding:check-checkpoint $dir)))
