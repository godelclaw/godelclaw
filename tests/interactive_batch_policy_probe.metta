!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The same weak process runner executes both turns, and the mode permits the
;; model's batch; the runner boots this probe once in each mode. The first
;; command in turn one injects a new inbound message into chat probe: the
;; help query that follows still runs, and only the reply into that chat is
;; withheld. With no new input in turn two, the full batch runs.
(= (probe-stimulus)
   (tg:activity-add "" "probe" "new stimulus" False "full" "123" False))
!(scripted:answer 1 "((probe-stimulus) (help _quote_query_quote_) (send-telegram-chat _quote_probe_quote_ _quote_stale-reply_quote_))")
!(scripted:answer 2 "((help _quote_send_quote_) (help _quote_query_quote_))")
!(initMemory)
!(configure commchannel telegram)

;; How many commands a turn's feedback returned, and whether it holds $text.
(= (probe:returns $feedback) (- (size-atom (str:split "COMMAND_RETURN:" $feedback)) 1))
(= (probe:has $feedback $text) (case (str:find $feedback $text) (((TextNotFound $missing) False) ($found True))))

!(let* (($_ (assertEqual (loop-mode:current) (loop-mode:canonical (system:getenv-or-default "NATIVE_PROBE_MODE" ""))))
         ($initial (initLoop))
         ($after-first
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($first (loop-last-results (process-periphery $after-first)))
         ($_ (assertEqual (probe:returns $first) 3))
         ($_ (assertEqual (probe:has $first "COMMAND_BATCH_INTERRUPTED:") False))
         ($_ (assertEqual (probe:has $first "stale-reply") True))
         ($after-second
          (process-step $after-first
            (turnProcess (process-periphery $after-first))))
         ($_ (cut))
         ($second (loop-last-results (process-periphery $after-second)))
         ($_ (assertEqual (probe:returns $second) 2))
         ($_ (assertEqual (probe:has $second "COMMAND_BATCH_INTERRUPTED:") False))
         ;; Neither prompt limits the batch.
         ($_ (assertEqual (scripted:calls) 2))
         ($_ (assertEqual (scripted:prompt-has 1 "at most 5 command(s)") 0))
         ($_ (assertEqual (scripted:prompt-has 2 "at most 5 command(s)") 0))
         ($_ (assertEqual (process-root $after-second)
                          weak-process-core-v1)))
     (println! INTERACTIVE_BATCH_POLICY_OK))
