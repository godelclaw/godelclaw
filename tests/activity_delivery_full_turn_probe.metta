!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

!(py-eval "__import__('helper').__dict__.__setitem__('_delivery_prompts', [])")
!(py-eval "__import__('helper').__dict__.__setitem__('delivery_pending_count', lambda: len(__import__('telegram')._pending_messages))")
!(py-eval "__import__('helper').__dict__.__setitem__('delivery_prompt_count', lambda: len(__import__('helper')._delivery_prompts))")
!(py-eval "__import__('helper').__dict__.__setitem__('delivery_history_has', lambda text: int(__import__('os').path.isfile(__import__('os').environ['METTACLAW_HISTORY_PATH']) and str(text) in open(__import__('os').environ['METTACLAW_HISTORY_PATH'], encoding='utf-8').read()))")
!(py-eval "__import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('helper')._delivery_prompts.append(str(args[-1])), __import__('cognitive_health').turn_started('probe'), (__import__('cognitive_health').turn_failed('probe-failure') if len(__import__('helper')._delivery_prompts) == 1 else __import__('cognitive_health').turn_completed(7)), ('()' if len(__import__('helper')._delivery_prompts) == 1 else '((nop))'))[-1])")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(initMemory)
!(configure commchannel telegram)
!(py-call (telegram._set_last "probe" "stable turn input" False "full" 702))

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ($afterFailure
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($_ (assertEqual (py-call (helper.delivery_pending_count)) 1))
         ($_ (assertEqual
               (py-call (helper.delivery_history_has "stable turn input")) 0))
         ($afterSuccess
          (process-step $afterFailure
            (turnProcess (process-periphery $afterFailure))))
         ($_ (cut))
         ;; processLoop commits its checkpoint before acknowledging the batch.
         ($_ (assertEqual (persistProcessState $afterSuccess) 1))
         ($_ (assertEqual (py-call (helper.turn_commit_ready)) 1))
         ($_ (assertEqual (settleActivityBatch) 1))
         ($_ (assertEqual (py-call (helper.delivery_pending_count)) 0))
         ($_ (assertEqual
               (py-call (helper.delivery_history_has "stable turn input")) 1))
         ($_ (assertEqual
               (py-call (helper.delivery_prompt_count)) 2)))
     (println! ACTIVITY_DELIVERY_FULL_TURN_OK))
