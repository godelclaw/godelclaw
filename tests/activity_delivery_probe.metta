!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

!(configure commchannel telegram)
!(py-call (telegram._set_last "probe" "stable input" False "full" 701))

;; Both observations cross the real SWI-PeTTa/Janus boundary. Relational
;; re-entry must see one stable prepared batch; only the explicit settlement
;; receipt removes it.
!(let* (($first (eval (receive)))
         ($_ (cut))
         ($second (eval (receive)))
         ($_ (cut))
         ($_ (println! (ACTIVITY_DELIVERY_FIRST $first)))
         ($_ (println! (ACTIVITY_DELIVERY_SECOND $second)))
         ($_ (assertEqual $first $second))
         ($_ (assertEqual (py-call (helper.text_nonempty $first)) 1))
         ($ack (py-call (telegram.ackActivityBatch)))
         ($_ (println! (ACTIVITY_DELIVERY_ACK $ack)))
         ($_ (assertEqual $ack 1))
         ($afterAck (eval (receive)))
         ($_ (cut))
         ($_ (println! (ACTIVITY_DELIVERY_AFTER_ACK $afterAck)))
         ($_ (assertEqual (py-call (helper.text_nonempty $afterAck)) 0)))
     (println! ACTIVITY_DELIVERY_OK))
