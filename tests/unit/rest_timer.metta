; A scheduled action and a timed rest, on the working state's own clock: a
; restart keeps the action and its original deadline; a heartbeat never
; fires a later action early, and a cancel leaves nothing pending; a timed
; rest outlasts the heartbeat and a restart and ends on time. (An action
; interrupted by a crash: tests/unit/working_state.metta.)
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/working_state_cetta.metta)

(= (rt:at $state $event $now) (working-state:transition $state $event $now))
(= (rt:boot $state $now)
   (case (working-state:boot-transition $state $now "fixture") (((WorkingBootV1 $persisted $cache $write) ($persisted $cache)))))

!(let* (($scheduled (rt:at (JsonObject ()) (WorkingRestScheduleV1 60 (shell "printf scheduled")) 100))
        ($saved (rt:at $scheduled (WorkingSaveV1 0 60 "earlier evidence" 90 1 17 0) 100))
        (($persisted $cache) (rt:boot $saved 120)))
   (nt:require a-restart-keeps-the-action-and-its-original-deadline
     (and (== (erv:get $cache "sleepInterval" none) 40)
     (and (== (erv:get $cache "banked_loops" none) 17)
     (and (== (repr (working-state:rest-pending-state $persisted)) "(\"continuation-value\" (\"shell\" \"printf scheduled\"))")
     (and (== (working-state:rest-due-at $persisted 1 120) 0)
          (== (working-state:rest-due-at $persisted 0 160) 1)))))))

!(let* (($scheduled (rt:at (JsonObject ()) (WorkingRestScheduleV1 5000 (help)) 100))
        ($cancelled (rt:at $scheduled WorkingRestCancelV1 100)))
   (nt:require a-heartbeat-never-fires-a-later-action-early
     (and (== (working-state:wait-at $scheduled 5000 90 30 100) 20)
     (and (== (working-state:rest-due-at $scheduled 1 100) 0)
          (== (working-state:rest-pending-state $cancelled) "no-continuation")))))

!(let* (($held (rt:at (rt:at (JsonObject ()) WorkingRestCancelV1 100) (WorkingRestHoldV1 36000) 100))
        ($_ (nt:require a-timed-rest-is-held
              (and (> (working-state:rest-until-at $held 100) 0)
                   (== (working-state:wait-at $held 36000 100 1800 100) 1800))))
        ($saved (rt:at $held (WorkingSaveV1 0 36000 "resting" 100 0 43 0) 100))
        (($persisted $cache) (rt:boot $saved 30100)))
   (nt:require a-timed-rest-outlasts-the-heartbeat-and-a-restart
     (and (== (erv:get $cache "sleepInterval" none) 6000)
     (and (> (working-state:rest-until-at $persisted 30100) 0)
     (and (== (working-state:wait-at $persisted 36000 30000 1800 30100) 1700)
     (and (== (working-state:wait-at $persisted 36000 30000 0 30100) 6000)
          (== (working-state:rest-until-at $persisted 36100) 0)))))))

!(println! UNIT_REST_TIMER_OK)
