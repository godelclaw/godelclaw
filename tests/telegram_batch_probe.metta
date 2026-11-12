; The MeTTa Telegram client's batches, with no channel service: all pending
; input arrives in one chronological batch that stays the same until it is
; acknowledged; the batch is routed and armed by its newest human input; a
; bot-only batch does not arm as human; input that arrives after a batch was
; prepared survives its acknowledgment; a runtime event keeps the reply
; target, which a message beside it decides.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (before? $text $a $b)
   (case ((str:find $text $a) (str:find $text $b))
     ((((TextNotFound $x) $y) False) (($x (TextNotFound $y)) False) (($i $j) (< $i $j)))))
(= (has? $text $part) (case (str:find $text $part) (((TextNotFound $x) False) ($i True))))
!(configure commchannel telegram)

; Chronological, stable until acknowledged, routed by the newest human.
!(tg:activity-add "" "1" "first" False "full" "1" False)
!(tg:activity-add "" "1" "second" True "full" "2" False)
!(tg:activity-add "" "1" "third" False "mid" "3" False)
!(let* (($one (tg:activity-batch)) ($two (tg:activity-batch)))
   (let* (($_ (assertEqual $one $two))
          ($_ (assertEqual (before? $one "first" "second") True))
          ($_ (assertEqual (before? $one "second" "third") True))
          ($_ (assertEqual (tg:pending-count) 3))
          ($_ (assertEqual (tg:prepared-count) 3))
          ($_ (assertEqual (tg:get reply-chat "") "1"))
          ($_ (assertEqual (tg:last-message-is-human) 1))
          ($_ (assertEqual (tg:last-message-arm-loops) (tg:tier-loops "mid")))
          ($_ (assertEqual (tg:ack-activity-batch) 1))
          ($_ (assertEqual (tg:pending-count) 0))
          ($_ (assertEqual (tg:prepared-count) 0))
          ($_ (assertEqual (tg:activity-batch) ""))
          ($_ (assertEqual (tg:last-message-is-human) 0)))
     (println! BATCH_STABLE_OK)))

; A later bot message does not take the route or the arming from a human.
!(tg:activity-add "" "7" "human-early" False "mid" "4" False)
!(tg:activity-add "" "9" "bot-later" True "full" "5" False)
!(let* (($_ (tg:activity-batch))
        ($_ (assertEqual (tg:get reply-chat "") "7"))
        ($_ (assertEqual (tg:last-message-is-human) 1))
        ($_ (assertEqual (tg:last-message-arm-loops) (tg:tier-loops "mid")))
        ($_ (assertEqual (tg:ack-activity-batch) 1)))
   (println! BATCH_MIXED_OK))

; A bot-only batch is delivered whole but does not arm as human.
!(tg:activity-add "" "5" "bot-a" True "full" "6" False)
!(tg:activity-add "" "5" "bot-b" True "full" "7" False)
!(let* (($batch (tg:activity-batch))
        ($_ (assertEqual (has? $batch "bot-a") True))
        ($_ (assertEqual (has? $batch "bot-b") True))
        ($_ (assertEqual (tg:get reply-chat "") "5"))
        ($_ (assertEqual (tg:last-message-is-human) 0))
        ($_ (assertEqual (tg:ack-activity-batch) 1)))
   (println! BATCH_BOT_ONLY_OK))

; Input after preparation belongs to the next batch.
!(tg:activity-add "" "1" "prepared" False "full" "10" False)
!(let* (($first (tg:activity-batch))
        ($_ (tg:activity-add "" "1" "later" False "full" "11" False))
        ($_ (assertEqual (has? $first "later") False))
        ($_ (assertEqual (tg:ack-activity-batch) 1))
        ($next (tg:activity-batch))
        ($_ (assertEqual (has? $next "later") True))
        ($_ (assertEqual (has? $next "prepared") False))
        ($_ (assertEqual (tg:ack-activity-batch) 1)))
   (println! BATCH_LATER_ARRIVAL_OK))

; A runtime event keeps the reply target and arms lightly; a message beside
; it decides the reply and the arming.
!(tg:activity-add "" "181" "from 181" False "full" "20" False)
!(let* (($_ (tg:activity-batch)) ($_ (assertEqual (tg:ack-activity-batch) 1))
        ($_ (py-call (telegram.enqueue_runtime_event "job 2 finished with exit 0" "job 2")))
        ($batch (tg:activity-batch))
        ($_ (assertEqual (has? $batch "[runtime] job 2 finished") True))
        ($_ (assertEqual (tg:get reply-chat "") "181"))
        ($_ (assertEqual (tg:last-message-arm-loops) (tg:tier-loops "light")))
        ($_ (assertEqual (tg:ack-activity-batch) 1))
        ($_ (py-call (telegram.enqueue_runtime_event "job 3 finished" "job 3")))
        ($_ (tg:activity-add "" "555" "hello" False "full" "21" False))
        ($_ (tg:activity-batch))
        ($_ (assertEqual (tg:get reply-chat "") "555"))
        ($_ (assertEqual (tg:last-message-arm-loops) (tg:tier-loops "full")))
        ($_ (assertEqual (tg:ack-activity-batch) 1)))
   (println! BATCH_RUNTIME_EVENT_OK))
!(println! TELEGRAM_BATCH_OK)
