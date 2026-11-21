; Exhaustive verb-by-verb and randomized differential oracle for
; src/conversation_index_cetta.metta against unindexed
; input-ledger:conversation-rows-of on a real durable store.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/input_ledger.metta)

!(bind! &diff-mirror (new-state DiffMirrorNone))
!(bind! &diff-seq (new-state 0))
!(bind! &diff-checks (new-state 0))
!(bind! &diff-rng (new-state 133742))

(= (diff:next-id $prefix)
   (let* (($n (+ (get-state &diff-seq) 1))
          ($_ (change-state! &diff-seq $n)))
     (str:concat $prefix (repr $n))))

(= (diff:rand $bound)
   (let* (($s (get-state &diff-rng))
          ($next (% (+ (* $s 1103515245) 12345) 2147483647))
          ($_ (change-state! &diff-rng $next)))
     (% $next $bound)))

(= (diff:oracle $db $focus $offset $limit)
   (case (db:snapshot $db inputs)
     (((Error $w $r) (Error diff:oracle ($w $r)))
      ($view
       (let* (($ans (input-ledger:conversation-rows-of $view $focus $offset $limit))
              ($_ (db:close $view)))
         $ans)))))

(= (diff:apply-delta $offset $limit $focus $delta)
   (case $delta
     (((conversation-delta $gen $rrev $nrev $erev $total $rm $nm $em $from-rel $dreqs $dnotes $drows)
       (case (get-state &diff-mirror)
         (((DiffMirrorV1 $m-off $m-lim $m-gen $m-rr $m-nr $m-er $m-tot $m-foc $m-reqs $m-notes $m-rows)
           (let* (($reqs (if (== $rm same) $m-reqs $dreqs))
                  ($notes (if (== $nm same) $m-notes $dnotes))
                  ($rows (case $em
                           ((same $m-rows)
                            (full $drows)
                            (tail (if (== $from-rel 0) $drows
                                    (union-atom (list:take $from-rel $m-rows) $drows))))))
                  ($_ (change-state! &diff-mirror
                        (DiffMirrorV1 $offset $limit $gen $rrev $nrev $erev $total $focus $reqs $notes $rows))))
             (conversation-rows $reqs $notes $rows)))
          ($none
           (let $_ (change-state! &diff-mirror
                     (DiffMirrorV1 $offset $limit $gen $rrev $nrev $erev $total $focus $dreqs $dnotes $drows))
             (conversation-rows $dreqs $dnotes $drows))))))
      ($bad (Error diff:apply-delta $bad)))))

(= (diff:delta-read $db $focus $offset $limit)
   (case (get-state &diff-mirror)
     (((DiffMirrorV1 $m-off $m-lim $m-gen $m-rr $m-nr $m-er $m-tot $m-foc $m-reqs $m-notes $m-rows)
       (if (and (== $m-off $offset) (== $m-lim $limit))
         (diff:apply-delta $offset $limit $focus
           (input-ledger:perform $db "conversation-delta"
             ($focus $offset $limit $m-gen $m-rr $m-nr $m-er $m-tot $m-foc)))
         (let $_ (change-state! &diff-mirror DiffMirrorNone)
           (diff:apply-delta $offset $limit $focus
             (input-ledger:perform $db "conversation-delta"
               ($focus $offset $limit none 0 0 0 0 ""))))))
      ($none
       (diff:apply-delta $offset $limit $focus
         (input-ledger:perform $db "conversation-delta"
           ($focus $offset $limit none 0 0 0 0 "")))))))

(= (diff:check-at $label $db $focus $offset $limit)
   (let* (($oracle (diff:oracle $db $focus $offset $limit))
          ($indexed (input-ledger:perform $db "conversation-rows" ($focus $offset $limit)))
          ($via-delta (diff:delta-read $db $focus $offset $limit))
          ($o-txt (repr $oracle))
          ($i-txt (repr $indexed))
          ($d-txt (repr $via-delta))
          ($n (+ (get-state &diff-checks) 1))
          ($_ (change-state! &diff-checks $n)))
     (if (and (== $i-txt $o-txt) (== $d-txt $o-txt))
       True
       (let* (($_ (println! (DIFF_MISMATCH $label $offset $limit)))
              ($_ (println! (ORACLE $o-txt)))
              ($_ (println! (INDEXED $i-txt)))
              ($_ (println! (DELTA $d-txt))))
         (nt:require $label False)))))

