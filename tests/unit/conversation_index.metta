; Differential and adversarial invariants for incremental conversation indexing
; (src/conversation_index_cetta.metta) and client incremental context preparation
; (src/task_context_cetta.metta, src/task_context_views.metta, src/context_compaction.metta).
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/context_compaction.metta)

(= (ci:ready $server $socket $remaining)
   (if (fs:exists $socket) True
     (if (<= $remaining 0) False
       (let $_ (proc:wait $server 20) (ci:ready $server $socket (- $remaining 1))))))

(= (ci:repeat $chunk $n $acc)
   (if (<= $n 0) $acc (ci:repeat $chunk (- $n 1) (str:concat $acc $chunk))))

(= (ci:builds)
   (case (state:ledger "conv-index-stats" ())
     (((conv-index-stats $b $d $m) $b)
      ($other -1))))

(= (ci:run-turn $focus $owner $cmd-tag $result-text)
   (let* (($cap (state:ledger "capture" ($focus "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner (state:request-id))))
          ((captured $ser $crev $cmode $cown) $cap)
          ($cmds (state:encoded (JsonArray ((JsonArray ((JsonString "shell") (JsonString $cmd-tag)))))))
          ($dec-cmd (state:encoded (JsonObject ((JsonPair "commands" (JsonArray ((JsonArray ((JsonString "shell") (JsonString $cmd-tag)))))) (JsonPair "limit" (JsonNumber "1"))))))
          ($att-cmd (state:encoded (JsonArray ((JsonString "shell") (JsonString $cmd-tag)))))
          ($res-json (state:encoded (JsonObject ((JsonPair "disposition" (JsonString "returned")) (JsonPair "result" (JsonString $result-text))))))
          ($prop (state:ledger "task-proposal" ($ser $focus $cmds "not run: batch interrupted" (state:request-id))))
          ($dec (state:ledger "decide" ($ser "command" "batch" $dec-cmd (state:request-id))))
          ((issued $tkt) $dec)
          ($ab (state:ledger "attempt-begin" ($tkt 0 $att-cmd (state:request-id))))
          ($ar (state:ledger "attempt-result" ($tkt 0 $att-cmd $res-json (state:request-id))))
          ($rep (state:ledger "report" ($tkt "(COMMAND_RETURN: ok)" "returned" "unknown" "abcd" 32 (state:request-id)))))
     ($ser $tkt)))

(= (ci:run-rest $focus $owner)
   (let* (($cap (state:ledger "capture" ($focus "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner (state:request-id))))
          ((captured $ser $crev $cmode $cown) $cap)
          ($cmds "[[\"rest\"]]")
          ($dec-cmd "{\"commands\":[[\"rest\"]],\"limit\":1}")
          ($att-cmd "[\"rest\"]")
          ($res-json "{\"disposition\":\"returned\",\"result\":\"(RESTING budget-banked)\"}")
          ($prop (state:ledger "task-proposal" ($ser $focus $cmds "not run: batch interrupted" (state:request-id))))
          ($dec (state:ledger "decide" ($ser "command" "batch" $dec-cmd (state:request-id))))
          ((issued $tkt) $dec)
          ($ab (state:ledger "attempt-begin" ($tkt 0 $att-cmd (state:request-id))))
          ($ar (state:ledger "attempt-result" ($tkt 0 $att-cmd $res-json (state:request-id))))
          ($rep (state:ledger "report" ($tkt "(RESTING budget-banked)" "returned" "unknown" "abcd" 24 (state:request-id)))))
     ($ser $tkt)))

; Compute an uncached reference render using legacy conversation-rows + uncached full-context-view
; (which uses &task-temp-seen and ConvCertNone, never reading or mutating any warm cache).
(= (ci:uncached-prepare $base $focus $trigger $capacity $model $tokens $effort)
   (let* (($assembled (request:append $base (topics:context)))
          ($footer (commentary:output-tail))
          ($ratio (context:ratio $model))
          ($window (str:string->number $capacity))
          ($render-window (max 0 (- $window (math:ceil (/ (str:byte-length $footer) $ratio)))))
          ($render-capacity (repr $render-window))
          ($owner (state:text (state:meta "owner")))
          ($root (str:concat "conversation:" $owner))
          ($start (task:saved-start $root))
          ((conversation-rows $reqs $notes $rows) (state:ledger "conversation-rows" ($focus $start (task:result-chars))))
          ($troot (task:root $focus))
          ($view (JsonObject ((JsonPair "root" (JsonString $root))
                              (JsonPair "task_root" (JsonString $troot))
                              (JsonPair "focus" (JsonString $focus))
                              (JsonPair "trigger" (JsonString $trigger))
                              (JsonPair "offset" (JsonNumber (repr $start)))
                              (JsonPair "requests" (JsonArray (state:objects ("id" "status" "kind" "note" "excerpt") $reqs)))
                              (JsonPair "notes" (JsonArray (state:objects (task:note-columns) $notes)))
                              (JsonPair "transcript" (JsonArray (task:row-pairs $rows $owner)))
                              (JsonPair "archive" JsonNull))))
          ($rendered (task:full-context-view $assembled $view $render-capacity $ratio)))
     (request:render (request:instruct $rendered $footer))))

(= (ci:bump-accepts $n)
   (if (<= $n 0) True
     (let* (($id (str:concat "req-ovf-" (repr $n)))
            ($ok (state:ledger "request-accept" ($id "ovf-body" "" "message" "s" "d" (state:request-id)))))
       (ci:bump-accepts (- $n 1)))))

!(let* (
        ; --- 1. Pure client helpers parity ---
        ($sample-obj (JsonObject ((JsonPair "z" (JsonString "č·…\n\"\\"))
                                  (JsonPair "a" (JsonNumber "42"))
                                  (JsonPair "m" (JsonArray ((JsonBool True) (JsonBool False) JsonNull
                                                            (JsonObject ((JsonPair "b" (JsonNumber "1")) (JsonPair "a" (JsonNumber "2"))))))))))
        ($_ (nt:require state-encoded-native-parity
              (nt:equal (state:encoded $sample-obj) (json-stringify (state:canonical $sample-obj)))))
        ($plain-rest (JsonObject ((JsonPair "id" (JsonString "o:1:0")) (JsonPair "request" (JsonString "r1"))
                                  (JsonPair "command" (JsonArray ((JsonString "rest"))))
                                  (JsonPair "disposition" (JsonString "returned"))
                                  (JsonPair "result" (JsonString "(RESTING budget-banked)")))))
        ($extra-rest (state:json-set $plain-rest "extra" (JsonString "keep")))
        ($non-rest (state:json-set $plain-rest "command" (JsonArray ((JsonString "shell") (JsonString "ls")))))
        ($_ (nt:require plain-rest-fast-guard
              (and (task:plain-rest? $plain-rest)
                   (and (not (task:plain-rest? $extra-rest))
                        (not (task:plain-rest? $non-rest))))))
        ($_ (nt:require task-plain-marker-escaping
              (and (nt:equal (task:plain "hello world") "hello world")
                   (nt:equal (task:plain "a _cache_boundary_ b _cache_chunk_ c")
                             "a _cache-boundary_ b _cache-chunk_ c"))))

        ; --- 2. Spawn real native input_ledger_server ---
        ($home (nt:required-env "HOME"))
        ($root (system:cwd))
        ($socket (path:join $home "l.sock"))
        ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($module (path:join $root "src/input_ledger_server.metta"))
        ($env (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
        ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta" $module "db-server:configuration")
                   ((cwd $root) (env $env) (max-bytes 65536) (lifetime owner))))
        ($_ (nt:require ledger-ready (ci:ready $server $socket 500)))

        ; --- 3. Seed owner, initial requests, notes, and 10 turns (>8 steps to seal chunk 0!) ---
        ($owner "unit-owner")
        ($_own (state:ledger "owner" ($owner (state:request-id))))
        ($_a1 (state:ledger "request-accept" ("input:old" "Original old input body" "operator" "message" "s1" "d1" (state:request-id))))
        ($_l1 (state:ledger "link-inputs" ("input:old" ("tg:1") (state:request-id))))
        ($_b1 (state:ledger "task-begin" ("input:old" "operator-event" "" "chat-1" "" (state:request-id))))
        ($_a2 (state:ledger "request-accept" ("req-main" "Main task request body" "" "message" "s2" "d2" (state:request-id))))
        ($_b2 (state:ledger "task-begin" ("req-main" "operator-event" "" "" "" (state:request-id))))
        ($_f1 (state:ledger "task-focus" ("req-main" (state:request-id))))
        ($_n1 (state:ledger "task-note" ("req-main" "initial note" "[]" (state:request-id))))
        ($base (RoleContext coding-messages-empty "skills" "obs" () () "data" "inst"))

        ; Cold context:prepare (builds server index once: builds == 1)
        ($p-cold (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-cold (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require cold-prepare-exact-parity (nt:equal $p-cold $u-cold)))
        ($_ (nt:require cold-build-count-one (nt:equal (ci:builds) 1)))

        ; Run 8 turns (including repeated >512B result across chunk boundary + consecutive rests!)
        ($big-dup (ci:repeat "REPEATED_LONG_PAYLOAD_0123456789_" 24 ""))
        ($t1 (ci:run-turn "req-main" $owner "step-1" $big-dup))
        ($t2 (ci:run-rest "req-main" $owner))
        ($t3 (ci:run-rest "req-main" $owner))
        ($t4 (ci:run-turn "req-main" $owner "step-4" "unique-4"))
        ($t5 (ci:run-turn "req-main" $owner "step-5" "unique-5"))
        ($t6 (ci:run-turn "req-main" $owner "step-6" "unique-6"))
        ($t7 (ci:run-turn "req-main" $owner "step-7" "unique-7"))
        ; Now 2 inputs + 7 calls = 9 entries (chunk 0 [0..8) seals, step 8 is in recent!)
        ($p-seal (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-seal (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require chunk-seal-exact-parity (nt:equal $p-seal $u-seal)))
        ($_ (nt:require zero-rebuilds-after-seal (nt:equal (ci:builds) 1)))

        ; Append another identical >512B result in recent tail (must back-reference step inside sealed chunk 0!)
        ; PLUS a second repeated >512B result ALSO inside recent tail (tests $local-seen!)
        ($big-recent (ci:repeat "RECENT_ONLY_DUP_PAYLOAD_ABCDEFGH_" 24 ""))
        ($t8 (ci:run-turn "req-main" $owner "step-8" $big-dup))
        ($t9 (ci:run-turn "req-main" $owner "step-9" $big-recent))
        ($t10 (ci:run-turn "req-main" $owner "step-10" $big-recent))
        ($p-dup (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-dup (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($pos-s10 (str:find $p-dup "step-10"))
        ($after-s10 (str:byte-slice $p-dup $pos-s10 (str:byte-length $p-dup)))
        ($_ (nt:require cross-chunk-and-local-seen-parity
              (and (nt:equal $p-dup $u-dup)
                   (and (nt:contains $after-s10 "(same result as step ")
                        (not (nt:contains $after-s10 "RECENT_ONLY_DUP_PAYLOAD_ABCDEFGH_"))))))
        ($_ (nt:require zero-rebuilds-after-dups (nt:equal (ci:builds) 1)))

        ; --- 4. Mid-call concurrent arrival (out-of-order tail bubble-insert!) ---
        ($cap-mid (state:ledger "capture" ("req-main" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner (state:request-id))))
        ((captured $ser-mid $_r1 $_m1 $_o1) $cap-mid)
        ; New request arrives AFTER capture, BEFORE task-proposal!
        ($_a-mid (state:ledger "request-accept" ("req-mid" "Mid-call arriving message" "" "message" "sm" "dm" (state:request-id))))
        ($_p-mid (state:ledger "task-proposal" ($ser-mid "req-main" "[[\"shell\",\"mid-call\"]]" "not run: batch interrupted" (state:request-id))))
        ($dec-mid (state:ledger "decide" ($ser-mid "command" "batch" "{\"commands\":[[\"shell\",\"mid-call\"]],\"limit\":1}" (state:request-id))))
        ((issued $tkt-mid) $dec-mid)
        ($_ab-mid (state:ledger "attempt-begin" ($tkt-mid 0 "[\"shell\",\"mid-call\"]" (state:request-id))))
        ($_ar-mid (state:ledger "attempt-result" ($tkt-mid 0 "[\"shell\",\"mid-call\"]" "{\"disposition\":\"returned\",\"result\":\"mid-result\"}" (state:request-id))))
        ($p-mid (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-mid (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ; Also verify indexed row sequence directly against unindexed reference (limit=0 bypasses index)!
        ((conversation-rows $_ir $_in $idx-rows-mid) (state:ledger "conversation-rows" ("req-main" 0 40000)))
        ((conversation-rows $_rr $_rn $ref-rows-mid) (state:ledger "conversation-rows" ("req-main" 0 0)))
        ($rec-start (str:find $p-mid "TASK_RECENT newest steps"))
        ($rec-slice (str:byte-slice $p-mid $rec-start (str:byte-length $p-mid)))
        ($pos-mid-call (str:find $rec-slice "mid-call"))
        ($pos-mid-msg (str:find $rec-slice "Mid-call arriving message"))
        ($_ (nt:require out-of-order-tail-bubble-parity
              (and (nt:equal $p-mid $u-mid)
                   (and (nt:equal $idx-rows-mid $ref-rows-mid)
                        (< $pos-mid-call $pos-mid-msg)))))
        ($_ (nt:require zero-rebuilds-after-bubble (nt:equal (ci:builds) 1)))

        ; --- 5. Mid-window invalidation via task-begin covering input:old inside sealed chunk 0! ---
        ($_a-cov (state:ledger "request-accept" ("input:cov" "Covering superset batch body" "operator" "message" "scov" "dcov" (state:request-id))))
        ($_l-cov (state:ledger "link-inputs" ("input:cov" ("tg:1" "tg:2") (state:request-id))))
        ($_b-cov (state:ledger "task-begin" ("input:cov" "operator-event" "" "chat-1" "" (state:request-id))))
        ($p-cov (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-cov (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require covered-mid-window-invalidates-prefix
              (and (nt:equal $p-cov $u-cov)
                   (nt:contains $p-cov "[input batch covered by input:cov; original source via request-read input:old]"))))
        ($_ (nt:require zero-rebuilds-after-cover (nt:equal (ci:builds) 1)))

        ; --- 6. Closed focus + task-note + request-disposition + checkpoint settle + request-finish ---
        ($_a-cl (state:ledger "request-accept" ("req-closed" "Closed focus body" "" "message" "sc" "dc" (state:request-id))))
        ($_n-cl (state:ledger "task-note" ("req-closed" "closed-note-body" "[]" (state:request-id))))
        ($_fin (state:ledger "request-finish" ("req-closed" (state:request-id))))
        ($_disp (state:ledger "request-disposition" ("req-mid" "defer" "deferred-note" (state:request-id))))
        ($p-cl (context:prepare $base "req-closed" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-cl (ci:uncached-prepare $base "req-closed" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require closed-focus-and-note-parity
              (and (nt:equal $p-cl $u-cl)
                   (and (nt:contains $p-cl "req-closed")
                        (and (nt:contains $p-cl "closed-note-body")
                             (nt:contains $p-cl "deferred-note"))))))
        ($_chk-settle (state:ledger "checkpoint" ("[]" () "req-mid" 1700000100 (state:request-id))))
        ($p-set (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-set (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require checkpoint-settle-parity (nt:equal $p-set $u-set)))
        ($_ (nt:require zero-rebuilds-after-status-ops (nt:equal (ci:builds) 1)))

        ; --- 7. conversation-history slice check ---
        ($hist (state:ledger "conversation-history" (0 5)))
        ($_ (nt:require history-slice-valid
              (case $hist
                (((history $htot $hrows) (and (> $htot 10) (== (size-atom $hrows) 5)))
                 ($bad False)))))

        ; --- 8. 64-entry rev-log ring overflow fails closed to entry-mode=full ---
        ($d-seed (state:ledger "conversation-delta" ("req-main" 0 40000 none 0 0 0 0 "")))
        ((conversation-delta $g0 $rr0 $nr0 $er0 $tot0 $_rm0 $_nm0 $_em0 $_fr0 $_rq0 $_nt0 $_rw0) $d-seed)
        ($_bumps (ci:bump-accepts 66))
        ($d-ovf (state:ledger "conversation-delta" ("req-main" 0 40000 $g0 $rr0 $nr0 $er0 $tot0 "req-main")))
        ($_ (nt:require rev-log-overflow-fails-closed-to-full
              (case $d-ovf
                (((conversation-delta $g1 $rr1 $nr1 $er1 $tot1 $rm1 $nm1 $em1 $fr1 $rq1 $nt1 $rw1)
                  (and (== $em1 full) (== (size-atom $rw1) $tot1)))
                 ($bad False)))))
        ($_ (nt:require zero-rebuilds-after-66-bumps (nt:equal (ci:builds) 1)))

        ; --- 9. Compaction offset advance evicts old rows and keeps parity ---
        ($wkey (task:window-key (str:concat "conversation:" $owner)))
        ($new-chk (state:encoded (JsonObject ((JsonPair "version" (JsonNumber "1"))
                                              (JsonPair "start" (JsonNumber "8"))
                                              (JsonPair "summary" (JsonString "Compacted first 8 steps."))))))
        ($_wchk (state:ledger "meta-put" ($wkey $new-chk (state:request-id))))
        ($p-off (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-off (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require offset-advance-exact-parity
              (and (nt:equal $p-off $u-off)
                   (nt:contains $p-off "Compacted first 8 steps."))))

        ; --- 10. Fail-closed rebuild on legacy unproposed decide issues NEW gen UUID & forces full delta! ---
        ($d-pre-rst (state:ledger "conversation-delta" ("req-main" 8 40000 none 0 0 0 0 "")))
        ((conversation-delta $g-pre $rr-pre $nr-pre $er-pre $tot-pre $_a $_b $_c $_d $_e $_f $_g) $d-pre-rst)
        ($cap-leg (state:ledger "capture" ("req-main" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" $owner (state:request-id))))
        ((captured $ser-leg $_lr $_lm $_lo) $cap-leg)
        ; Write decide WITHOUT task-proposal -> forces fail-closed cidx:reset!
        ($dec-leg (state:ledger "decide" ($ser-leg "command" "batch" "{\"commands\":[[\"shell\",\"legacy-unproposed\"]],\"limit\":1}" (state:request-id))))
        ((issued $tkt-leg) $dec-leg)
        ($_ab-leg (state:ledger "attempt-begin" ($tkt-leg 0 "[\"shell\",\"legacy-unproposed\"]" (state:request-id))))
        ($_ar-leg (state:ledger "attempt-result" ($tkt-leg 0 "[\"shell\",\"legacy-unproposed\"]" "{\"disposition\":\"returned\",\"result\":\"legacy-ok\"}" (state:request-id))))
        ; Now ask delta with PRE-reset cursor: MUST get new gen UUID and full resync!
        ($d-post-rst (state:ledger "conversation-delta" ("req-main" 8 40000 $g-pre $rr-pre $nr-pre $er-pre $tot-pre "req-main")))
        ($_ (nt:require rebuild-rotates-gen-and-forces-full-delta
              (case $d-post-rst
                (((conversation-delta $g-post $rrp $nrp $erp $totp $rmp $nmp $emp $frp $rqp $ntp $rwp)
                  (and (!= $g-post $g-pre)
                       (and (== $rmp full) (and (== $nmp full) (== $emp full)))))
                 ($bad False)))))
        ($_ (nt:require rebuild-incremented-build-count (nt:equal (ci:builds) 2)))
        ($p-leg (context:prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($u-leg (ci:uncached-prepare $base "req-main" "continuation-tick" "200000" "unit-model" 4096 "default"))
        ($_ (nt:require post-rebuild-exact-parity (nt:equal $p-leg $u-leg)))

        ; Clean shutdown
        ($_c1 (proc:cancel $server))
        ($_c2 (proc:await $server))
        ($_c3 (proc:close $server)))
   (println! UNIT_CONVERSATION_INDEX_OK))
