; Unit tests of src/activity_source_cetta.metta; no Python. The expected
; answers are those of the Python it replaced (the Telegram module's
; acknowledgement, since deleted, and input_ack.committed and
; input_ack.already_committed) for the same batches and configuration.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/activity_source_cetta.metta)

(= (as:handover $modes $ids)
   (let $folder (path:join $modes "channel-handover")
     (let $_ (fs:make-directories $folder 448) (as:write-inputs $folder $ids))))
(= (as:write-inputs $folder $ids)
   (if (== $ids ()) True
     (let $_ (fs:write (path:join $folder (str:concat (car-atom $ids) ".json")) "{}")
       (as:write-inputs $folder (cdr-atom $ids)))))
(= (as:state $modes $id)
   (let $folder (path:join $modes "channel-handover")
     (if (fs:is-file (path:join $folder (str:concat $id ".json"))) pending
       (if (fs:is-file (path:join $folder (str:concat $id ".done"))) done absent))))
(= (as:wait-socket $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (as:wait-socket $server $socket (- $left 1))))))

; The ids: runtime occurrences in order, update ids in order without repeats.
!(let* (($_ (nt:require batch-split
              (nt:equal (activity-source:batch "runtime:job:1,42,42,7,runtime:model-call:a.b-c_d")
                        (ActivityBatchV1 ("runtime:job:1" "runtime:model-call:a.b-c_d") ("42" "7")))))
        ($_ (nt:require empty-batch (nt:equal (activity-source:batch "") (ActivityBatchV1 () ()))))
        ($_ (nt:require largest-update-id
              (nt:equal (activity-source:batch "9223372036854775807") (ActivityBatchV1 () ("9223372036854775807")))))
        ($long (str:concat "runtime:" (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17) $k "abcdefghij"))))
        ($_ (nt:require invalid-ids-refuse-the-batch
              (nt:equal (map-atom ("runtime:" "runtime:bad id" "runtime:é" "042" "-1" "1,,2" "abc" "12a"
                                   "9223372036854775808" "10000000000000000000" "0,runtime:x y")
                                  $text (activity-source:batch $text))
                        (ActivityBatchRefusedV1 ActivityBatchRefusedV1 ActivityBatchRefusedV1 ActivityBatchRefusedV1
                         ActivityBatchRefusedV1 ActivityBatchRefusedV1 ActivityBatchRefusedV1 ActivityBatchRefusedV1
                         ActivityBatchRefusedV1 ActivityBatchRefusedV1 ActivityBatchRefusedV1))))
        ($_ (nt:require runtime-id-length-bound (nt:equal (activity-source:batch $long) ActivityBatchRefusedV1)))
        ($_ (nt:require zero-is-an-update-id (nt:equal (activity-source:batch "0") (ActivityBatchV1 () ("0"))))))
   (println! UNIT_ACTIVITY_SOURCE_IDS_OK))

; Without observed strategy or a ledger: runtime ids are not held back, the
; handover inputs are marked done, and an invalid batch settles nothing.
!(let* (($home (nt:required-env "HOME")) ($modes (path:join $home "modes"))
        ($_ (as:handover $modes ("42" "7" "9")))
        ($plain (ActivitySourceConfigV1 "" False $modes))
        ($_ (nt:require invalid-batch-settles-nothing (nt:equal (activity-source:ack-with $plain "42,x") 0)))
        ($_ (nt:require nothing-moved (nt:equal (as:state $modes "42") pending)))
        ($_ (nt:require batch-settled (nt:equal (activity-source:ack-with $plain "runtime:job:1,42,7,8") 1)))
        ($_ (nt:require inputs-done (nt:equal (map-atom ("42" "7" "8" "9") $id (as:state $modes $id))
                                              (done done absent pending))))
        ($_ (nt:require settled-again (nt:equal (activity-source:ack-with $plain "42") 1)))
        ($observed (ActivitySourceConfigV1 "" True $modes))
        ($_ (nt:require observed-without-ledger-holds-runtime
              (nt:equal (activity-source:ack-with $observed "runtime:job:2,9") 0)))
        ($_ (nt:require held-back-input-pending (nt:equal (as:state $modes "9") pending)))
        ($_ (nt:require observed-handover-only (nt:equal (activity-source:ack-with $observed "9") 1)))
        ($_ (nt:require no-engine-state-no-folder
              (nt:equal (activity-source:ack-with (ActivitySourceConfigV1 "" False "") "5") 1)))
        ; The environment's own configuration: no observed strategy, no ledger, no engine state.
        ($_ (nt:require env-ack (nt:equal (activity-source:ack "runtime:job:3,11") 1)))
        ($_ (nt:require env-committed (nt:equal (activity-source:committed "a,b") 1)))
        ($_ (nt:require env-not-already-committed (nt:equal (activity-source:already-committed "runtime:a") 0)))
        ($_ (nt:require number-id (nt:equal (activity-source:ack 12) 1))))
   (println! UNIT_ACTIVITY_SOURCE_PLAIN_OK))

; With the native input ledger: only its answer settles runtime ids, and
; handover inputs are processed in it.
!(let* (($home (nt:required-env "HOME")) ($root (system:cwd)) ($modes (path:join $home "ledger-modes"))
        ($socket (path:join $home "l.sock")) ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($module (path:join $root "src/input_ledger_server.metta"))
        ($stale (if (fs:exists $socket) (fs:remove $socket) True))
        ($server-env (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
        ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta" $module "db-server:configuration")
                             ((cwd $root) (env $server-env) (max-bytes 65536))))
        ($_ (nt:require ledger-ready (as:wait-socket $server $socket 500)))
        ($_ (as:handover $modes ("42" "43")))
        ($ledger (ActivitySourceConfigV1 $socket True $modes))
        ($_ (nt:require uncommitted-runtime-holds-batch (nt:equal (activity-source:ack-with $ledger "runtime:r1,42") 0)))
        ($_ (nt:require input-still-pending (nt:equal (as:state $modes "42") pending)))
        ($_ (nt:require not-committed (not (activity-source:committed? $ledger ("42")))))
        ($_ (nt:require handover-processed (nt:equal (activity-source:ack-with $ledger "42") 1)))
        ($_ (nt:require processed-is-committed (activity-source:committed? $ledger ("42"))))
        ($now (system:wall-time))
        ($_ (nt:require runtime-committed-in-ledger
              (nt:equal (ledger-client:call $socket "mark-processed" ($now ("runtime:r1"))) True)))
        ($_ (nt:require committed-runtime-settles (nt:equal (activity-source:ack-with $ledger "runtime:r1,43") 1)))
        ($_ (nt:require both-done (nt:equal (map-atom ("42" "43") $id (as:state $modes $id)) (done done))))
        ($_ (nt:require all-committed (activity-source:committed? $ledger ("42" "43" "runtime:r1"))))
        ($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))
        ; A ledger that does not answer settles nothing through it.
        ($_ (as:handover $modes ("44")))
        ($gone (ActivitySourceConfigV1 $socket True $modes))
        ($_ (nt:require dead-ledger-holds-runtime (nt:equal (activity-source:ack-with $gone "runtime:r1,44") 0)))
        ($_ (nt:require dead-ledger-keeps-input (nt:equal (as:state $modes "44") pending)))
        ($_ (nt:require dead-ledger-refuses-processing (nt:equal (activity-source:ack-with $gone "44") 0))))
   (println! UNIT_ACTIVITY_SOURCE_LEDGER_OK))