(= (diff:check $label $db $focus)
   (let* (($_1 (diff:check-at $label $db $focus 0 40000))
          ($_2 (diff:check-at $label $db $focus 0 24)))
     True))

(= (diff:step $label $db $verb $args $focus)
   (let* (($ans (input-ledger:perform $db $verb $args))
          ($_ (diff:check $label $db $focus)))
     $ans))

(= (diff:builds $db)
   (case (input-ledger:perform $db "conv-index-stats" ())
     (((conv-index-stats $b $d $m) $b)
      ($other -1))))

(= (diff:open-store $path)
   (let* (($db (db:open $path))
          ($_ (input-ledger:ensure $db))
          ($_ (cidx:reset))
          ($_ (change-state! &cidx-builds 0))
          ($_ (change-state! &cidx-deltas 0))
          ($_ (change-state! &diff-mirror DiffMirrorNone)))
     $db))

(= (diff:random-step $db $i $owner $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)
   (let* (($op (diff:rand 20))
          ($rid (diff:next-id "rnd-w-"))
          ($lbl (str:concat "rnd-" (repr $i))))
     (case $op
       ((0 ; request-accept
         (let* (($new-r (str:concat "rnd-req-" (repr $i)))
                ($_ (diff:step $lbl $db "request-accept" ($new-r (str:concat "Random body payload number " (repr $i)) "operator" "message" "sum" "dig" $rid) $focus)))
           ($db (cons-atom $new-r $reqs) $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $new-r)))
        (1 ; task-note
         (let* (($target (car-atom $reqs))
                ($_ (diff:step $lbl $db "task-note" ($target (str:concat "rnd-note-" (repr $i)) "[]" $rid) $focus)))
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (2 ; capture
         (let* (($ans (diff:step $lbl $db "capture" ($focus "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner $rid) $focus))
                ($ser (case $ans (((captured $s $_r $_m $_o) $s) ($other $pending-ser)))))
           ($db $reqs $ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (3 ; task-proposal on pending-ser
         (if (== $pending-ser none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let $_ (diff:step $lbl $db "task-proposal" ($pending-ser $focus "[[\"shell\",\"rnd\"]]" "not run: batch interrupted" $rid) $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))))
        (4 ; decide command on pending-ser
         (if (== $pending-ser none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let* (($ans (diff:step $lbl $db "decide" ($pending-ser "command" "batch" "{\"commands\":[[\"shell\",\"rnd\"]],\"limit\":1}" $rid) $focus))
                  ($tkt (case $ans (((issued $t) $t) ($other $issued-tkt)))))
             ($db $reqs none $tkt $reported-tkt $observed-tkt $attested-tkt $focus))))
        (5 ; attempt-begin + attempt-result on issued-tkt
         (if (== $issued-tkt none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let* (($_1 (diff:step (str:concat $lbl "-ab") $db "attempt-begin" ($issued-tkt 0 "[\"shell\",\"rnd\"]" (diff:next-id "rnd-ab-")) $focus))
                  ($body (if (== (% $i 2) 0) "short-ok" "LONG_RESULT_PAYLOAD_EXCEEDING_24_CHAR_TRUNCATION_LIMIT_XYZ"))
                  ($res (str:join "" ("{\"disposition\":\"returned\",\"result\":\"" $body "\"}")))
                  ($_2 (diff:step (str:concat $lbl "-ar") $db "attempt-result" ($issued-tkt 0 "[\"shell\",\"rnd\"]" $res (diff:next-id "rnd-ar-")) $focus)))
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))))
        (6 ; report on issued-tkt
         (if (== $issued-tkt none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let $_ (diff:step $lbl $db "report" ($issued-tkt "rnd-payload" "returned" "unknown" "dg1234" 11 $rid) $focus)
             ($db $reqs $pending-ser none $issued-tkt $observed-tkt $attested-tkt $focus))))
        (7 ; observe on reported-tkt
         (if (== $reported-tkt none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let $_ (diff:step $lbl $db "observe" ((($reported-tkt "dg1234")) $rid) $focus)
             ($db $reqs $pending-ser $issued-tkt none $reported-tkt $attested-tkt $focus))))
        (8 ; attest on observed-tkt
         (if (== $observed-tkt none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let $_ (diff:step $lbl $db "attest" ($focus $observed-tkt $rid) $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt none $observed-tkt $focus))))
        (9 ; finish on attested-tkt
         (if (== $attested-tkt none)
           (let $_ (diff:check $lbl $db $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus))
           (let $_ (diff:step $lbl $db "finish" ($focus $attested-tkt $rid) $focus)
             ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt none $focus))))
        (10 ; restore
         (let $_ (diff:step $lbl $db "restore" ($rid) $focus)
           ($db $reqs none $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (11 ; control
         (let $_ (diff:step $lbl $db "control" ((if (== (% $i 2) 0) "godelclaw" "coding") False none $rid) $focus)
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (12 ; request-disposition
         (let* (($target (car-atom $reqs))
                ($d (if (== (% $i 3) 0) "defer" (if (== (% $i 3) 1) "waiting-user" "keep")))
                ($_ (diff:step $lbl $db "request-disposition" ($target $d (str:concat "disp-" (repr $i)) $rid) $focus)))
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (13 ; checkpoint
         (let* (($target (if (== (% $i 2) 0) "" (car-atom $reqs)))
                ($_ (diff:step $lbl $db "checkpoint" ("[]" ((str:concat "inp-" (repr $i))) $target (+ 1700000000 $i) $rid) $focus)))
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (14 ; topic-note
         (let $_ (diff:step $lbl $db "topic-note" ((str:concat "topic-note-" (repr $i)) $rid) $focus)
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (15 ; meta-put
         (let $_ (diff:step $lbl $db "meta-put" ((str:concat "rnd_meta:" (repr $i)) "{\"v\":1}" $rid) $focus)
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (16 ; mark-processed
         (let $_ (diff:step $lbl $db "mark-processed" ((+ 1700000500 $i) ((str:concat "mp-" (repr $i)))) $focus)
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (17 ; llm-admit (provider collection)
         (let $_ (diff:step $lbl $db "llm-admit" ((str:concat "llm-tkt-" (repr $i)) "openai" 4096 "fp" "default" "turn" $owner "{}" (+ 1700001000 $i) $rid) $focus)
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))
        (18 ; switch focus + task-focus
         (let* (($next-f (index-atom $reqs (% $i (size-atom $reqs))))
                ($_ (diff:step $lbl $db "task-focus" ($next-f $rid) $next-f)))
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $next-f)))
        (19 ; request-settle or request-answered
         (let* (($target (car-atom $reqs))
                ($_ (if (== (% $i 2) 0)
                      (diff:step $lbl $db "request-settle" ($target (+ 1700002000 $i) $rid) $focus)
                      (diff:step $lbl $db "request-answered" ($target $rid) $focus))))
           ($db $reqs $pending-ser $issued-tkt $reported-tkt $observed-tkt $attested-tkt $focus)))))))

(= (diff:run-random $db $i $limit $owner $reqs $p $is $rp $ob $at $foc)
   (if (>= $i $limit) True
     (let ($db2 $reqs2 $p2 $is2 $rp2 $ob2 $at2 $foc2)
          (diff:random-step $db $i $owner $reqs $p $is $rp $ob $at $foc)
       (diff:run-random $db2 (+ $i 1) $limit $owner $reqs2 $p2 $is2 $rp2 $ob2 $at2 $foc2))))

!(let* (($home (nt:required-env "HOME"))

        ;; =====================================================================
        ;; Suite A: Fresh store BEFORE owner — index built while owner=absent
        ;; must reset on first owner write, then stay warm on second owner write.
        ;; =====================================================================
        ($dbA (diff:open-store (path:join $home "store-a")))
        ($_a0 (diff:check fresh-empty-before-owner $dbA "req-a"))
        ($_a1 (nt:require suiteA-build-1 (nt:equal (diff:builds $dbA) 1)))
        ($_a2 (diff:step fresh-accept-before-owner $dbA "request-accept" ("req-a" "Body A" "" "message" "sa" "da" "w-a1") "req-a"))
        ($ownA (diff:step fresh-first-owner-resets-index $dbA "owner" ("owner-A" "w-a2") "req-a"))
        ($_a3 (nt:require suiteA-owner-value (nt:equal $ownA "owner-A")))
        ($_a4 (nt:require suiteA-owner-triggered-rebuild (nt:equal (diff:builds $dbA) 2)))
        ($ownA2 (diff:step second-owner-stays-warm $dbA "owner" ("owner-OTHER" "w-a3") "req-a"))
        ($_a5 (nt:require suiteA-second-owner-keeps-first (nt:equal $ownA2 "owner-A")))
        ($_a6 (nt:require suiteA-second-owner-no-rebuild (nt:equal (diff:builds $dbA) 2)))
        ((captured $serA $_rA $_mA $cownA) (diff:step suiteA-capture $dbA "capture" ("req-a" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" "owner-A" "w-a4") "req-a"))
        ($_a7 (diff:step suiteA-proposal $dbA "task-proposal" ($serA "req-a" "[[\"shell\",\"a\"]]" "not run" "w-a5") "req-a"))
        ((issued $tktA) (diff:step suiteA-decide $dbA "decide" ($serA "command" "batch" "{\"commands\":[[\"shell\",\"a\"]],\"limit\":1}" "w-a6") "req-a"))
        ($_a8 (diff:step suiteA-att-begin $dbA "attempt-begin" ($tktA 0 "[\"shell\",\"a\"]" "w-a7") "req-a"))
        ($_a9 (diff:step suiteA-att-res $dbA "attempt-result" ($tktA 0 "[\"shell\",\"a\"]" "{\"disposition\":\"returned\",\"result\":\"ok-A\"}" "w-a8") "req-a"))
        ($_a10 (nt:require suiteA-zero-extra-rebuilds (nt:equal (diff:builds $dbA) 2)))
        ($_ac (db:close $dbA))

        ;; =====================================================================
        ;; Suite B: Fresh store BEFORE capture — capture sets owner from absent!
        ;; =====================================================================
        ($dbB (diff:open-store (path:join $home "store-b")))
        ($_b0 (diff:check fresh-empty-before-capture $dbB "req-b"))
        ($_b1 (diff:step suiteB-accept $dbB "request-accept" ("req-b" "Body B" "" "message" "sb" "db" "w-b1") "req-b"))
        ((captured $serB $_rB $_mB $cownB) (diff:step suiteB-first-capture-sets-owner $dbB "capture" ("req-b" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" "owner-B" "w-b2") "req-b"))
        ($_b2 (nt:require suiteB-capture-triggered-rebuild (nt:equal (diff:builds $dbB) 2)))
        ($_b3 (diff:step suiteB-proposal $dbB "task-proposal" ($serB "req-b" "[[\"shell\",\"b\"]]" "not run" "w-b3") "req-b"))
        ((issued $tktB) (diff:step suiteB-decide $dbB "decide" ($serB "command" "batch" "{\"commands\":[[\"shell\",\"b\"]],\"limit\":1}" "w-b4") "req-b"))
        ($_b4 (diff:step suiteB-att-begin $dbB "attempt-begin" ($tktB 0 "[\"shell\",\"b\"]" "w-b5") "req-b"))
        ($_b5 (diff:step suiteB-att-res $dbB "attempt-result" ($tktB 0 "[\"shell\",\"b\"]" "{\"disposition\":\"returned\",\"result\":\"ok-B\"}" "w-b6") "req-b"))
        ($_bc (db:close $dbB))

        ;; =====================================================================
        ;; Suite C: Exhaustive verb-by-verb differential check across EVERY
        ;; write verb in input_ledger.metta + llm_ledger.metta.
        ;; =====================================================================
        ($db (diff:open-store (path:join $home "store-c")))
        ($owner "diff-owner")
        ($_c0 (input-ledger:perform $db "owner" ($owner "c-own")))
        ($_c1 (diff:check c-init $db "req-1"))
        ($b-base (diff:builds $db))

        ;; Request & task lifecycle verbs (delta-maintained)
        ($_ (diff:step v-request-accept-1 $db "request-accept" ("input:old" "Old input batch body" "operator" "message" "s1" "d1" "c-1") "input:old"))
        ($_ (diff:step v-request-accept-dup $db "request-accept" ("input:old" "Old input batch body" "operator" "message" "s1" "d1" "c-2") "input:old"))
        ($_ (diff:step v-link-inputs-1 $db "link-inputs" ("input:old" ("tg:1") "c-3") "input:old"))
        ($_ (diff:step v-task-begin-1 $db "task-begin" ("input:old" "operator-event" "" "chat-1" "" "c-4") "input:old"))
        ($_ (diff:step v-request-accept-2 $db "request-accept" ("req-1" "Primary active task request" "" "message" "s2" "d2" "c-5") "req-1"))
        ($_ (diff:step v-task-begin-2 $db "task-begin" ("req-1" "operator-event" "" "" "" "c-6") "req-1"))
        ($_ (diff:step v-task-focus $db "task-focus" ("req-1" "c-7") "req-1"))
        ($_ (diff:step v-task-note-1 $db "task-note" ("req-1" "first note body" "[]" "c-8") "req-1"))
        ($_ (diff:step v-task-note-2 $db "task-note" ("req-1" "second note body" "[]" "c-9") "req-1"))

        ;; Cover input:old via superset input:new
        ($_ (diff:step v-accept-cov $db "request-accept" ("input:new" "Covering superset batch" "operator" "message" "s3" "d3" "c-10") "req-1"))
        ($_ (diff:step v-link-cov $db "link-inputs" ("input:new" ("tg:1" "tg:2") "c-11") "req-1"))
        ($_ (diff:step v-begin-cov $db "task-begin" ("input:new" "operator-event" "" "chat-1" "" "c-12") "req-1"))

        ;; Full turn lifecycle: capture -> proposal (new + update) -> decide -> attempt-begin -> attempt-result (truncated!) -> report -> observe -> attest -> finish
        ((captured $s0 $_ $_ $_) (diff:step v-capture-0 $db "capture" ("req-1" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner "c-13") "req-1"))
        ($_ (diff:step v-proposal-new $db "task-proposal" ($s0 "req-1" "[[\"shell\",\"echo hi\"]]" "not run: batch interrupted" "c-14") "req-1"))
        ($_ (diff:step v-proposal-update $db "task-proposal" ($s0 "req-1" "[[\"shell\",\"echo hi\"]]" "admitted" "c-15") "req-1"))
        ((issued $t0) (diff:step v-decide-cmd $db "decide" ($s0 "command" "batch" "{\"commands\":[[\"shell\",\"echo hi\"]],\"limit\":1}" "c-16") "req-1"))
        ($_ (diff:step v-attempt-begin $db "attempt-begin" ($t0 0 "[\"shell\",\"echo hi\"]" "c-17") "req-1"))
        ($_ (diff:step v-attempt-result-long $db "attempt-result" ($t0 0 "[\"shell\",\"echo hi\"]" "{\"disposition\":\"returned\",\"result\":\"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ\"}" "c-18") "req-1"))
        ($_ (diff:step v-report $db "report" ($t0 "payload-0" "returned" "unknown" "digest-0" 9 "c-19") "req-1"))
        ($_ (diff:step v-observe $db "observe" ((($t0 "digest-0")) "c-20") "req-1"))
        ($_ (diff:step v-attest $db "attest" ("req-1" $t0 "c-21") "req-1"))

        ;; Non-zero offset check mid-stream
        ($_ (diff:check-at v-nonzero-offset $db "req-1" 2 40000))

        ;; Decide text & failure turns + restore with active pending turn & issued action!
        ((captured $s1 $_ $_ $_) (diff:step v-capture-text $db "capture" ("req-1" "1111111111111111111111111111111111111111111111111111111111111111" "{}" "" $owner "c-22") "req-1"))
        ($_ (diff:step v-decide-text $db "decide" ($s1 "text" "" "" "c-23") "req-1"))
        ((captured $s2 $_ $_ $_) (diff:step v-capture-fail $db "capture" ("req-1" "2222222222222222222222222222222222222222222222222222222222222222" "{}" "" $owner "c-24") "req-1"))
        ($_ (diff:step v-decide-fail $db "decide" ($s2 "failure" "" "" "c-25") "req-1"))
        ((captured $s3 $_ $_ $_) (diff:step v-capture-for-restore $db "capture" ("req-1" "3333333333333333333333333333333333333333333333333333333333333333" "{}" "" $owner "c-26") "req-1"))
        ($_ (diff:step v-prop-for-restore $db "task-proposal" ($s3 "req-1" "[[\"nop\"]]" "not run" "c-27") "req-1"))
        ((issued $t3) (diff:step v-decide-for-restore $db "decide" ($s3 "command" "batch" "{\"commands\":[[\"nop\"]],\"limit\":1}" "c-28") "req-1"))
        ((captured $s4 $_ $_ $_) (diff:step v-capture-pending-for-restore $db "capture" ("req-1" "4444444444444444444444444444444444444444444444444444444444444444" "{}" "" $owner "c-29") "req-1"))
        ($_ (diff:step v-restore $db "restore" ("c-30") "req-1"))

        ;; ALL remaining disjoint verbs (every single one!)
        ($_ (diff:step v-mark-processed $db "mark-processed" (1700000001 ("u-1" "u-2")) "req-1"))
        ($_ (diff:step v-register $db "register" ("ntask-1" "u-3" "lane-1" "skey-1" 1700000002) "req-1"))
        ($_ (diff:step v-submitted $db "submitted" ("ntask-1") "req-1"))
        ($_ (diff:step v-receipt $db "receipt" ("skey-1" (delivered 9001)) "req-1"))
        ($_ (diff:step v-notice-withdraw $db "notice-withdraw" ("ntask-1" "nw-1") "req-1"))
        ($_ (diff:step v-notice-cleanup-key $db "notice-cleanup-key" ("ntask-1" "dkey-1" "nc-1") "req-1"))
        ($_ (diff:step v-notice-delivery $db "notice-delivery" ("dkey-1" (delivered 9001) "nd-1") "req-1"))
        ($_ (diff:step v-register-2 $db "register" ("ntask-2" "u-4" "lane-1" "skey-2" 1700000003) "req-1"))
        ($_ (diff:step v-check $db "check" ("ntask-2" 1700000999 10) "req-1"))
        ($_ (diff:step v-topic-select $db "topic-select" ("research" "ts-1") "req-1"))
        ($_ (diff:step v-topic-note $db "topic-note" ("research notes" "tn-1") "req-1"))
        ($_ (diff:step v-provider-notice-reserve $db "provider-notice-reserve" ("ep-1" "intent-1" "pnr-1") "req-1"))
        ($_ (diff:step v-provider-notice-queued $db "provider-notice-queued" ("ep-1" "pnq-1") "req-1"))
        ($_ (diff:step v-control $db "control" ("coding" False "{\"v\":2}" "ctrl-1") "req-1"))
        ($_ (diff:step v-meta-cas $db "meta-cas" ("task_window:conversation:diff-owner" none "{\"version\":1,\"start\":0,\"summary\":\"\"}" "mc-1") "req-1"))
        ($_ (diff:step v-meta-put $db "meta-put" ("custom_key" "{\"x\":1}" "mp-1") "req-1"))

        ;; ALL llm-* write verbs (provider collection)
        ($_ (diff:step v-llm-admit $db "llm-admit" ("lc-1" "openai" 4096 "fp1" "default" "turn" $owner "{}" 1700000010 "l-1") "req-1"))
        ($_ (diff:step v-llm-describe $db "llm-describe" ("lc-1" "{\"m\":\"gpt\"}" 1700000011 "l-2") "req-1"))
        ($_ (diff:step v-llm-req-sub $db "llm-request-submitted" ("lc-1" 1700000012 "l-3") "req-1"))
        ($_ (diff:step v-llm-claim $db "llm-claim" ("lc-1" "ep-a" 1234 100 "g" "act" "lane" "turn" 0 1 1000 100 0 "{}" 1700000013 "l-4") "req-1"))
        ($_ (diff:step v-llm-submitted $db "llm-submitted" ("lc-1" "ep-a" "http" 1700000014 "l-5") "req-1"))
        ($_ (diff:step v-llm-progress $db "llm-progress" ("lc-1" "ep-a" 512 10 0 1700000015 "l-6") "req-1"))
        ($_ (diff:step v-llm-complete $db "llm-complete" ("lc-1" "ep-a" "ev" 42 "stop" 1700000016 1700000013 "canon" 0 "turn" 1700000016 "l-7") "req-1"))
        ($_ (diff:step v-llm-admit-2 $db "llm-admit" ("lc-2" "openai" 4096 "fp2" "default" "turn" $owner "{}" 1700000017 "l-8") "req-1"))
        ($_ (diff:step v-llm-claim-2 $db "llm-claim" ("lc-2" "ep-a" 1234 100 "g" "act" "lane" "turn" 0 1 1000 100 0 "{}" 1700000018 "l-9") "req-1"))
        ($_ (diff:step v-llm-fail $db "llm-fail" ("lc-2" "ep-a" "turn" 0 1700000019 1700000099 "failed" "timeout" 0 "turn" 1700000019 "l-10") "req-1"))
        ($_ (diff:step v-llm-admit-3 $db "llm-admit" ("lc-3" "openai" 4096 "fp3" "default" "turn" $owner "{}" 1700000020 "l-11") "req-1"))
        ($_ (diff:step v-llm-claim-3 $db "llm-claim" ("lc-3" "ep-a" 1234 100 "g" "act" "lane" "turn" 0 1 1000 100 0 "{}" 1700000021 "l-12") "req-1"))
        ($_ (diff:step v-llm-rejected $db "llm-rejected" ("lc-3" "ep-a" "429" "l-13") "req-1"))
        ($_ (diff:step v-llm-settle $db "llm-settle" ("lc-3" "ep-a" "turn" "late" "" "slow" 1 1700000022 "l-14") "req-1"))
        ($_ (diff:step v-llm-notified $db "llm-notified" ("lc-3" "l-15") "req-1"))
        ($_ (diff:step v-llm-recover $db "llm-recover" (1700000025 "l-16") "req-1"))
        ($_ (diff:step v-llm-recover-reqs $db "llm-recover-requests" (1700000026 "l-17") "req-1"))

        ;; Rare request-status verbs: finish, request-disposition, request-settle, request-answered, request-cancel, request-finish, checkpoint
        ($_ (diff:step v-finish-attested $db "finish" ("req-1" $t0 "c-31") "req-1"))
        ($_ (diff:step v-accept-3 $db "request-accept" ("req-2" "Second task" "" "message" "s4" "d4" "c-32") "req-2"))
        ($_ (diff:step v-disp-defer $db "request-disposition" ("req-2" "defer" "later" "c-33") "req-2"))
        ($_ (diff:step v-disp-wait $db "request-disposition" ("req-2" "waiting-user" "ask" "c-34") "req-2"))
        ($_ (diff:step v-disp-keep $db "request-disposition" ("req-2" "keep" "back" "c-35") "req-2"))
        ($_ (diff:step v-req-settle $db "request-settle" ("req-2" 1700000030 "c-36") "req-2"))
        ($_ (diff:step v-req-answered $db "request-answered" ("req-2" "c-37") "req-2"))
        ($_ (diff:step v-accept-cancel $db "request-accept" ("req-3" "To cancel" "" "message" "s5" "d5" "c-38") "req-3"))
        ($_ (diff:step v-req-cancel $db "request-cancel" ("req-3" "c-39") "req-3"))
        ($_ (diff:step v-accept-fin $db "request-accept" ("req-4" "To finish" "" "message" "s6" "d6" "c-40") "req-4"))
        ($_ (diff:step v-req-finish $db "request-finish" ("req-4" "c-41") "req-4"))
        ($_ (diff:step v-accept-chk $db "request-accept" ("req-5" "To checkpoint" "" "message" "s7" "d7" "c-42") "req-5"))
        ($_ (diff:step v-checkpoint-empty $db "checkpoint" ("[\"x\"]" ("u-9") "" 1700000040 "c-43") "req-5"))
        ($_ (diff:step v-checkpoint-req $db "checkpoint" ("[\"y\"]" ("u-10") "req-5" 1700000041 "c-44") "req-5"))

        ;; Verify ZERO rebuilds across the entire normal + disjoint + llm + status gauntlet!
        ($_ (nt:require zero-rebuilds-across-all-delta-and-disjoint-verbs (nt:equal (diff:builds $db) $b-base)))

        ;; Fail-closed reset verbs: request-retire, legacy unproposed decide+attempt, import, AND unpositioned proposal + clock ticks!
        ($_ (diff:step v-accept-ret $db "request-accept" ("retire:one" "Retirable" "" "message" "sr" "dr" "c-45") "req-5"))
        ($_ (diff:step v-request-retire $db "request-retire" ("retire:" "retired-note" "c-46") "req-5"))
        ((captured $s-leg $_ $_ $_) (diff:step v-cap-legacy $db "capture" ("req-5" "5555555555555555555555555555555555555555555555555555555555555555" "{}" "" $owner "c-47") "req-5"))
        ((issued $t-leg) (diff:step v-decide-legacy $db "decide" ($s-leg "command" "batch" "{\"commands\":[[\"shell\",\"legacy\"]],\"limit\":1}" "c-48") "req-5"))
        ($_ (diff:step v-legacy-att-begin $db "attempt-begin" ($t-leg 0 "[\"shell\",\"legacy\"]" "c-49") "req-5"))
        ($_ (diff:step v-legacy-att-res $db "attempt-result" ($t-leg 0 "[\"shell\",\"legacy\"]" "{\"disposition\":\"returned\",\"result\":\"legacy-res\"}" "c-50") "req-5"))
        ($_ (diff:step v-import $db "import" (any ((processed-input "imported-id" 1700000050))) "req-5"))

        ;; Unpositioned proposal (no ledger:turn at serial 999) -> sets $unpos=True -> every clock-ticker resets cleanly!
        ($_ (diff:step v-unpos-proposal $db "task-proposal" (999 "req-5" "[[\"shell\",\"unpos\"]]" "not run" "c-51") "req-5"))
        ($_ (diff:step v-unpos-accept-ticks-clock $db "request-accept" ("req-after-unpos" "Clock tick with unpos" "" "message" "su" "du" "c-52") "req-5"))
        ($_ (diff:step v-unpos-link-ticks-clock $db "link-inputs" ("req-after-unpos" ("tg:unpos") "c-53") "req-5"))
        ($_ (diff:step v-unpos-capture-ticks-clock $db "capture" ("req-5" "6666666666666666666666666666666666666666666666666666666666666666" "{}" "" $owner "c-54") "req-5"))
        ($_ (diff:step v-unpos-restore $db "restore" ("c-55") "req-5"))

        ;; =====================================================================
        ;; Suite D: 40-step deterministic randomized mixed workload checking
        ;; indexed rows & incremental delta mirror against oracle at EVERY step.
        ;; =====================================================================
        ($_ (diff:run-random $db 0 40 $owner ("req-5" "req-after-unpos" "req-1") none none none none none "req-5"))
        ($_ (diff:check-at post-random-nonzero-offset $db "req-5" 5 40000))
        ;; =====================================================================
        ;; Suite E: Unpositioned serial 0 ordering shift on request-accept clock tick!
        ;; Without resetting on request-accept when $unpos=True, (old_clock+1) falls
        ;; behind the second accepted request's arrival key (old_clock+2).
        ;; =====================================================================
        ($dbE (diff:open-store (path:join $home "store-e")))
        ($_e0 (input-ledger:perform $dbE "owner" ("owner-E" "e-own")))
        ($_e1 (diff:step suiteE-unpos-0 $dbE "task-proposal" (0 "req-e1" "[[\"shell\",\"u0\"]]" "not run" "e-1") "req-e1"))
        ($_e2 (diff:step suiteE-accept-1 $dbE "request-accept" ("req-e1" "First" "" "message" "s1" "d1" "e-2") "req-e1"))
        ($_e3 (diff:step suiteE-accept-2 $dbE "request-accept" ("req-e2" "Second" "" "message" "s2" "d2" "e-3") "req-e1"))
        ($_e4 (diff:step suiteE-link-ticks $dbE "link-inputs" ("req-e2" ("tg:e1" "tg:e2") "e-4") "req-e1"))
        ($_ec (db:close $dbE))

        ($_ (nt:require differential-checks-ran (> (get-state &diff-checks) 200)))
        ($_dc (db:close $db)))
   (println! (UNIT_CONVERSATION_INDEX_DIFFERENTIAL_OK (checks (get-state &diff-checks)))))
