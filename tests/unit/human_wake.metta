; A human wake applies the goal kernel once per message batch: the same batch
; again is not applied, a new batch is, and after a restart the batch already
; applied is still not applied again (its digest is kept in the kernel state).
!(import! &self ../native_test_support.metta)

; Run wakes for $tokens in one fresh process; the printed (WAKE fresh applied) lines.
(= (hw:run $repo $home $goals $tokens)
   (let* (($program (path:join $home "wake.metta"))
          ($_ (fs:write $program (str:join "" (cons-atom
                (str:join "" ("!(import! &self " (path:join $repo "src/goal_bookkeeping_cetta.metta") ")\n"))
                (map-atom $tokens $t
                  (str:join "" ("!(let (GoalWake $fresh $applied $message) (gk:human-wake " (json-stringify (JsonString $t))
                                " " (json-stringify (JsonString $goals)) " \"\") (println! (WAKE $fresh $applied)))\n"))))))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo
                     (nt:environment $home) 30000 1048576)
       (((proc:exited 0 $out $err) (filter-atom (str:split "\n" $out) $l (str:starts-with $l "(WAKE ")))
        ($bad (Failed $bad))))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($goals (path:join $home "goals.metta"))
         ($_ (fs:write $goals "(goal-budget 3)\n(goal task Work sti:0.8 lti:0.3 vibes:(care) blocked-by:none last-verified:never note:\"fixture\")\n"))
         ($first (hw:run $repo $home $goals ("batch-17" "batch-17" "batch-18")))
         ($_ (nt:require a-batch-is-applied-once-and-a-new-batch-again
               (== $first ("(WAKE 1 1)" "(WAKE 0 0)" "(WAKE 1 1)"))))
         ($_ (nt:require the-applied-batch-is-recorded (fs:exists (path:join $home "goals_kernel.json"))))
         ($restart (hw:run $repo $home $goals ("batch-18" "batch-19")))
         ($_ (nt:require after-a-restart-the-same-batch-is-not-applied-again
               (== $restart ("(WAKE 0 0)" "(WAKE 1 1)")))))
   (println! UNIT_HUMAN_WAKE_OK))
