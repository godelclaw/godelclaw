; Receipt presentation codec, preview-stub exclusion, and durable observation
; controls over a real native ledger (full, budget, and incremental #5 paths).
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/strategy_broker_cetta.metta)
!(import! &self ../../src/strategy_attempts_cetta.metta)
!(import! &self ../../src/turn_health_cetta.metta)
!(import! &self ../../src/task_context_views.metta)
!(import! &self ../../src/context_compaction.metta)
(= (tg:get $key $default) (if (== $key reply-chat) "unit-chat" $default))
(= (tg:reply-target $chat) (if (== $chat "") "unit-chat" $chat))
(= (ro:ready $server $socket $remaining)
   (if (fs:exists $socket) True
     (if (== $remaining 0) False
       (let $_ (proc:wait $server 20) (ro:ready $server $socket (- $remaining 1))))))
(= (ro:pad $n $body)
   (if (<= $n 0) $body (ro:pad (- $n 1) (str:concat $body $body))))
(= (ro:row $id $disposition $text)
   (JsonObject ((JsonPair "id" (JsonString $id))
                (JsonPair "request" (JsonString "fixture"))
                (JsonPair "disposition" (JsonString $disposition))
                (JsonPair "result" (JsonString $text))
                (JsonPair "command" (JsonArray ((JsonString "fixture-read")))))))
(= (ro:view $rows)
   (JsonObject ((JsonPair "root" (JsonString "conversation:fixture"))
                (JsonPair "focus" (JsonString "fixture"))
                (JsonPair "trigger" (JsonString "operator-event"))
                (JsonPair "offset" (JsonNumber "0"))
                (JsonPair "requests" (JsonArray ()))
                (JsonPair "notes" (JsonArray ()))
                (JsonPair "transcript" (JsonArray $rows)))))
(= (ro:base)
   (RoleContext (request:initial-messages "FIXTURE_POLICY" "FIXTURE_INPUT") "" "" () "" "" ""))
(= (ro:render-budget $rows $budget)
   (let* (($_ (turn:context-presented "[]"))
          ($rendered (task:render-budget-view (ro:base) (ro:view $rows) (JsonObject ()) $budget 2.5))
          ($prompt (request:render $rendered))
          ($ids (turn:get "presented" ())))
     (Rendered $prompt $ids)))
(= (ro:render-full $rows)
   (let* (($_ (turn:context-presented "[]"))
          ($rendered (task:full-context-view (ro:base) (ro:view $rows) "200000" 2.5))
          ($prompt (request:render $rendered))
          ($ids (turn:get "presented" ())))
     (Rendered $prompt $ids)))
(= (ro:action-observed? $ticket)
   (case (state:ledger "actions" ((ticket $ticket)))
     ((((action $t $_ $_ $_ $_ $_ $obs $_ $_)) (== $obs 1))
      ($other False))))

