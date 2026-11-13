!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The provider is the test world's loopback peer, on the native transport.
;; It answers the first request with an empty message, a failed turn, and the
;; second with a program. Its request count is the number of prompts sent.
(= (probe:world $name) (str:concat (system:getenv-or-default "NATIVE_TEST_WORLD" "") $name))
(= (probe:reply $content)
   (str:join "" ("{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"" $content
                 "\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}")))
(= (probe:prompts) (json:parse (str:strip-python (fs:read-text (probe:world "count")))))
(= (probe:history-has $text)
   (let $path (system:getenv-or-default "METTACLAW_HISTORY_PATH" "")
     (if (fs:exists $path)
       (case (str:find (fs:read-text $path) $text) (((TextNotFound $missing) 0) ($found 1)))
       0)))
!(fs:write (probe:world "response-1.body") (probe:reply ""))
!(fs:write (probe:world "response-2.body") (probe:reply "((nop))"))
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(initMemory)
!(configure commchannel telegram)
!(tg:activity-add "" "probe" "stable turn input" False "full" "702" False)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ;; A stopped lifecycle keeps eligible input and its effects pending.
         ($_ (lifecycle:stop))
         ($disabledState
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (assertEqual (loop-budget (process-periphery $disabledState)) 0))
         ($_ (assertEqual (tg:pending-count) 1))
         ($_ (assertEqual
               (probe:history-has "stable turn input") 0))
         ($_ (assertEqual (probe:prompts) 0))
         ($_ (lifecycle:start))
         ($afterFailure
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($_ (assertEqual (tg:pending-count) 1))
         ($_ (assertEqual
               (probe:history-has "stable turn input") 0))
         ($afterSuccess
          (process-step $afterFailure
            (turnProcess (process-periphery $afterFailure))))
         ($_ (cut))
         ;; processLoop commits its checkpoint before acknowledging the batch.
         ($_ (assertEqual (persistProcessState $afterSuccess) 1))
         ($_ (assertEqual (working:turn-commit-ready) 1))
         ($_ (assertEqual (settleActivityBatch) 1))
         ($_ (assertEqual (tg:pending-count) 0))
         ($_ (assertEqual
               (probe:history-has "stable turn input") 1))
         ($_ (assertEqual
               (probe:prompts) 2)))
     (println! ACTIVITY_DELIVERY_FULL_TURN_OK))
