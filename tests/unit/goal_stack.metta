; The goal stack (goal-stack spec v0.2) as the agent's native writer
; keeps it: kernel passes flag stuck and weak goals but never delete them, a
; never-verified goal cannot be claimed solved, a drop needs a reason and
; composts the goal, a compost failure keeps it, STI is normalized to the
; budget and decays while LTI consolidates, the mood is a gamma-weighted
; average of the affect traces sent, a human wake applies once per batch,
; and malformed records or values are refused without rewriting anything.
; Each case writes its own stack and runs the writer in a fresh process.
!(import! &self ../native_test_support.metta)

(= (gs:goal) "(goal probmetta Work sti:0.8 lti:0.3 vibes:(SenseMaking) blocked-by:\"add-atom space error\" last-verified:never note:\"3-channel STI groundwork\")")
(= (gs:free) "(goal attune Relationship sti:0.9 lti:0.5 vibes:(CuddleBubbling) blocked-by:none last-verified:2026-07-18T15:00 note:\"track Zar\")")
(= (gs:weak) "(goal probmetta Work sti:0.01 lti:0.01 vibes:(SenseMaking) blocked-by:\"add-atom space error\" last-verified:never note:\"3-channel STI groundwork\")")
(= (gs:and3 $a $b $c) (and $a (and $b $c)))
(= (gs:and4 $a $b $c $d) (and $a (gs:and3 $b $c $d)))
(= (gs:and5 $a $b $c $d $e) (and $a (gs:and4 $b $c $d $e)))
(= (gs:lit $text) (json-stringify (JsonString $text)))
(= (gs:replace $text $old $new) (str:join $new (str:split $old $text)))
(= (gs:count $text $needle) (- (size-atom (str:split $needle $text)) 1))

; A case directory holding the stack: a comment line, then the lines.
(= (gs:stack $home $name $lines)
   (let* (($dir (path:join $home $name)) ($_ (fs:make-directories $dir 448))
          ($path (path:join $dir "goals.metta"))
          ($_ (fs:write $path (str:join "" ((str:join "\n" (cons-atom ";; test stack" $lines)) "\n")))))
     $path))
(= (gs:file $path $name) (path:join (path:dirname $path) $name))
(= (gs:view $path) (fs:read-text $path))

; The calls, as guest expressions.
(= (gs:pass $path) (str:join "" ("(gk:kernel-status " (gs:lit $path) " \"\" GkNoWake)")))
(= (gs:pass-log $path) (str:join "" ("(gk:kernel-status " (gs:lit $path) " " (gs:lit (gs:file $path "updates.jsonl")) " GkNoWake)")))
(= (gs:write $text $path) (str:join "" ("(gk:goal-write " (gs:lit $text) " " (gs:lit $path) ")")))
(= (gs:drop $name $reason $path) (str:join "" ("(gk:goal-drop " (gs:lit $name) " " (gs:lit $reason) " " (gs:lit $path) ")")))
(= (gs:wake $token $path) (str:join "" ("(gk:human-wake " (gs:lit $token) " " (gs:lit $path) " \"\")")))
(= (gs:affect $path) (str:join "" ("(gv:affect " (gs:lit $path) ")")))
; The stack's digest: one line, where the text would span several.
(= (gs:digest $path) (str:join "" ("(str:sha256 (fs:read-text " (gs:lit $path) "))")))

; Run the calls in one fresh writer process; the printed results, in order.
(= (gs:run $repo $path $calls)
   (let* (($program (gs:file $path "case.metta"))
          ($_ (fs:write $program (str:join "" (cons-atom
                (str:join "" ("!(import! &self " (path:join $repo "src/goal_bookkeeping_cetta.metta") ")\n"))
                (map-atom $calls $call (str:join "" ("!(println! (OUT " $call "))\n"))))))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo
                     (nt:environment (path:dirname $path)) 60000 1048576)
       (((proc:exited 0 $out $err) (filter-atom (str:split "\n" $out) $line (str:starts-with $line "(OUT ")))
        ($bad (($bad)))))))

