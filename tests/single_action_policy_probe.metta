!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Scripted provider replies drive two actual cognitive turns. The first
;; command contains two shell operations; its separate suffix must not run.
;; The second request must contain the result and the deferred disposition.
!(py-eval "__import__('helper').__dict__.__setitem__('_single_action_prompts', [])")
!(py-eval "__import__('helper').__dict__.__setitem__('_single_action_first', '((shell \"printf FIRST_SUBSTEP; printf SECOND_SUBSTEP\") (shell \"printf SUFFIX_EXECUTED\"))')")
!(py-eval "__import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('helper')._single_action_prompts.append(str(args[-1])), __import__('cognitive_health').turn_started('probe'), __import__('cognitive_health').turn_completed(32), (__import__('helper')._single_action_first if len(__import__('helper')._single_action_prompts) == 1 else '((help shell))'))[-1])")
!(py-eval "__import__('helper').__dict__.__setitem__('single_action_feedback_ok', lambda feedback: int(str(feedback).count('COMMAND_RETURN:') == 1 and 'FIRST_SUBSTEPSECOND_SUBSTEP' in str(feedback) and 'COMMAND_BATCH_DEFERRED:' in str(feedback)))")
!(py-eval "__import__('helper').__dict__.__setitem__('single_action_observed_ok', lambda: int(len(__import__('helper')._single_action_prompts) == 2 and all('at most 1 command(s)' in p and 'One command may run a multi-step script' in p for p in __import__('helper')._single_action_prompts) and 'FIRST_SUBSTEPSECOND_SUBSTEP' in __import__('helper')._single_action_prompts[1] and 'COMMAND_BATCH_DEFERRED:' in __import__('helper')._single_action_prompts[1]))")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ($_ (assertEqual (policy-command-batch-limit godelclaw) 1))
         ($_ (assertEqual (policy-command-batch-limit coding) 1))
         ($after-first
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (assertEqual
               (py-call (helper.single_action_feedback_ok
                 (loop-last-results (process-periphery $after-first)))) 1))
         ($after-second
          (process-step $after-first
            (turnProcess (process-periphery $after-first))))
         ($_ (assertEqual
               (py-call (helper.single_action_observed_ok)) 1))
         ($_ (assertEqual (process-root $after-second)
                          weak-process-core-v1)))
     (println! SINGLE_ACTION_POLICY_OK))
