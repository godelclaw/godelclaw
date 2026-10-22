!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The same weak process runner executes both turns. Both modes permit the
;; model's batch. The first command in turn one injects a new inbound message
;; into chat probe: the help query that follows still runs, and only the
;; reply into that chat is withheld. With no new input in turn two, the full
;; batch runs.
(= (probe-stimulus)
   (py-call (telegram._set_last "probe" "new stimulus" False "full" 123)))
!(py-eval "__import__('helper').__dict__.__setitem__('_batch_probe_prompts', [])")
!(py-eval "__import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('helper')._batch_probe_prompts.append(str(args[-1])), __import__('cognitive_health').turn_started('probe'), __import__('cognitive_health').turn_completed(32), ('((probe-stimulus) (help _quote_query_quote_) (send-telegram-chat _quote_probe_quote_ _quote_stale-reply_quote_))' if len(__import__('helper')._batch_probe_prompts) == 1 else '((help _quote_send_quote_) (help _quote_query_quote_))'))[-1])")
!(py-eval "__import__('helper').__dict__.__setitem__('stimulus_batch_probe_ok', lambda feedback: int(str(feedback).count('COMMAND_RETURN:') == 3 and 'COMMAND_BATCH_INTERRUPTED:' not in str(feedback) and 'stale-reply' in str(feedback)))")
!(py-eval "__import__('helper').__dict__.__setitem__('stable_batch_probe_ok', lambda feedback: int(str(feedback).count('COMMAND_RETURN:') == 2 and 'COMMAND_BATCH_INTERRUPTED:' not in str(feedback)))")
!(py-eval "__import__('helper').__dict__.__setitem__('batch_probe_prompt_limits_ok', lambda: int(len(__import__('helper')._batch_probe_prompts) == 2 and 'at most 5 command(s)' not in __import__('helper')._batch_probe_prompts[0] and 'at most 5 command(s)' not in __import__('helper')._batch_probe_prompts[1]))")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ($after-agent
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($agent-feedback
          (loop-last-results (process-periphery $after-agent)))
         ($_ (assertEqual
               (py-call (helper.stimulus_batch_probe_ok $agent-feedback))
               1))
         ($_ (py-call (loop_modes.set_mode "coding")))
         ($after-coding
          (process-step $after-agent
            (turnProcess (process-periphery $after-agent))))
         ($_ (cut))
         ($coding-feedback
          (loop-last-results (process-periphery $after-coding)))
         ($_ (assertEqual
               (py-call (helper.stable_batch_probe_ok $coding-feedback))
               1))
         ($_ (assertEqual
               (py-call (helper.batch_probe_prompt_limits_ok))
               1))
         ($_ (assertEqual (process-root $after-coding)
                          weak-process-core-v1)))
     (println! INTERACTIVE_BATCH_POLICY_OK))