; As the loop and skills call it: through src/goal_bookkeeping.metta, with the
; goals and channel log named by the environment, and goals given as atoms.
(= (gs:run-wired $repo $path $calls)
   (let* (($program (gs:file $path "case.metta"))
          ($_ (fs:write $program (str:join "" (cons-atom
                (str:join "" ("!(import! &self " (path:join $repo "src/goal_bookkeeping.metta") ")\n"))
                (map-atom $calls $call (str:join "" ("!(println! (OUT " $call "))\n"))))))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo
                     (cons-atom ("METTACLAW_GOALS_PATH" $path) (cons-atom ("METTACLAW_TELEGRAM_LOG_PATH" (gs:file $path "updates.jsonl"))
                       (nt:environment (path:dirname $path)))) 60000 1048576)
       (((proc:exited 0 $out $err) (filter-atom (str:split "\n" $out) $line (str:starts-with $line "(OUT ")))
        ($bad (($bad)))))))

; An outbound message carrying an affect trace, as the channel log has it.
(= (gs:outbound $text)
   (str:concat (json-stringify (JsonObject ((JsonPair "kind" (JsonString "outbound")) (JsonPair "text" (JsonString $text))))) "\n"))
(= (gs:goal-line $text $name)
   (car-atom (filter-atom (str:split "\n" $text) $line (str:starts-with $line (str:concat "(goal " (str:concat $name " "))))))
(= (gs:sti $line) (str:string->number (car-atom (str:split " " (car-atom (cdr-atom (str:split "sti:" $line)))))))
(= (gs:sti-sum $text)
   (foldl-atom (filter-atom (str:split "\n" $text) $line (str:starts-with $line "(goal ")) 0 $sum $line (+ $sum (gs:sti $line))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($stuck (gs:stack $home "stuck" ((gs:goal))))
        (($s1 $s2 $s3) (gs:run $repo $stuck ((gs:pass $stuck) (gs:pass $stuck) (gs:pass $stuck))))
        ($flagged (gs:view $stuck))
        ($unblocked (gs:replace (gs:goal-line $flagged "probmetta") "\"add-atom space error\"" "none"))
        (($_ $s4) (gs:run $repo $stuck ((gs:write $unblocked $stuck) (gs:pass $stuck)))))
   (nt:require a-blocked-goal-is-flagged-stuck-on-the-third-wake-and-cleared-once-unblocked
     (gs:and5 (not (nt:contains $s1 "stuck")) (not (nt:contains $s2 "stuck"))
          (nt:contains $s3 "probmetta:stuck") (nt:contains $flagged "(goal-flag probmetta stuck)")
          (not (nt:contains $s4 "stuck")))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "solved" ((gs:goal) (gs:free))))
        (($never $verified) (gs:run $repo $path ((gs:drop "probmetta" "solved" $path) (gs:drop "attune" "solved" $path)))))
   (nt:require a-never-verified-goal-cannot-be-claimed-solved
     (gs:and3 (nt:contains $never "(OUT \"rejected: last-verified is 'never'")
          (nt:contains (gs:view $path) "(goal probmetta ")
          (nt:contains $verified "goal attune composted (solved)"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "budget" ("(goal-budget 1.0)" (gs:goal) (gs:free))))
        (($pass) (gs:run $repo $path ((gs:pass $path)))))
   (nt:require sti-is-normalized-to-the-goal-budget
     (and (nt:contains $pass "goal-kernel: 2 goals") (<= (gs:sti-sum (gs:view $path)) 1.000000001))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "cull" ((gs:weak))))
        (($pass) (gs:run $repo $path ((gs:pass $path))))
        ($kept (gs:view $path))
        (($drop) (gs:run $repo $path ((gs:drop "probmetta" "death-review" $path)))))
   (nt:require a-weak-goal-is-flagged-for-review-never-deleted-and-composts-on-review
     (gs:and4 (nt:contains $pass "cull-review") (nt:contains $kept "(goal probmetta ")
          (nt:contains $drop "goal probmetta composted (death-review)")
          (nt:contains (fs:read-text (gs:file $path "goals_compost.log")) "probmetta"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "retire" ((gs:free))))
        (($empty $reason) (gs:run $repo $path ((gs:drop "attune" "" $path)
                                               (gs:drop "attune" "superseded by native mailboxes" $path)))))
   (nt:require a-goal-retires-with-any-reason-but-not-without-one
     (gs:and4 (nt:contains $empty "(OUT \"rejected: give a reason")
          (nt:contains $reason "composted (superseded by native mailboxes)")
          (not (nt:contains (gs:view $path) "(goal attune "))
          (nt:contains (fs:read-text (gs:file $path "goals_compost.log")) " superseded by native mailboxes "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "recover" ((gs:weak))))
        ($_ (gs:run $repo $path ((gs:pass $path))))
        ($flagged (gs:view $path))
        (($write $_) (gs:run $repo $path ((gs:write (gs:goal) $path) (gs:pass $path)))))
   (nt:require the-cull-flag-clears-once-the-goal-recovers
     (gs:and3 (nt:contains $flagged "(goal-flag probmetta cull-review)") (nt:contains $write "goal probmetta updated")
          (not (nt:contains (gs:view $path) "cull-review")))))