!(let* (;; 1. Codec & call-id decoding: bare Strings from json:parse AND JsonString wrappers.
        ($encoded (json:stringify (JsonArray ((JsonString "synthetic-ticket:0:1")
                                              (JsonString "bad")
                                              (JsonString ":0")
                                              (JsonString "o:x")
                                              (JsonString "o:-1")
                                              (JsonString "o:0.5")
                                              (JsonString "owner:7:12")))))
        ($_ (nt:require context-presented-accepts-json (nt:equal (turn:context-presented $encoded) 1)))
        ($stored (turn:get "presented" ()))
        ($_ (nt:require json-parse-bare-strings-decode
              (nt:equal (turn:presented-pairs $stored) (("synthetic-ticket:0" 1) ("owner:7" 12)))))
        ($_ (nt:require wrapped-json-strings-also-decode
              (nt:equal (turn:presented-pairs ((JsonString "synthetic-ticket:0:1") (JsonNumber "9") JsonNull))
                        (("synthetic-ticket:0" 1)))))
        ($_ (nt:require call-id-rejects-malformed
              (and (== (turn:call-id ":0") none)
                (and (== (turn:call-id "o:0.5") none)
                  (and (== (turn:call-id "o:-1") none)
                       (== (turn:call-id "owner:4:0") ("owner:4" 0)))))))

        ;; 2. Renderer classification: preview stubs & archive entries excluded; empty & raw kept.
        ($long (ro:pad 11 "synthetic-result-piece;"))
        ((Rendered $stub-prompt $stub-ids) (ro:render-budget ((ro:row "ticket-fixture:0" "returned" $long)) 800))
        ($_ (nt:require tight-budget-emits-preview-only
              (and (nt:contains $stub-prompt "result_preview") (not (nt:contains $stub-prompt $long)))))
        ($_ (nt:require preview-stub-not-reported-as-presented (nt:equal $stub-ids ())))
        ((Rendered $empty-prompt $empty-ids) (ro:render-budget ((ro:row "ticket-fixture:1" "returned" "")) 800))
        ($_ (nt:require complete-empty-return-is-presented
              (nt:equal (turn:presented-pairs $empty-ids) (("ticket-fixture" 1)))))
        ((Rendered $_ $held-ids)
         (ro:render-budget ((ro:row "ticket-fixture:2" "not_run" "not executed")
                            (ro:row "ticket-fixture:3" "uncertain" "interrupted")
                            (ro:row "task-input:req-1" "input" "user text")) 4000))
        ($_ (nt:require withheld-and-input-rows-not-presented (nt:equal $held-ids ())))
        ($preview-only-row
          (JsonObject ((JsonPair "id" (JsonString "ticket-fixture:4"))
                       (JsonPair "disposition" (JsonString "returned"))
                       (JsonPair "result_preview" (JsonString "partial")))))
        ($archive-only-row (task:index-entry (ro:row "ticket-fixture:5" "returned" "full text")))
        ($raw-row
          (JsonObject ((JsonPair "id" (JsonString "ticket-fixture:6"))
                       (JsonPair "disposition" (JsonString "recorded"))
                       (JsonPair "result_raw" (JsonString "raw-body")))))
        ($rest-run-row
          (JsonObject ((JsonPair "id" (JsonString "ticket-fixture:7"))
                       (JsonPair "compacted" (JsonString "successful-identical-rest-run"))
                       (JsonPair "disposition" (JsonString "returned"))
                       (JsonPair "result" (JsonString "(RESTING budget-banked)")))))
        ($missing-id-row
          (JsonObject ((JsonPair "disposition" (JsonString "returned"))
                       (JsonPair "result" (JsonString "nameless")))))
        ($_ (nt:require carried-id-excludes-stubs-and-archives-only
              (nt:equal (task:carried-ids ($preview-only-row $archive-only-row $raw-row $rest-run-row $missing-id-row))
                        ((JsonString "ticket-fixture:6") (JsonString "ticket-fixture:7")))))
        ((MeasuredTailV1 $_ $_ $_ $measured-car)
         (task:shown-and-measure ($preview-only-row $archive-only-row (ro:row "ticket-fixture:8" "returned" "ok")) 2.5 0 True () ()))
        ($_ (nt:require incremental-shown-and-measure-excludes-stubs
              (nt:equal $measured-car ((JsonString "ticket-fixture:8")))))

        ;; 3. Real native ledger controls (preview stub, rejection, digest mismatch, full match).
        ($home (nt:required-env "HOME")) ($root (system:cwd))
        ($socket (nt:required-env "METTACLAW_INPUT_LEDGER_SOCKET")) ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($module (path:join $root "src/input_ledger_server.metta"))
        ($environment (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
        ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta" $module "db-server:configuration")
                             ((cwd $root) (env $environment) (max-bytes 65536) (lifetime owner))))
        ($_ (nt:require ledger-ready (ro:ready $server $socket 500)))
        ($_ (lifecycle-native:start))

        ;; Turn 1: create and report a 1-attempt action with a long body.
        ($_ (nt:require turn1-begin (nt:equal (turn:begin 1 "operator-event" "verify receipt observation" "[\"tg:ro-1\"]") 1)))
        ($_ (turn:context-presented "[]"))
        ($_ (turn:context-prepared (str:sha256 "turn1 prompt") 0))
        ($_ (nt:require turn1-capture (nt:equal (turn:capture "turn1 prompt" 0) 1)))
        ($t1-num (turn:broker-turn 1))
        (($t1-ok $ticket1) (turn:broker-begin $t1-num (quote ((nop))) 1))
        ($_ (nt:require turn1-admitted (and (nt:equal $t1-ok 1) (!= $ticket1 ""))))
        ($_ (strategy:open-batch $t1-num $ticket1))
        ($_ (nt:require turn1-reserved (nt:equal (strategy:reserve $t1-num (quote (nop))) 1)))
        ($_ (nt:require turn1-result (nt:equal (turn:command-result $t1-num returned (nop) $long 0) 1)))
        ($_ (turn:broker-end $t1-num (quote ((COMMAND_RETURN: ((nop) $long))))))
        ($_ (strategy:close-batch))
        ($call1 (str:concat $ticket1 ":0"))
        ($row1 (ro:row $call1 "returned" $long))
        ($probe1 (($ticket1 0)))
        ($_ (nt:require ticket1-starts-unobserved
              (and (== (size-atom (state:ledger "presented-receipts" ($probe1))) 1)
                   (not (ro:action-observed? $ticket1)))))

        ;; Turn 2: digest-matched admitted answer to a budget preview-only stub stays unobserved.
        ($_ (nt:require turn2-begin (nt:equal (turn:begin 2 "continuation-tick" "" "[]") 1)))
        ((Rendered $stub-p2 $stub-ids2) (ro:render-budget ($row1) 800))
        ($_ (nt:require turn2-is-preview-stub
              (and (nt:contains $stub-p2 "result_preview") (and (not (nt:contains $stub-p2 $long)) (== $stub-ids2 ())))))
        ($_ (turn:context-prepared (str:sha256 $stub-p2) 0))
        ($_ (nt:require turn2-capture (nt:equal (turn:capture $stub-p2 0) 1)))
        (($t2-ok $_) (turn:broker-begin (turn:broker-turn 2) (quote ()) 0))
        ($_ (nt:require turn2-admitted (== $t2-ok 1)))
        ($_ (nt:require preview-stub-answer-stays-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe1))) 1)))

        ;; Turn 3: provider failure / rejection with full body stays unobserved and clears state.
        ($_ (nt:require turn3-begin (nt:equal (turn:begin 3 "continuation-tick" "" "[]") 1)))
        ((Rendered $full-p3 $full-ids3) (ro:render-budget ($row1) 40000))
        ($_ (nt:require turn3-carries-full-body
              (and (nt:contains $full-p3 $long)
                   (nt:equal (turn:presented-pairs $full-ids3) (($ticket1 0))))))
        ($_ (turn:context-prepared (str:sha256 $full-p3) 0))
        ($_ (nt:require turn3-capture (nt:equal (turn:capture $full-p3 0) 1)))
        ($_ (nt:require turn3-provider-failed (nt:equal (turn:provider-failed) 1)))
        ($_ (nt:require rejection-clears-answered-presented (nt:equal (turn:get "answered-presented" ()) ())))
        ($_ (nt:require rejection-stays-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe1))) 1)))

        ;; Turn 4: digest mismatch with full body stays unobserved even when admitted.
        ($_ (nt:require turn4-begin (nt:equal (turn:begin 4 "continuation-tick" "" "[]") 1)))
        ((Rendered $full-p4 $full-ids4) (ro:render-budget ($row1) 40000))
        ($_ (nt:require turn4-carries-id (nt:equal (turn:presented-pairs $full-ids4) (($ticket1 0)))))
        ($_ (turn:context-prepared (str:sha256 $full-p4) 0))
        ($_ (nt:require turn4-capture-edited (nt:equal (turn:capture (str:concat $full-p4 " edited") 0) 1)))
        (($t4-ok $_) (turn:broker-begin (turn:broker-turn 4) (quote ()) 0))
        ($_ (nt:require turn4-admitted (== $t4-ok 1)))
        ($_ (nt:require digest-mismatch-stays-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe1))) 1)))

        ;; Turn 5: later matching answer carrying the real full body marks receipt observed.
        ($_ (nt:require turn5-begin (nt:equal (turn:begin 5 "continuation-tick" "" "[]") 1)))
        ((Rendered $full-p5 $full-ids5) (ro:render-budget ($row1) 40000))
        ($_ (nt:require turn5-carries-full-body (nt:contains $full-p5 $long)))
        ($_ (turn:context-prepared (str:sha256 $full-p5) 0))
        ($_ (nt:require turn5-capture (nt:equal (turn:capture $full-p5 0) 1)))
        ($_ (nt:require still-unobserved-before-answer
              (== (size-atom (state:ledger "presented-receipts" ($probe1))) 1)))
        (($t5-ok $_) (turn:broker-begin (turn:broker-turn 5) (quote ()) 0))
        ($_ (nt:require turn5-admitted (== $t5-ok 1)))
        ($_ (nt:require matching-full-answer-marks-observed
              (and (nt:equal (state:ledger "presented-receipts" ($probe1)) ())
                   (ro:action-observed? $ticket1))))

        ;; 4. Two-attempt action: all-or-none observation + full-mode equal-result in-prompt link.
        ($_ (nt:require turn6-begin (nt:equal (turn:begin 6 "continuation-tick" "" "[]") 1)))
        ($_ (turn:context-presented "[]"))
        ($_ (turn:context-prepared (str:sha256 "turn6 prompt") 0))
        ($_ (nt:require turn6-capture (nt:equal (turn:capture "turn6 prompt" 0) 1)))
        ($t6-num (turn:broker-turn 6))
        (($t6-ok $ticket2) (turn:broker-begin $t6-num (quote ((nop) (nop))) 2))
        ($_ (nt:require turn6-admitted (and (nt:equal $t6-ok 1) (!= $ticket2 ""))))
        ($_ (strategy:open-batch $t6-num $ticket2))
        ($shared (str:concat "TWO_POSITION_SHARED_BODY" (ro:pad 7 "synthetic-result-piece;")))
        ($_ (nt:require t6-res0 (nt:equal (strategy:reserve $t6-num (quote (nop))) 1)))
        ($_ (nt:require t6-out0 (nt:equal (turn:command-result $t6-num returned (nop) $shared 0) 1)))
        ($_ (nt:require t6-res1 (nt:equal (strategy:reserve $t6-num (quote (nop))) 1)))
        ($_ (nt:require t6-out1 (nt:equal (turn:command-result $t6-num returned (nop) $shared 1) 1)))
        ($_ (turn:broker-end $t6-num (quote ((COMMAND_RETURN: ((nop) $shared)) (COMMAND_RETURN: ((nop) $shared))))))
        ($_ (strategy:close-batch))
        ($id20 (str:concat $ticket2 ":0")) ($id21 (str:concat $ticket2 ":1"))
        ($r20 (ro:row $id20 "returned" $shared)) ($r21 (ro:row $id21 "returned" $shared))
        ($probe2 (($ticket2 0) ($ticket2 1)))
        ($_ (nt:require two-pos-starts-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe2))) 1)))

        ;; Turn 7: partial (only position 0 carried) stays unobserved.
        ($_ (nt:require turn7-begin (nt:equal (turn:begin 7 "continuation-tick" "" "[]") 1)))
        ((Rendered $part-p7 $part-ids7) (ro:render-full ($r20)))
        ($_ (nt:require partial-carries-only-pos0
              (and (nt:contains $part-p7 $shared)
                   (nt:equal (turn:presented-pairs $part-ids7) (($ticket2 0))))))
        ($_ (turn:context-prepared (str:sha256 $part-p7) 0))
        ($_ (nt:require turn7-capture (nt:equal (turn:capture $part-p7 0) 1)))
        (($t7-ok $_) (turn:broker-begin (turn:broker-turn 7) (quote ()) 0))
        ($_ (nt:require turn7-admitted (== $t7-ok 1)))
        ($_ (nt:require partial-batch-stays-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe2))) 1)))

        ;; Turn 8: both positions linked to one complete body in full mode marks batch observed.
        ($_ (nt:require turn8-begin (nt:equal (turn:begin 8 "continuation-tick" "" "[]") 1)))
        ((Rendered $link-p8 $link-ids8) (ro:render-full ($r20 $r21)))
        ($_ (nt:require shared-body-emitted-once (== (size-atom (str:split $shared $link-p8)) 2)))
        ($_ (nt:require shared-body-has-in-prompt-link (nt:contains $link-p8 "same result as step 0")))
        ($_ (nt:require both-linked-ids-presented
              (nt:equal (turn:presented-pairs $link-ids8) (($ticket2 0) ($ticket2 1)))))
        ($_ (turn:context-prepared (str:sha256 $link-p8) 0))
        ($_ (nt:require turn8-capture (nt:equal (turn:capture $link-p8 0) 1)))
        (($t8-ok $_) (turn:broker-begin (turn:broker-turn 8) (quote ()) 0))
        ($_ (nt:require turn8-admitted (== $t8-ok 1)))
        ($_ (nt:require linked-two-pos-marks-observed
              (and (nt:equal (state:ledger "presented-receipts" ($probe2)) ())
                   (ro:action-observed? $ticket2))))

        ;; 5. End-to-end production incremental context:prepare (#5 path) observes a real receipt.
        ($_ (nt:require turn9-begin (nt:equal (turn:begin 9 "continuation-tick" "" "[]") 1)))
        ($_ (turn:context-presented "[]"))
        ($_ (turn:context-prepared (str:sha256 "turn9 prompt") 0))
        ($_ (nt:require turn9-capture (nt:equal (turn:capture "turn9 prompt" 0) 1)))
        ($t9-num (turn:broker-turn 9))
        (($t9-ok $ticket3) (turn:broker-begin $t9-num (quote ((nop))) 1))
        ($_ (nt:require turn9-admitted (and (nt:equal $t9-ok 1) (!= $ticket3 ""))))
        ($_ (strategy:open-batch $t9-num $ticket3))
        ($_ (nt:require t9-res0 (nt:equal (strategy:reserve $t9-num (quote (nop))) 1)))
        ($_ (nt:require t9-out0 (nt:equal (turn:command-result $t9-num returned (nop) "INCREMENTAL_OBS_BODY" 0) 1)))
        ($_ (turn:broker-end $t9-num (quote ((COMMAND_RETURN: ((nop) "INCREMENTAL_OBS_BODY"))))))
        ($_ (strategy:close-batch))
        ($probe3 (($ticket3 0)))
        ($_ (nt:require ticket3-starts-unobserved
              (== (size-atom (state:ledger "presented-receipts" ($probe3))) 1)))

        ($_ (nt:require turn10-begin (nt:equal (turn:begin 10 "continuation-tick" "" "[]") 1)))
        ($_ (task:render-caches-reset))
        ($prompt10 (context:prepare (ro:base) (turn:focus) "continuation-tick" "200000" "gpt-5.4" 0 ""))
        ($_ (nt:require incremental-prompt-carries-ticket3 (nt:contains $prompt10 "INCREMENTAL_OBS_BODY")))
        ($_ (nt:require turn10-capture (nt:equal (turn:capture $prompt10 0) 1)))
        (($t10-ok $_) (turn:broker-begin (turn:broker-turn 10) (quote ()) 0))
        ($_ (nt:require turn10-admitted (== $t10-ok 1)))
        ($_ (nt:require incremental-context-prepare-marks-observed
              (and (nt:equal (state:ledger "presented-receipts" ($probe3)) ())
                   (ro:action-observed? $ticket3))))

        ($_ (proc:cancel $server)) ($_ (proc:wait $server 5000)))
   (println! UNIT_RECEIPT_OBSERVATION_OK))
