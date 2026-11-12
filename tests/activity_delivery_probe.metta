!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

!(configure commchannel telegram)
!(tg:activity-add "" "probe" "stable input" False "full" "701" False)

;; Relational re-entry must see one stable prepared batch; only the explicit
;; settlement receipt removes it.
!(let* (($first (eval (receive)))
         ($_ (cut))
         ($second (eval (receive)))
         ($_ (cut))
         ($_ (println! (ACTIVITY_DELIVERY_FIRST $first)))
         ($_ (println! (ACTIVITY_DELIVERY_SECOND $second)))
         ($_ (assertEqual $first $second))
         ($_ (assertEqual (loop-host:nonempty? $first) True))
         ($ack (tg:ack-activity-batch))
         ($_ (println! (ACTIVITY_DELIVERY_ACK $ack)))
         ($_ (assertEqual $ack 1))
         ($afterAck (eval (receive)))
         ($_ (cut))
         ($_ (println! (ACTIVITY_DELIVERY_AFTER_ACK $afterAck)))
         ($_ (assertEqual (loop-host:nonempty? $afterAck) False)))
     (println! ACTIVITY_DELIVERY_OK))