; The compost log cannot be appended to: a directory stands in its place.
!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "compost-failure" ((gs:free))))
        ($_ (fs:make-directories (gs:file $path "goals_compost.log") 448))
        (($drop) (gs:run $repo $path ((gs:drop "attune" "solved" $path)))))
   (nt:require a-compost-failure-keeps-the-goal
     (and (nt:contains $drop "goal-drop failed: compost log was not written")
          (nt:contains (gs:view $path) "(goal attune "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "mood" ((gs:free))))
        ($first (gs:outbound "hi\n⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5⟩"))
        ($_ (fs:write (gs:file $path "updates.jsonl") $first))
        ($_ (gs:run $repo $path ((gs:pass-log $path))))
        ($_ (fs:write (gs:file $path "updates.jsonl")
                      (str:concat $first (gs:outbound "yo\n⋄⟨Cn:.8 C:.8 Ct:.8 I:.8 J:.8 A:.8 S:.8 Co:.8⟩"))))
        (($_ $affect) (gs:run $repo $path ((gs:pass-log $path) (gs:affect $path)))))
   (nt:require the-mood-is-a-gamma-average-of-the-traces-sent
     (and (nt:contains (gs:view $path) "(mood Cn:0.593 ")
          (nt:contains $affect "gamma:0.69 Cn:0.593 "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "decay" ((gs:free))))
        (($pass) (gs:run $repo $path ((gs:pass $path)))))
   (nt:require sti-decays-and-lti-consolidates-toward-it
     (and (nt:contains $pass "goal-kernel: 1 goals, sti 0.855/3")
          (nt:contains (gs:view $path) "(goal attune Relationship sti:0.855 lti:0.5178 "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "wake" ((gs:free))))
        (($first $after $repeat $unchanged $different $changed)
          (gs:run $repo $path ((gs:wake "batch-17" $path) (gs:digest $path) (gs:wake "batch-17" $path) (gs:digest $path)
                               (gs:wake "batch-18" $path) (gs:digest $path)))))
   (nt:require a-human-wake-decays-and-consolidates-once-per-batch
     (gs:and5 (nt:contains $first "(GoalWake 1 1 ") (nt:contains $repeat "(GoalWake 0 0 ")
          (== $after $unchanged) (nt:contains $different "(GoalWake 1 1 ") (not (== $changed $after)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "wake-malformed" ((gs:free) "(goal broken)")))
        (($first $repeat) (gs:run $repo $path ((gs:wake "malformed-batch" $path) (gs:wake "malformed-batch" $path)))))
   (nt:require a-goal-kernel-failure-cannot-block-fresh-input
     (and (nt:contains $first "(GoalWake 1 0 \"goal-kernel error") (nt:contains $repeat "(GoalWake 0 "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "unknown-lines" (";; precious comment" (gs:free))))
        (($_ $broken $added $updated)
          (gs:run $repo $path ((gs:pass $path) (gs:write "(goal broken)" $path) (gs:write (gs:goal) $path) (gs:write (gs:goal) $path)))))
   (nt:require unknown-lines-survive-and-writes-are-validated
     (gs:and4 (nt:contains (gs:view $path) ";; precious comment") (nt:contains $broken "(OUT \"rejected: expected (goal ")
          (nt:contains $added "goal probmetta added") (nt:contains $updated "goal probmetta updated"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "legacy" ("(goal legacy Work sti:0.2 lti:0.4 vibes:(care) blocked-by:none)"
                                         "(goal attune Old sti:0.8 lti:0.1 vibes:(old) blocked-by:none)" (gs:free))))
        (($pass) (gs:run $repo $path ((gs:pass $path))))
        ($view (gs:view $path)))
   (nt:require legacy-records-migrate-and-the-canonical-duplicate-wins
     (gs:and5 (nt:contains $pass "goal-kernel: 2 goals") (== (gs:count $view "(goal attune ") 1)
          (nt:contains $view "(goal attune Relationship ") (nt:contains $view "(goal legacy Work ")
          (nt:contains $view "last-verified:never note:\"\""))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "malformed" ((gs:free) "(goal broken)")))
        ($before (gs:view $path))
        (($pass $write) (gs:run $repo $path ((gs:pass $path) (gs:write (gs:goal) $path)))))
   (nt:require a-malformed-record-fails-without-rewriting-the-stack
     (gs:and3 (nt:contains $pass "malformed goal record") (nt:contains $write "malformed goal record")
          (== (gs:view $path) $before))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "invalid-number" ()))
        (($write) (gs:run $repo $path ((gs:write (gs:replace (gs:free) "sti:0.9" "sti:1.2.3") $path)))))
   (nt:require an-invalid-number-is-rejected
     (and (nt:contains $write "(OUT \"rejected: expected (goal ") (not (fs:exists (gs:file $path "goals_kernel.json"))))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "unquoted" ()))
        (($write) (gs:run $repo $path ((gs:write "(goal sti3 Work sti:0.7 lti:0.2 vibes:(SenseMaking) blocked-by:add-atom-space-error last-verified:never note:3-channel STI for Godel)" $path))))
        ($view (gs:view $path)))
   (nt:require a-command-channel-goal-needs-no-quotes
     (gs:and3 (nt:contains $write "goal sti3 added") (nt:contains $view "note:\"3-channel STI for Godel\"")
          (nt:contains $view "blocked-by:add-atom-space-error"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "nine-dimensions" ((gs:free))))
        ($old (gs:outbound "a\n⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5⟩"))
        ($_ (fs:write (gs:file $path "updates.jsonl") $old))
        ($_ (gs:run $repo $path ((gs:pass-log $path))))
        ($eight (gs:view $path))
        ($_ (fs:write (gs:file $path "updates.jsonl")
                      (str:concat $old (gs:outbound "b\n⋄⟨Cn:.8 C:.8 Ct:.8 I:.8 J:.8 A:.8 S:.8 Co:.8 Sp:.8⟩"))))
        ($_ (gs:run $repo $path ((gs:pass-log $path))))
        ($nine (gs:view $path)))
   (nt:require nine-dimension-traces-and-eight-dimension-history-coexist
     (gs:and3 (nt:contains $eight " Sp:0") (nt:contains $nine "(mood Cn:0.593 ") (nt:contains $nine " Sp:0.248"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "wired" ((gs:free))))
        ($_ (fs:write (gs:file $path "updates.jsonl") (gs:outbound "hi\n⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5 Sp:.5⟩")))
        (($boot $fresh $message $write $drop)
          (gs:run-wired $repo $path ("(gk:kernel-pass)" "(goal-bookkeeping:fresh (gk:human-wake \"batch-1\"))"
                                     "(goal-bookkeeping:message (gk:human-wake \"batch-2\"))"
                                     "(gk:goal-write (goal novel Work sti:0.5 lti:0.5 vibes:(日本 care) blocked-by:none last-verified:never note:généré 🙂))"
                                     "(gk:goal-drop attune superseded)")))
        ($view (gs:view $path)))
   (nt:require the-loop-and-skill-entry-points-reach-the-writer
     (gs:and5 (nt:contains $boot "goal-kernel: 1 goals, sti 0.855/3") (== $fresh "(OUT 1)")
              (gs:and3 (nt:contains $message "goal-kernel: 1 goals") (nt:contains $write "goal novel added")
                       (nt:contains $drop "goal attune composted (superseded)"))
              (nt:contains $view "(goal novel Work sti:") (gs:and3 (nt:contains $view "vibes:(日本 care)")
                (nt:contains $view "note:\"généré 🙂\"") (nt:contains $view "(mood Cn:0.5 ")))))

