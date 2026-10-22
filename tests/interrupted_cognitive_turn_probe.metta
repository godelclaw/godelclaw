!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (probe-stimulus)
   (py-call (telegram._set_last "probe" "new stimulus" False "full" 811)))
!(py-eval "__import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('cognitive_health').turn_started('probe'), __import__('cognitive_health').turn_completed(64), '((probe-stimulus) (pin _quote_pin-after-new-input-lands_quote_) (send-telegram-chat _quote_probe_quote_ _quote_stale-reply-must-not-be-sent_quote_) (pin _quote_pin-after-withheld-send-must-not-land_quote_))')[-1])")
!(py-eval "__import__('helper').__dict__.__setitem__('_working_boot_cache', {})")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($initial (initLoop))
         ($after (process-step $initial
                   (turnProcess (process-periphery $initial))))
         ($_ (cut))
         ($feedback (loop-last-results (process-periphery $after)))
         ($_ (assertEqual
               (py-call (helper.text_nonempty $feedback)) 1)))
     (println! INTERRUPTED_COGNITIVE_TURN_OK))
