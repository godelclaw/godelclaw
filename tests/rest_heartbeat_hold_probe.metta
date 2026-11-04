; A timed rest lasts until its deadline. The life heartbeat caps each wait and
; renews the banked budget, but its timeout does not end the rest.
!(import! &self (library lib_import))
!(import! &self ./src/utils)
!(import! &self ./src/weak_process_core)
!(import! &self ./src/open_assemblage)
!(import! &self ./src/loop_policy)
!(import! &self ./src/loop)
(= (assertEqual $a $b) (assert (== $a $b)))
!(add-atom &self (= (sleepInterval) 1))

(= (hold-probe:rest $periphery $seconds)
   (progn (py-call (helper.rest_cancel))
          (py-call (helper.rest_hold $seconds))
          (loop-directives-clear)
          (loop-directive-set rest-await-input 0)
          (loop-directive-set rest-request 1)
          (loop-directive-set sleep-interval $seconds)
          (applyRestBanking (loop-directives-apply $periphery))))

!(let* (($start (coordinate loops 43
          (coordinate sleep-interval 1
            (coordinate last-heartbeat 0
              (coordinate active-policy godelclaw
                (coordinate banked-loops 0
                  (coordinate rest-await-input 0
                    (coordinate pending-continuation no-continuation
                      coordinates-empty))))))))
         ($_ (py-call (helper.rest_cancel)))
         ($_ (assertEqual (loop-parked $start) False))
         ($resting (hold-probe:rest $start 36000))
         ($_ (assertEqual (loop-budget $resting) 0))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $resting) 43))
         ($_ (assertEqual (loop-sleep $resting) 36000))
         ($_ (assertEqual (loop-parked $resting) True))
         ($_ (assertEqual (loop-resting $resting) 1))
         ;; The heartbeat banks its renewal and leaves the rest's pacing alone.
         ($beat (heartbeatPeriphery $resting 1800 44))
         ($_ (assertEqual (loop-budget $beat) 0))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $beat) 44))
         ($_ (assertEqual (loop-sleep $beat) 36000))
         ;; The capped wait's timeout keeps resting; renewal stays suspended.
         ($_ (assertEqual (restoreAfterWait $beat 1) $beat))
         ($_ (assertEqual (policy-next-autonomous-ready godelclaw 0 1 (loop-resting $beat)) 0))
         ;; A held rest with nothing banked is still a rest.
         ($empty (coordinate-set banked-loops 0 $beat))
         ($_ (assertEqual (loop-resting $empty) 1))
         ($_ (assertEqual (restoreAfterWait $empty 1) $empty))
         ;; The wait is capped by the heartbeat, never past the rest's end.
         ($_ (assertEqual (<= (py-call (helper.wait_seconds 36000 (get_time) 1800)) 1800) True))
         ($_ (assertEqual (> (py-call (helper.rest_until)) (+ (get_time) 35000)) True))
         ;; At the deadline the rest ends and hands the bank back.
         ($_ (py-call (helper.rest_hold 0)))
         ($_ (assertEqual (loop-parked $beat) False))
         ($due (restoreAfterWait $beat 1))
         ($_ (assertEqual (loop-budget $due) 44))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $due) 0))
         ($_ (assertEqual (loop-sleep $due) 1))
         ;; A real wake ends it early and releases the deadline.
         ($again (hold-probe:rest $start 36000))
         ($woken (restoreAfterWait $again 0))
         ($_ (assertEqual (loop-budget $woken) 43))
         ($_ (assertEqual (py-call (helper.rest_holding)) 0))
         ;; An activity wait is unchanged: no deadline, parked until input.
         ($_ (py-call (helper.rest_cancel)))
         ($await (coordinate-set rest-await-input 1 $resting))
         ($_ (assertEqual (loop-parked $await) True))
         ($_ (assertEqual (restoreAfterWait $await 1) $await))
         ($_ (loop-directives-clear)))
   (println! REST_HEARTBEAT_HOLD_OK))