; Faults, injected as a directory where the writer needs a file.
!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "first-write" ((gs:free))))
        ($before (gs:view $path))
        ($_ (fs:make-directories (gs:file $path "goals.metta.tmp") 448))
        (($pass $write) (gs:run $repo $path ((gs:pass $path) (gs:write (gs:goal) $path)))))
   (nt:require a-failed-first-write-changes-nothing
     (gs:and4 (nt:contains $pass "goal-kernel error: ") (nt:contains $write "(Error ")
              (== (gs:view $path) $before) (not (fs:exists (gs:file $path "goals_kernel.json"))))))

; The goals are committed, the kernel state is not: the same batch again in
; the same process neither decays the goals again nor writes anything.
!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "partial-write" ((gs:free))))
        ($before (gs:view $path))
        ($_ (fs:make-directories (gs:file $path "goals_kernel.json.tmp") 448))
        (($first $after $repeat $unchanged)
          (gs:run $repo $path ((gs:wake "durable-failure" $path) (gs:digest $path) (gs:wake "durable-failure" $path) (gs:digest $path)))))
   (nt:require a-partly-committed-wake-is-not-retried
     (gs:and5 (nt:contains $first "(GoalWake 1 0 \"goal-kernel error: ") (nt:contains $repeat "(GoalWake 0 0 ")
              (== $after $unchanged) (not (== (gs:view $path) $before))
              (not (fs:exists (gs:file $path "goals_kernel.json"))))))

; The compost record is written, then the stack cannot be: the goal stays,
; with its record composted, and nothing retries.
!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "uncertain-drop" ((gs:free))))
        ($before (gs:view $path))
        ($_ (fs:make-directories (gs:file $path "goals.metta.tmp") 448))
        (($drop) (gs:run $repo $path ((gs:drop "attune" "retire" $path)))))
   (nt:require an-uncertain-drop-keeps-the-goal-and-its-compost-record
     (gs:and3 (nt:contains $drop "(Error ") (== (gs:view $path) $before)
              (nt:contains (fs:read-text (gs:file $path "goals_compost.log")) " retire (goal attune Relationship "))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($path (gs:stack $home "wrong-shape" ((gs:free))))
        ($before (gs:view $path))
        ($_ (fs:write (gs:file $path "updates.jsonl") "[]\n"))
        (($pass) (gs:run $repo $path ((gs:pass-log $path)))))
   (nt:require a-log-line-of-the-wrong-shape-is-an-error-that-changes-nothing
     (gs:and3 (nt:contains $pass "goal-kernel error: 'list' object has no attribute 'get'")
              (== (gs:view $path) $before) (not (fs:exists (gs:file $path "goals_kernel.json"))))))

!(println! UNIT_GOAL_STACK_OK)
