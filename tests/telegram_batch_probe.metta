; The MeTTa Telegram client's batches, with no channel service: all pending
; input arrives in one chronological batch that stays the same until it is
; acknowledged; the batch is routed and armed by its newest human input; a
; bot-only batch does not arm as human; input that arrives after a batch was
; prepared survives its acknowledgment; a runtime event keeps the reply
; target, which a message beside it decides. The file beside the health file
; holds what the client publishes for other processes (the command responder
; reads its rest and counts), and the health file shows its poll and rest.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (before? $text $a $b)
   (case ((str:find $text $a) (str:find $text $b))
     ((((TextNotFound $x) $y) False) (($x (TextNotFound $y)) False) (($i $j) (< $i $j)))))
(= (has? $text $part) (case (str:find $text $part) (((TextNotFound $x) False) ($i True))))
(= (health-field $name) (json-plain (json:get (sf:object (tg:health-path)) $name)))
(= (client-published $name $default) (sf:get (sf:native-object (tg:client-state-path)) $name $default))
(= (json-plain $value)
   (case $value (((JsonString $s) $s) ((JsonNumber $n) (str:string->number $n)) ($other $other))))
!(configure commchannel telegram)

; The identity of presented input: SHA-256 of the context epoch and the sorted
; update ids, NUL-separated (the pinned answer is for epoch 7 and ids 101,
; 102). Order does not change it; another occurrence does.
(= (batch:identity)
   (let* (($epoch (tg:get context-epoch 0)) ($ids (tg:get batch-ids ""))
          ($_ (tg:set context-epoch 7))
          ($_ (tg:set batch-ids "102,101"))
          ($first (tg:current-batch-identity))
          ($_ (assertEqual $first "20f109c03f10dbc3d9de08c63e9ffd66b9d0f7670ff3414a55c2622d9a5f187e"))
          ($_ (tg:set batch-ids "101,102"))
          ($_ (assertEqual (tg:current-batch-identity) $first))
          ($_ (tg:set batch-ids "101,102,103"))
          ($_ (assertEqual (== (tg:current-batch-identity) $first) False))
          ($_ (tg:set context-epoch $epoch)))
     (tg:set batch-ids $ids)))

; Chronological, stable until acknowledged, routed by the newest human; the
; counts are published and the Python code sees the reply chat.
(= (batch:stable)
   (let* (($_ (tg:activity-add "" "1" "first" False "full" "1" False))
          ($_ (tg:activity-add "" "1" "second" True "full" "2" False))
          ($_ (tg:activity-add "" "1" "third" False "mid" "3" False))
          ($one (tg:activity-batch))
          ($two (tg:activity-batch))
          ($_ (assertEqual $one $two))
          ($_ (batch:identity))
          ($_ (assertEqual (client-published "pending_count" -1) 3))
          ($_ (assertEqual (client-published "prepared_count" -1) 3))
          ($_ (assertEqual (py-call (telegram._reply_target "")) "1"))
          ($_ (assertEqual (health-field "poll_status") "ok"))
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
          ($_ (assertEqual (client-published "pending_count" -1) 0))
          ($_ (assertEqual (tg:activity-batch) ""))
          ($_ (assertEqual (tg:last-message-is-human) 0)))
     (println! BATCH_STABLE_OK)))

; A later bot message does not take the route or the arming from a human.
(= (batch:mixed)
   (let* (($_ (tg:activity-add "" "7" "human-early" False "mid" "4" False))
          ($_ (tg:activity-add "" "9" "bot-later" True "full" "5" False))
          ($_ (tg:activity-batch))
          ($_ (assertEqual (tg:get reply-chat "") "7"))
          ($_ (assertEqual (tg:last-message-is-human) 1))
          ($_ (assertEqual (tg:last-message-arm-loops) (tg:tier-loops "mid")))
          ($_ (assertEqual (tg:ack-activity-batch) 1)))
     (println! BATCH_MIXED_OK)))

; A bot-only batch is delivered whole but does not arm as human.
(= (batch:bot-only)
   (let* (($_ (tg:activity-add "" "5" "bot-a" True "full" "6" False))
          ($_ (tg:activity-add "" "5" "bot-b" True "full" "7" False))
          ($batch (tg:activity-batch))
          ($_ (assertEqual (has? $batch "bot-a") True))
          ($_ (assertEqual (has? $batch "bot-b") True))
          ($_ (assertEqual (tg:get reply-chat "") "5"))
          ($_ (assertEqual (tg:last-message-is-human) 0))
          ($_ (assertEqual (tg:ack-activity-batch) 1)))
     (println! BATCH_BOT_ONLY_OK)))

; Input after preparation belongs to the next batch.
(= (batch:later-arrival)
   (let* (($_ (tg:activity-add "" "1" "prepared" False "full" "10" False))
          ($first (tg:activity-batch))
          ($_ (tg:activity-add "" "1" "later" False "full" "11" False))
          ($_ (assertEqual (has? $first "later") False))
          ($_ (assertEqual (tg:ack-activity-batch) 1))
          ($next (tg:activity-batch))
          ($_ (assertEqual (has? $next "later") True))
          ($_ (assertEqual (has? $next "prepared") False))
          ($_ (assertEqual (tg:ack-activity-batch) 1)))
     (println! BATCH_LATER_ARRIVAL_OK)))

; A runtime event keeps the reply target and arms lightly; a message beside
; it decides the reply and the arming.
(= (batch:runtime-event)
   (let* (($_ (tg:activity-add "" "181" "from 181" False "full" "20" False))
          ($_ (tg:activity-batch))
          ($_ (assertEqual (tg:ack-activity-batch) 1))
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
     (println! BATCH_RUNTIME_EVENT_OK)))

; A rest, as other processes and a monitor see it.
(= (batch:published-rest)
   (let* (($until (+ (tg:now) 300))
          ($_ (tg:publish-rest $until))
          ($_ (assertEqual (client-published "sleep_until" 0) $until))
          ($_ (assertEqual (health-field "loop_status") "waiting"))
          ($_ (assertEqual (health-field "waiting_until") $until))
          ($_ (tg:publish-rest 0))
          ($_ (assertEqual (health-field "loop_status") "awake"))
          ($_ (assertEqual (client-published "sleep_until" 1) 0)))
     (println! BATCH_PUBLISHED_REST_OK)))

; Every section must answer before the witness.
!(let* (($a (batch:stable)) ($b (batch:mixed)) ($c (batch:bot-only)) ($d (batch:later-arrival))
        ($e (batch:runtime-event)) ($f (batch:published-rest)))
   (println! TELEGRAM_BATCH_OK))
