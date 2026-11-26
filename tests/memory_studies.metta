; Attention studies over the native memory world (loaded by
; tests/cognitive_memory.metta after tests/memory_world.metta and
; tests/memory_attention.metta): an optional catalogue whose choices persist,
; reference operators against independent arithmetic, finite diffusion
; against its Poisson oracle, optional arms that leave explicit similarity
; alone, a walk beyond the initial pool, bounded updates, feedback that
; changes no evidence, temporary inhibition, a comparison that writes
; nothing, and scoped queries that stay scoped.

(= (mst:quiet) "
!(attention-policy \"exposure\" 0)
!(attention-policy \"bootstrap-neighbours\" 0)
!(attention-policy \"spread-rounds\" 0)
!(attention-policy \"seed-similarity-weight\" 1)
")
(= (mst:operators $root) (str:join "" ("!(import! &self " (repr (path:join $root "experiments/attention/operators.metta")) ")\n")))
(= (mst:guest $world $root $forms $env)
   (mc:guest $world $root (str:join "" ((mat:guest-module $root) (mst:operators $root) $forms)) $env))

(= (mst:unique $xs) (if (== $xs ()) True (if (mst:member (car-atom $xs) (cdr-atom $xs)) False (mst:unique (cdr-atom $xs)))))
(= (mst:member $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (mst:member $x (cdr-atom $xs)))))
(= (mst:filled $v) (not (or (== $v (JsonString "")) (or (== $v (JsonArray ())) (== $v JsonNull)))))
(= (mst:all-filled $threads)
   (if (== $threads ()) True
     (let $t (car-atom $threads)
       (if (and (mst:filled (preq:field $t "tests")) (and (mst:filled (preq:field $t "limit")) (mst:filled (preq:field $t "stage"))))
           (mst:all-filled (cdr-atom $threads)) False))))

(= (mst:catalogue $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-catalogue"))
          ((JsonArray $threads) (preq:field (preq:parse (fs:read-text (path:join $root "src/attention_studies.json"))) "threads"))
          ($_ (nt:require the-catalogue-lists-ten-distinct-complete-threads
                (and (== (size-atom $threads) 10)
                     (and (mst:unique (map-atom $threads $t (preq:field $t "id"))) (mst:all-filled $threads)))))
          ($out (mc:guest $world $root "
!(println! (CATALOGUE (attention-studies)))
!(println! (HELP (api:help \"attention-try\")))
!(println! (SET (attention-policy \"study-arm\" \"power-law\")))
!(println! (NUMERIC (attention-policy \"study-mix\" \"0.4\")))
!(println! (BAD (attention-policy \"study-arm\" \"typo\")))
!(println! (CURRENT (setting \"attention\" \"study-arm\")))" $env))
          ($_ (nt:require the-catalogue-is-discoverable-and-its-choices-validated
                (mc:has-all $out ("semantic-walk" "offline-reference" "(CommandHelp \"attention-study\" \"attention-try\""
                                  "(AttentionConfigured \"study-arm\" \"power-law\" true)"
                                  "(AttentionConfigured \"study-mix\" 0.4 true)" "(SettingError \"attention\" \"study-arm\""))))
          ($_ (nt:require a-study-choice-persists
                (nt:contains (mc:guest $world $root "!(println! (RESTORED (attention-policy \"study-arm\")))" $env)
                             "(RESTORED \"power-law\")"))))
     (mc:stop $server)))

(= (mst:operators-case $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-operators"))
          ($out (mst:guest $world $root "
!(let $soft (study:softmax ((1000 \"a\") (1001 \"b\")) 1)
   (nt:require reference-operators-match-independent-arithmetic
     (and (ox:near5 (ax:power 2 60 60 1) 0.5)
     (and (ox:near5 (ax:power 2 900 0 1) (/ 2 3))
     (and (ox:near5 (ax:gain 3 1 1) (/ 2 3))
     (and (ox:near5 (ax:gain 0 0 0) 0.5)
     (and (ox:near5 (ax:inhibition 1 60 0.8 60) 0.6)
     (and (ox:near5 (ax:inhibition 1 0 0.8 0) 1)
     (and (ox:near5 (study:actr-sum 100 (0 75 99) 0.5 1) 1.3)
     (and (ox:near5 (study:bounded-hebb 0.25 1 1 0.2) 0.4)
     (and (ox:near5 (ox:scores-sum $soft) 1)
          (ox:near5 (ox:score \"b\" $soft) 0.7310585786))))))))))))
!(println! OPERATORS_OK)" $env))
          ($_ (nt:require the-operators-guest-completed (nt:contains $out "OPERATORS_OK"))))
     (mc:stop $server)))

(= (mst:diffusion $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-diffusion"))
          ($out (mst:guest $world $root (str:concat (mst:quiet) "
!(semantic:query \"alpha\" 0 \"similarity\")
!(attention-link \"a\" \"b\" 1)
!(let* (($heat (study:heat (am:bank) ((AttentionMass \"a\" 1)) 1 2))
        ($masses (ox:heads AttentionMass $heat))
        ((MissingMass $missing) (car-atom (ox:heads MissingMass $heat)))
        ($a (ox:mass-of \"a\" $masses)) ($b (ox:mass-of \"b\" $masses)))
   (nt:require finite-diffusion-matches-the-poisson-oracle
     (and (ox:near5 $a (math:exp -1)) (and (ox:near5 $b (* 1.5 (math:exp -1))) (ox:near5 (+ $missing (+ $a $b)) 1)))))
!(println! (STICKY (list:retain-top-k-keyed
   (study:focus ((0.8 \"a\") (0.3 \"b\")) (\"b\") 0.6) 1)))
!(println! (RESET (list:retain-top-k-keyed
   (study:focus ((0.8 \"a\") (0.3 \"b\")) () 0.6) 1)))
!(let* (($broad (study:softmax ((0 \"a\") (1 \"b\")) 10)) ($narrow (study:softmax ((0 \"a\") (1 \"b\")) 0.1)))
   (nt:require temperature-narrows-the-choice
     (and (ox:near5 (ox:scores-sum $broad) 1) (and (ox:near5 (ox:scores-sum $narrow) 1)
          (> (ox:score \"b\" $narrow) (ox:score \"b\" $broad))))))
!(println! DIFFUSION_OK)") $env))
          ($_ (nt:require focus-can-lock-in-and-reset
                (and (nt:contains $out "DIFFUSION_OK")
                     (and (nt:contains (mc:line $out "(STICKY ") "\"b\"") (nt:contains (mc:line $out "(RESET ") "\"a\""))))))
     (mc:stop $server)))

(= (mst:power-arm $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-power"))
          ($out (mc:guest $world $root (str:concat (mst:quiet) "
!(attention-use \"b\" 1)
!(attention-policy \"study-mix\" 1)
!(println! (BASE (semantic:query \"alpha\" 1 \"hybrid\")))
!(println! (POWER (attention-try \"power-law\" \"alpha\" 1)))
!(println! (WHY (attention-last)))
!(println! (UNCHANGED (attention-policy \"study-arm\")))
!(attention-policy \"study-arm\" \"power-law\")
!(println! (SIMILARITY (semantic:query \"alpha\" 1 \"similarity\")))
!(attention-policy \"study-mix\" 0)
!(println! (ZERO_MIX (semantic:query \"alpha\" 1 \"hybrid\")))
!(println! (BAD_TRY (attention-try \"typo\" \"alpha\")))") $env))
          ($why (mc:line $out "(WHY "))
          ($_ (nt:require an-arm-is-optional-and-explicit-similarity-keeps-its-order
                (and (mc:has-all $out ("(BASE (MemoryResults ((MemoryHit \"a\"" "(POWER (MemoryResults ((MemoryHit \"b\""
                                       "(SIMILARITY (MemoryResults ((MemoryHit \"a\"" "(ZERO_MIX (MemoryResults ((MemoryHit \"a\""
                                       "(UNCHANGED \"baseline\")" "(StudyError selector"))
                     (mc:has-all $why ("ExperimentalUncertified" "(AttentionStudy \"power-law\""))))))
     (mc:stop $server)))

(= (mst:walk $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-walk"))
          ($_ (nt:require walk-records-join-the-store
                (mc:add $socket "st-walk" (("c" (tensor:from f64 (3) (0.0 0.0 1.0)) "synthetic bridge" ())
                                          ("d" (tensor:from f64 (3) (-1.0 0.0 0.0)) "synthetic destination" ())))))
          ($out (mc:guest $world $root (str:concat (mst:quiet) "
!(attention-policy \"candidate-multiplier\" 1)
!(attention-policy \"spread-rounds\" 3)
!(attention-policy \"restart\" 0.1)
!(attention-policy \"study-mix\" 1)
!(attention-link \"a\" \"c\" 1)
!(attention-link \"c\" \"d\" 1)
!(attention-feedback \"d\" 1 100)
!(println! (REACHED (attention-try \"gain-need\" \"alpha\" 1)))
!(println! (EXPLAIN (attention-last)))") $env))
          ($_ (nt:require an-experimental-walk-reaches-beyond-the-similarity-pool
                (mc:has-all $out ("(REACHED (MemoryResults ((MemoryHit \"d\"" "(Spread (Rounds 3) ExperimentalUncertified)")))))
     (mc:stop $server)))

(= (mst:bounded $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-bounded"))
          ($out (mst:guest $world $root "
!(nt:require random-bounded-updates-stay-in-range-and-match-arithmetic (== (ox:hebb-rows 134 40 0) 40))
; Two histories of equal count and latest use, with different ACT-R sums.
!(nt:require the-fresher-history-sums-higher
   (> (study:actr-sum 100 (98 99) 0.5 1) (study:actr-sum 100 (0 99) 0.5 1)))
!(println! (SAME_PROXY (ax:power 2 1 60 0.5)))
!(println! BOUNDED_OK)" $env))
          ($_ (nt:require the-bounded-guest-completed (nt:contains $out "BOUNDED_OK"))))
     (mc:stop $server)))

(= (mst:evidence $world $root $env)
   (mc:line (mc:guest $world $root "!(println! (EVIDENCE (memory-read \"a\") (memory-read \"b\")))" $env) "(EVIDENCE "))
(= (mst:feedback $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-feedback"))
          ($original (mst:evidence $world $root $env))
          ($out (mst:guest $world $root (str:concat (mst:quiet) "
!(attention-policy \"study-mix\" 1)
!(println! (BAD (attention-feedback \"a\" 2)))
!(println! (FEEDBACK_A (attention-feedback \"a\" 0 100)))
!(println! (FEEDBACK_B (attention-feedback \"b\" 1 100)))
!(println! (GAIN (attention-try \"gain-need\" \"alpha\" 1)))
!(println! (ORDINARY (attention-try \"baseline\" \"alpha\" 1)))
!(println! (TRACE (ax:trace (am:bank) \"b\")))") $env))
          ($_ (nt:require feedback-changes-the-gain-proxy
                (mc:has-all $out ("(StudyError feedback" "(FEEDBACK_B (StudyFeedback \"b\"" "(GAIN (MemoryResults ((MemoryHit \"b\""
                                  "(ORDINARY (MemoryResults ((MemoryHit \"a\""))))
          ($_ (nt:require feedback-changes-no-evidence (== (mst:evidence $world $root $env) $original)))
          ($later (mst:guest $world $root "
!(println! (RESTORED (attention-try \"gain-need\" \"alpha\" 1)))
!(println! (TRACE (ax:trace (am:bank) \"b\")))
!(forget \"b\")
!(println! (AFTER (ax:state (am:bank))))
!(println! (MISSING (attention-feedback \"b\" 1)))" $env))
          ($_ (nt:require feedback-survives-restart-and-leaves-with-its-memory
                (and (mc:has-all $later ("(RESTORED (MemoryResults ((MemoryHit \"b\"" "(StudyError memory"))
                     (not (nt:contains (mc:line $later "(AFTER ") "\"b\""))))))
     (mc:stop $server)))

(= (mst:inhibition $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-inhibition"))
          ($out (mst:guest $world $root (str:concat (mst:quiet) "
!(attention-policy \"study-mix\" 1)
!(attention-policy \"inhibition-strength\" 1)
!(println! (FIRST (attention-try \"baseline\" \"alpha\" 1)))
!(println! (ALTERNATIVE (attention-try \"inhibition\" \"alpha\" 1)))
!(println! (DIRECT (memory-read \"a\")))
!(println! (SIM (semantic:query \"alpha\" 1 \"similarity\")))
!(let* (($bank (am:bank))
        ((StudyTrace $id $n $first $last $shown $latest $pos $neg) (ax:trace $bank \"a\"))
        ($aged (- (system:wall-time) 1200))
        ($row (StudyTrace $id $n $first $last $shown $aged $pos $neg)))
   (ax:replace $bank $row))
!(println! (RETURN (attention-try \"inhibition\" \"alpha\" 1)))
!(attention-policy \"inhibition-strength\" 0)
!(println! (DISABLE (attention-try \"inhibition\" \"alpha\" 1)))") $env))
          ($_ (nt:require inhibition-is-temporary-and-blocks-no-repeated-evidence
                (mc:has-all $out ("(FIRST (MemoryResults ((MemoryHit \"a\"" "(ALTERNATIVE (MemoryResults ((MemoryHit \"b\""
                                  "(DIRECT (MemoryCurrent \"a\"" "(SIM (MemoryResults ((MemoryHit \"a\""
                                  "(RETURN (MemoryResults ((MemoryHit \"a\"" "(DISABLE (MemoryResults ((MemoryHit \"a\"")))))
     (mc:stop $server)))

(= (mst:compare $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-compare"))
          ($_ (mst:guest $world $root (str:concat (mst:quiet) "
!(attention-use \"b\")
!(attention-feedback \"a\" 0 10)
!(attention-feedback \"b\" 1 10)") $env))
          ($out (mst:guest $world $root "
!(semantic:query \"alpha\" 1 \"hybrid\")
!(println! (BEFORE (fs:read-text (am:path))))
!(println! (COMPARE (attention-compare 1)))
!(println! (AFTER (fs:read-text (am:path))))
!(println! (AGAIN (attention-compare 1)))" $env))
          ($comparison (mc:line $out "(COMPARE "))
          ($_ (nt:require a-same-pool-comparison-neither-trains-nor-saves
                (and (== (mc:line $out "(BEFORE ") (mc:line $out "(AFTER "))
                (and (== $comparison (mc:line $out "(AGAIN "))
                     (mc:has-all $comparison ("(SameCandidatePool true) (PreQueryObservations true)"
                                              "(StudyRanking \"baseline\"" "(StudyRanking \"power-law\""
                                              "(StudyRanking \"gain-need\"" "(StudyRanking \"inhibition\""))))))
          ($_ (nt:require the-comparison-has-content (not (== $comparison "")))))
     (mc:stop $server)))

(= (mst:scoped $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "st-scoped"))
          ($out (mst:guest $world $root (str:concat (mst:quiet) "
!(workspace \"only-a\" (\"task:a\") () \"task:a\")
!(workspace-tag \"a\" (\"task:a\"))
!(attention-use \"b\" 100)
!(attention-feedback \"b\" 1 100)
!(workspace \"only-a\")
!(attention-policy \"study-mix\" 1)
!(println! (POWER (attention-try \"power-law\" \"alpha\" 0)))
!(println! (GAIN (attention-try \"gain-need\" \"alpha\" 0)))
!(println! (IOR (attention-try \"inhibition\" \"alpha\" 0)))
!(println! (COMPARISON (attention-compare 0)))") $env))
          ($_ (nt:require scoped-arms-keep-outside-memories-out
                (and (mst:only-a (mc:line $out "(POWER "))
                (and (mst:only-a (mc:line $out "(GAIN "))
                (and (mst:only-a (mc:line $out "(IOR "))
                     (not (nt:contains (mc:line $out "(COMPARISON ") "\"b\""))))))))
     (mc:stop $server)))
(= (mst:only-a $line) (and (nt:contains $line "(MemoryHit \"a\"") (not (nt:contains $line "(MemoryHit \"b\""))))

(= (mst:run $world $service $root $sdk)
   (let* (($_ (mst:catalogue $world $service $root))
          ($_ (mst:operators-case $world $service $root))
          ($_ (mst:diffusion $world $service $root))
          ($_ (mst:power-arm $world $service $root))
          ($_ (mst:walk $world $service $root))
          ($_ (mst:bounded $world $service $root))
          ($_ (mst:feedback $world $service $root))
          ($_ (mst:inhibition $world $service $root))
          ($_ (mst:compare $world $service $root))
          ($_ (mst:scoped $world $service $root)))
     True))
