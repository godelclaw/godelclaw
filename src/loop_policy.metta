;; Replaceable life-policy data and per-turn directives.  This file is not
;; part of the two-equation process kernel.

!(bind! &loop-directives (new-state coordinates-empty))

(= (loop-directives-clear)
   (change-state! &loop-directives coordinates-empty))
(= (loop-directive-set $name $value)
   (change-state! &loop-directives
     (coordinate-set $name $value (get-state &loop-directives))))
(= (loop-directives-apply $periphery)
   (coordinate-overlay (get-state &loop-directives) $periphery))

(= (loop-iteration $periphery)
   (coordinate-get-or iteration 1 $periphery))
(= (loop-budget $periphery)
   (coordinate-get-or loops 0 $periphery))
(= (loop-sleep $periphery)
   (coordinate-get-or sleep-interval 1 $periphery))
(= (loop-last-results $periphery)
   (coordinate-get-or last-results "" $periphery))
(= (loop-last-heartbeat $periphery)
   (coordinate-get-or last-heartbeat 0 $periphery))
(= (loop-active-policy $periphery)
   (coordinate-get-or active-policy godelclaw $periphery))
(= (loop-autonomous-ready $periphery)
   (coordinate-get-or autonomous-ready 0 $periphery))
(= (loop-pending-continuation $periphery)
   (coordinate-get-or pending-continuation no-continuation $periphery))

;; Named policy fragments compose into presets.  The list order is semantic:
;; later fragments override earlier coordinates.  Adding a fragment or preset
;; does not add a case to the process core.  reasoning=inherit means use the
;; configured model default.
(= (policy-registry)
   (coordinate base
     (coordinate burst-budget 50
       (coordinate idle-wait 1
         (coordinate autonomous-renewal False
           (coordinate reasoning inherit
             (coordinate structured-request 0
               (coordinate iter-transformations 0
                 (coordinate command-batch-limit 5 coordinates-empty)))))))
     (coordinate iter-renewal
       (coordinate idle-wait 60
         (coordinate autonomous-renewal True
           (coordinate structured-request 1
             (coordinate iter-transformations 1 coordinates-empty))))
       (coordinate coding-reasoning
         (coordinate reasoning high
           (coordinate structured-request 1 coordinates-empty))
         coordinates-empty))))
(= (policy-stage $name)
   (coordinate-get $name (policy-registry)))

(= (policy-preset godelclaw)
   (stage-list base (stage-list iter-renewal stages-empty)))
(= (policy-preset coding)
   (stage-list base
     (stage-list iter-renewal
       (stage-list coding-reasoning stages-empty))))

(= (policy-compile stages-empty) (policy-ok coordinates-empty))
(= (policy-compile (stage-list $name $rest))
   (let $stage (policy-stage $name)
     (case $stage
       (((missing-coordinate $missing)
         (policy-error unknown-stage $missing))
        ($known
         (let $tail (policy-compile $rest)
           (case $tail
             (((policy-error $kind $detail) $tail)
              ((policy-ok $coordinates)
               (policy-ok (coordinate-overlay $coordinates $known)))))))))))
(= (policy-record $selected)
   (let $compiled (policy-compile (policy-preset $selected))
     (case $compiled
       (((policy-error $kind $detail) $compiled)
        ((policy-ok $coordinates) $coordinates)))))

(= (policy-burst-budget $selected)
   (coordinate-get-or burst-budget 50 (policy-record $selected)))
(= (policy-idle-wait $selected)
   (coordinate-get-or idle-wait 1 (policy-record $selected)))
(= (policy-autonomous-renewal $selected)
   (coordinate-get-or autonomous-renewal False (policy-record $selected)))
(= (policy-reasoning $selected $default)
   (let $reasoning
        (coordinate-get-or reasoning inherit (policy-record $selected))
        (if (== $reasoning inherit) $default $reasoning)))
(= (policy-structured-request $selected)
   (coordinate-get-or structured-request 0 (policy-record $selected)))
(= (policy-iter-transformations $selected)
   (coordinate-get-or iter-transformations 0 (policy-record $selected)))
(= (policy-command-batch-limit $selected)
   (py-call (helper.command_batch_limit
     (coordinate-get-or command-batch-limit 5 (policy-record $selected)))))

;; A rest in flight is exactly a banked budget waiting to be handed back,
;; so the periphery already answers "is renewal suspended?" and no separate
;; persisted flag is needed. The rest ends itself; nothing has to clear it.
(= (loop-resting $periphery)
   (if (> (coordinate-get-or banked-loops 0 $periphery) 0) 1 0))

(= (policy-wait-seconds $selected $loops $requested $paused)
   (if (and (and (policy-autonomous-renewal $selected) (<= $loops 0))
            (== $paused 0))
       (policy-idle-wait $selected)
       $requested))

(= (policy-autonomous-turn $selected $ready $has-activity $paused)
   (and (and (policy-autonomous-renewal $selected) (== $ready 1))
        (and (not $has-activity) (== $paused 0))))

(= (policy-next-autonomous-ready $selected $loops $wait-timed-out $paused)
   (if (and (and (policy-autonomous-renewal $selected) (<= $loops 0))
            (and (== $wait-timed-out 1) (== $paused 0)))
       1
       0))

;; A response that does not cross the command protocol is a provider-boundary
;; failure, not permission to hammer the provider. Keep the agent's remaining
;; energy intact, install positive retry pacing, and restore normal pacing as
;; soon as a valid command sequence crosses the boundary.
(= (applyProtocolPacing $normal $retry $periphery $outcome)
   (coordinate-set sleep-interval
     (if (== $outcome no-commands) (max 1 $retry) $normal)
     $periphery))
