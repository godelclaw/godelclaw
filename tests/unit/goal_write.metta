; goal-write as the agent calls it: fields written with a space after their
; colon are read as written, and the goals view shows them in their compact
; form.
!(import! &self ../native_test_support.metta)

!(let* (($root (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($goals (path:join $home "goals.metta"))
        ($source (path:join $home "goal-write.metta"))
        ($_ (fs:write $source (str:join "\n" (
              (str:join "" ("!(import! &self " (repr (path:join $root "src/skills.metta")) ")"))
              "!(println! (WRITE (goal-write (goal wiki Work sti: 0.8 lti: 0.3 vibes: (SenseMaking) blocked-by: none last-verified: never note: distill reports))))"
              "!(println! (VIEW (goals)))"
              ""))))
        ($run (proc:run ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" $source)
                        $root (cons-atom ("METTACLAW_GOALS_PATH" $goals) (nt:environment $home)) 120000 1048576))
        ($out (case $run (((proc:exited 0 $o $e) $o) ($other (repr $other))))))
   (nt:require goal-write-tolerates-spaces-after-colons
     (and (nt:contains $out "(WRITE \"goal wiki added\")")
          (nt:contains $out "sti:0.8"))))

!(println! UNIT_GOAL_WRITE_OK)
