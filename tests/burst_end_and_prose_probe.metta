!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Each turn has its own number, as the loop gives it.
(= (next-turn $periphery)
   (coordinate-set iteration (+ 1 (loop-iteration $periphery)) $periphery))

;; Renewal preserves unfinished work even when the previous burst ended with
;; nop. Prose outside
;; the command envelope is shown to the next turn and kept in history, marked
;; undelivered. The model answers from a script.
!(py-eval "(__import__('helper').__dict__.__setitem__('_burst_prompts', []), __import__('helper').__dict__.__setitem__('_burst_replies', ['Hello there, I finished.', '((nop))', '((nop))']), __import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('helper')._burst_prompts.append(str(args[-1])), __import__('cognitive_health').turn_started('probe'), __import__('cognitive_health').turn_completed(3), __import__('helper')._burst_replies.pop(0))[-1]))")
!(py-eval "__import__('helper').__dict__.__setitem__('burst_prompt_has', lambda i, text: int(len(__import__('helper')._burst_prompts) > int(i) and str(text) in __import__('helper')._burst_prompts[int(i)]))")
!(py-eval "__import__('helper').__dict__.__setitem__('burst_history_has', lambda text: int(__import__('os').path.isfile(__import__('os').environ['METTACLAW_HISTORY_PATH']) and str(text) in open(__import__('os').environ['METTACLAW_HISTORY_PATH'], encoding='utf-8').read()))")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($start (process-periphery (initLoop)))
         ;; A renewal keeps how the finished burst ended and starts anew.
         ($after-nop (applyAutonomousBurst (coordinate-set burst-end nop $start) True))
         ($_ (assertEqual (coordinate-get-or renewed-after none $after-nop) nop))
         ($_ (assertEqual (coordinate-get-or burst-end none $after-nop) budget))
         ($ran-out (applyAutonomousBurst $start True))
         ($_ (assertEqual (coordinate-get-or renewed-after none $ran-out) budget))
         ;; A renewal after running out continues the task. The model
         ;; answers in prose, which reaches no one.
         ($first (cognitiveTurn $ran-out "" False False True False))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 0 "NO_ADDITIONAL_USER_INPUT_CONTINUE_THE_CURRENT_TASK")) 1))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 0 "TASK_COMPLETE_PICK_A_GOAL")) 0))
         ($_ (assertEqual (py-call (helper.burst_history_has "UNDELIVERED")) 1))
         ($_ (assertEqual (py-call (helper.burst_history_has "Hello there, I finished.")) 1))
         ;; The next turn sees that prose, marked undelivered; it ends the
         ;; burst with nop.
         ($second (cognitiveTurn (next-turn $first) "" False False False False))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 1 "UNDELIVERED")) 1))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 1 "Hello there, I finished.")) 1))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 1 "NOT_DELIVERED_TO_ANY_CHANNEL_USE_send")) 1))
         ($_ (assertEqual (coordinate-get-or burst-end none $second) nop))
         ;; The renewal after nop reconsiders work without claiming completion.
         ($third (cognitiveTurn (applyAutonomousBurst (next-turn $second) True) "" False False True False))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 2 "TASK_COMPLETE_PICK_A_GOAL")) 0))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 2 "RECONSIDER_UNFINISHED_WORK")) 1))
         ($_ (assertEqual (py-call (helper.burst_prompt_has 2 "CONTINUE_THE_CURRENT_TASK")) 0)))
     (println! BURST_END_AND_PROSE_OK))
