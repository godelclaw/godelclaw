!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(initMemory)
!(initLoop)
!(println! (run-command-batch-once 71 1 (quote ((rest 1 (shell "printf scheduled >> \"$METTACLAW_TIMER_PROBE\"")) (shell "test ! -e \"$METTACLAW_TIMER_PROBE\" && printf NOT_EAGER")))))
!(py-eval "__import__('time').sleep(1.1)")
;; Construct a fresh working self as a process restart does; no pending
;; action is supplied by this test. It must come from the checkpoint.
!(let* (($boot (initLoop))
         ($p (process-periphery $boot))
         ($resumed (applyTimedContinuation $p 0))
         ($again (applyTimedContinuation $resumed 1))
         ($_ (assert (== (loop-pending-continuation $again) no-continuation))))
   (println! REST_TIMER_RESTART_OK))
