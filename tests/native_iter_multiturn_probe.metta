!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(py-eval "__import__('sys').path.insert(0, 'tests')")
!(import! &self ./tests/native_iter_scenario.py)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Four ordinary turns through the actual loop/request path.  The test helper
;; supplies only a scripted model and disposable transformation files; Iter
;; execution and request folding remain native SWI-PeTTa.
!(initMemory)
!(configure commchannel telegram)
!(py-call (native_iter_scenario.install))

!(let* (($_ (py-call (loop_modes.set_mode "coding")))
         ($_ (py-call (native_iter_scenario.select_stage "first")))
         ($initial (initLoop))
         ($after-first
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (py-call (native_iter_scenario.select_stage "failure")))
         ($after-second
          (process-step $after-first
            (turnProcess (process-periphery $after-first))))
         ($_ (py-call (native_iter_scenario.select_stage "timeout")))
         ($after-third
          (process-step $after-second
            (turnProcess (process-periphery $after-second))))
         ($_ (py-call (native_iter_scenario.select_stage "fourth")))
         ($after-fourth
          (process-step $after-third
            (turnProcess (process-periphery $after-third))))
         ($_ (assertEqual (process-root $after-fourth) weak-process-core-v1))
         ($_ (assertEqual (py-call (native_iter_scenario.verdict)) 1))
         ($_ (cut)))
     (println! NATIVE_ITER_MULTITURN_OK))
