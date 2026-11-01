; Activity waits keep inference dormant across idle polls and heartbeat grants.
!(import! &self (library lib_import))
!(import! &self ./src/utils)
!(import! &self ./src/weak_process_core)
!(import! &self ./src/open_assemblage)
!(import! &self ./src/loop_policy)
!(import! &self ./src/loop)
(= (assertEqual $a $b) (assert (== $a $b)))
!(add-atom &self (= (sleepInterval) 1))

(= (wait-probe:ticks $count $state)
   (if (== $count 0) $state
     (wait-probe:ticks (- $count 1)
       (restoreAfterWait
         (heartbeatPeriphery $state $count
           (+ 1 (coordinate-get-or banked-loops 0 $state))) 1))))

!(let* (($start (coordinate loops 43
          (coordinate sleep-interval 1
            (coordinate active-policy godelclaw
              (coordinate banked-loops 0
                (coordinate rest-await-input 1
                  (coordinate rest-request 1 coordinates-empty)))))))
         ($parked (applyRestBanking $start))
         ($_ (assertEqual (loop-budget $parked) 0))
         ($_ (assertEqual (loop-sleep $parked) (policy-idle-wait godelclaw)))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $parked) 43))
         ($_ (assertEqual (loop-resting $parked) 1))
         ($_ (assertEqual (restoreAfterWait $parked 1) $parked))
         ($later (wait-probe:ticks 128 $parked))
         ($_ (assertEqual (loop-budget $later) 0))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $later) 171))
         ($_ (assertEqual (loop-resting $later) 1))
         ($_ (assertEqual (policy-next-autonomous-ready godelclaw 0 1 (loop-resting $later)) 0))
         ($woken (restoreAfterWait $later 0))
         ($_ (assertEqual (loop-budget $woken) 171))
         ($_ (assertEqual (coordinate-get-or banked-loops 0 $woken) 0))
         ($_ (assertEqual (coordinate-get-or rest-await-input 0 $woken) 0))
         ($_ (assertEqual (loop-resting $woken) 0))
         ($_ (assertEqual (loop-sleep $woken) 1))
         ($raced (applyObservedWake $parked True))
         ($_ (assertEqual (loop-budget $raced) 43))
         ($_ (assertEqual (coordinate-get-or rest-await-input 0 $raced) 0))
         ($empty (coordinate-set banked-loops 0 $parked))
         ($_ (assertEqual (loop-resting $empty) 1))
         ($_ (assertEqual (loop-budget (restoreAfterWait $empty 1)) 0))
         ($timed (coordinate-set rest-await-input 0 $parked))
         ($_ (assertEqual (loop-budget (restoreAfterWait $timed 1)) 43)))
   (println! REST_ACTIVITY_WAIT_OK))
