!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)
(= (assertEqual $a $b) (assert (== $a $b)))

;; New input arrives while the first scripted provider call is outstanding.
;; It queues for the next step without discarding the emitted command.
!(scripted:answer 1 "((shell \"printf EMITTED_COMMAND\"))")
!(scripted:answer 2 "((help shell))")
;; The input arrives with the first call: while the provider has answered
;; exactly one request, a stimulus is pending.
!(py-eval "__import__('strategy_host').__dict__.__setitem__('_stimulus_free', lambda: open(__import__('os').environ['NATIVE_TEST_WORLD'] + 'count').read().strip() != '1')")
!(py-eval "__import__('helper').__dict__.__setitem__('strategy_stale_ok', lambda: int(len(__import__('strategy_host').ledger().projection()['actions']) == 1))")
!(py-eval "__import__('helper').__dict__.__setitem__('strategy_recovered_ok', lambda: int(len(__import__('strategy_host').ledger().projection()['actions']) == 2 and len(__import__('strategy_host').ledger().active()) > 0))")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ($first (process-step $initial (turnProcess (process-periphery $initial))))
         ($_ (assertEqual (py-call (helper.strategy_stale_ok)) 1))
         ($_ (assertEqual (scripted:calls) 1))
         ($second (process-step $first (turnProcess (process-periphery $first))))
         ($_ (assertEqual (py-call (helper.strategy_recovered_ok)) 1))
         ($_ (assertEqual (scripted:calls) 2))
         ($_ (assertEqual (scripted:prompt-has 1 "TASK_CONTEXT") 1))
         ($_ (assertEqual (scripted:prompt-has 2 "TASK_CONTEXT") 1)))
   (println! OBSERVED_STRATEGY_OK))
