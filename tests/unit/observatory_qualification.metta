; Qualification checks covering the four open observatory audit cases:
; 1) Unrestricted O(1)-memory framing for multi-megabyte receipts and journal lines
; 2) Power-loss safety: newline-aligned multi-chunk append and orphan stage sweep
; 3) Interleaved reader visibility: shared-lock snapshot isolation & fail-closed report
; 4) Native report formatting, window filtering, and schema parity
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/observatory_cetta.metta)

(= (oq:repeat $text $times $acc)
   (if (<= $times 0) $acc
     (if (== (% $times 2) 0)
       (oq:repeat (str:concat $text $text) (/ $times 2) $acc)
       (oq:repeat (str:concat $text $text) (math:floor (/ $times 2)) (str:concat $acc $text)))))

(= (oq:pad $n $unit)
   (let* (($ulen (str:byte-length $unit))
          ($need (+ (math:floor (/ $n $ulen)) 1))
          ($big (oq:repeat $unit $need "")))
     (str:byte-slice $big 0 $n)))

(= (oq:dir $tag)
   (let* (($d (path:join (nt:required-env "HOME") $tag))
          ($mk (fs:make-directories $d 448)))
     $d))

(= (oq:reset-state $dir $fast-limit)
   (let* (($o1 (change-state! &obs-options (JsonObject ((JsonPair "--data" $dir) (JsonPair "--state" $dir)))))
          ($c1 (change-state! &obs-cursor (JsonObject ())))
          ($c2 (change-state! &obs-counts (JsonObject ())))
          ($w1 (change-state! &obs-write-failed False))
          ($b1 (change-state! &op-batch JsonNull))
          ($f1 (change-state! &obs-fast-frame-bytes $fast-limit)))
     True))

(= (oq:journal-stream-case $tag $bytes $fast-limit)
   (let* (($dir (oq:dir $tag))
          ($r0 (oq:reset-state $dir $fast-limit))
          ($cur (JsonObject ((JsonPair "journal_cursor" "before"))))
          ($sc (change-state! &obs-cursor $cur))
          ($cf (sf:replace (obs:path "cursor.json") (json:stringify-python $cur) 384 True))
          ($rec (op:recover $dir))
          ($beg (op:begin $dir))
          ($in-path (path:join $dir "stream"))
          ($wr (fs:write $in-path $bytes))
          ($entry (path:join (nt:required-env "TEST_REPOSITORY") "tests/fixtures/journal_stream_native.metta"))
          ($proc (proc:spawn ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                    "--import-mode" "ancestor-walk" $entry $in-path)
                   ((mode stream) (max-bytes 65536) (timeout-ms 20000) (lifetime owner))))
          ($res (obs:journal-stream-owned $proc "before")))
     ($dir $res)))

(= (oq:test-large-receipts)
   (let* (($dir (oq:dir "large-receipts"))
          ($r0 (oq:reset-state $dir 256))
          ($path (path:join $dir "effect-receipts.jsonl"))
          ; ~3.1 MiB receipt with escaped JSON quotes/newlines and huge MeTTa command
          ($big-res (oq:pad 3000000 "chunk \\\"escaped\\\" \\n payload "))
          ($big-cmd (str:join "" ("(shell \"echo " (oq:pad 150000 "a") "\")")))
          ($r1 (str:join "" ("{\"recorded_at\":100.25,\"turn\":1,\"command\":" (json:stringify-python $big-cmd)
                             ",\"disposition\":\"returned\",\"reason\":\"ok\",\"result\":\"" $big-res "\"}\n")))
          ($r2 (str:join "" ("{\"recorded_at\":101.5,\"turn\":2,\"command\":\"['send', '" (oq:pad 70000 "b") "']\""
                             ",\"disposition\":\"returned\",\"reason\":\"ok\"}\n")))
          ($r3 (str:join "" ("{\"recorded_at\":102.0,\"turn\":3,\"commands\":[\"(nop)\",\"(rest)\"]"
                             ",\"disposition\":\"withheld\",\"reason\":\"policy\",\"result\":\"" (oq:pad 70000 "c") "\"}\n")))
          ($r4-part (str:join "" ("{\"recorded_at\":103.0,\"turn\":4,\"command\":[\"rest\",\"" (oq:pad 80000 "d") "\"]"
                                  ",\"disposition\":\"returned\",\"reason\":\"ok\"}")))
          ($expected-partial-off (+ (str:byte-length $r1) (+ (str:byte-length $r2) (str:byte-length $r3))))
          ($w0 (fs:write $path (str:join "" ($r1 $r2 $r3 $r4-part))))
          ($res1 (obs:receipts))
          ($t1 (nt:require large-receipts-first-pass-ok (== $res1 True)))
          ($t2 (nt:require large-receipts-emitted-three-complete (== (sf:get (get-state &obs-counts) "effects.jsonl" 0) 3)))
          ($t3 (nt:require large-incomplete-receipt-retains-exact-start-offset
                 (== (sf:get (get-state &obs-cursor) "receipts_offset" -1) $expected-partial-off)))
          ($w1 (fs:append-text $path "\n"))
          ($res2 (obs:receipts))
          ($cl (obs:close-writers))
          ($out (fs:read-text (path:join $dir "effects.jsonl")))
          ($t4 (nt:require large-receipts-second-pass-completes-fourth
                 (and (== $res2 True) (== (sf:get (get-state &obs-counts) "effects.jsonl" 0) 4))))
          ($t5 (nt:require large-receipts-offset-reaches-eof
                 (== (sf:get (get-state &obs-cursor) "receipts_offset" -1) (fs:size $path))))
          ($t6 (nt:require large-receipts-extracts-metta-shell (nt:contains $out "\"turn\":1,\"kind\":\"shell\"")))
          ($t7 (nt:require large-receipts-extracts-legacy-send (nt:contains $out "\"turn\":2,\"kind\":\"send\"")))
          ($t8 (nt:require large-receipts-extracts-batch (nt:contains $out "\"turn\":3,\"kind\":\"batch\"")))
          ($t9 (nt:require large-receipts-extracts-array-rest (nt:contains $out "\"turn\":4,\"kind\":\"rest\"")))
          ($t10 (nt:require large-receipts-drops-payload-body (not (nt:contains $out "escaped")))))
     True))

(= (oq:test-large-journal)
   (let* (
          ; Direct deterministic chunk-boundary split across [synthetic_llm] usage
          ($s0 (oscan:new))
          ($s1 (oscan:feed $s0 "{\"__CURSOR\":\"c-split\",\"__REALTIME_TIMESTAMP\":\"1795155000000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\"prefix [synthetic_llm] anthropic us"))
          ($s2 (oscan:feed $s1 "age: in=77 cache_read=33 cache_write=11 out=22 suffix\"}"))
          ($split-ok (case (oscan:finish $s2)
                       (((BigJsonOk $obj)
                         (case (sf:get $obj "__OBS_MSG_HITS__" JsonNull)
                           (((MsgHits $_s (UsageHit "anthropic" "77" "33" "11" "22") $_tr $_f $_r) True)
                            ($no-hit False))))
                        ($bad-split False))))
          ($t0 (nt:require scanner-splits-usage-across-chunk-boundary $split-ok))
          ; Place [synthetic_llm] usage right across the 65536-byte chunk boundary!
          ($pad-boundary (oq:pad 65420 "x"))
          ($j1 (str:join "" ("{\"__CURSOR\":\"cur-1\",\"__REALTIME_TIMESTAMP\":\"1795155001000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\""
                             $pad-boundary "[synthetic_llm] anthropic usage: in=120 cache_read=80 cache_write=10 out=40"
                             (oq:pad 70000 "y") "\"}")))
          ($j2 (str:join "" ("{\"__CURSOR\":\"cur-2\",\"__REALTIME_TIMESTAMP\":\"1795155002000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\"(RESULTS: "
                             (oq:pad 80000 "z") ")\"}")))
          ($j3 (str:join "" ("{\"__CURSOR\":\"cur-3\",\"__REALTIME_TIMESTAMP\":\"1795155003000000\",\"_COMM\":\"systemd\",\"SYSLOG_IDENTIFIER\":\"systemd\",\"MESSAGE\":\"Started Lila "
                             (oq:pad 70000 "w") "\"}")))
          ($j4 (str:join "" ("{\"__CURSOR\":\"cur-4\",\"__REALTIME_TIMESTAMP\":\"1795155004000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\""
                             (oq:pad 70000 "q") " engine 'cetta' failed (boom (42)) tail\"}")))
          ($j5 (str:join "" ("{\"__CURSOR\":\"cur-5\",\"__REALTIME_TIMESTAMP\":\"1795155005000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\""
                             (oq:pad 70000 "p") " [synthetic_llm] transient retry\"}")))
          ; Note: NO trailing newline after $j5 (valid large final record!)
          ($stream-ok (str:join "\n" ($j1 $j2 $j3 $j4 $j5)))
          (($dir-ok $res-ok) (oq:journal-stream-case "large-journal-ok" $stream-ok 256))
          ($fin-ok (obs:finish-collection True True $res-ok))
          ($llm-txt (fs:read-text (path:join $dir-ok "llm.jsonl")))
          ($turns-txt (fs:read-text (path:join $dir-ok "turns.jsonl")))
          ($life-txt (fs:read-text (path:join $dir-ok "lifecycle.jsonl")))
          ($t1 (nt:require large-journal-succeeds-and-publishes (and (== $res-ok True) (== $fin-ok 0))))
          ($t2 (nt:require large-journal-usage-across-64k-boundary
                 (nt:contains $llm-txt "\"provider\":\"anthropic\",\"in\":120,\"cache_read\":80,\"cache_write\":10,\"out\":40")))
          ($t3 (nt:require large-journal-transient-final-no-newline
                 (nt:contains $llm-txt "\"transient\":true")))
          ($t4 (nt:require large-journal-turn-projected (nt:contains $turns-txt "\"engine\":\"cetta\"")))
          ($t5 (nt:require large-journal-systemd-and-engine-fail
                 (and (nt:contains $life-txt "Started Lila")
                      (nt:contains $life-txt "\"event\":\"engine_failed\",\"engine\":\"cetta\",\"why\":\"boom (42)\""))))
          ($t6 (nt:require large-journal-advances-cursor-to-cur5
                 (== (sf:get (sf:native-object (path:join $dir-ok "cursor.json")) "journal_cursor" "") "cur-5")))
          ; Truncated large final journal record MUST fail explicitly and preserve cursor
          ($j-trunc (str:join "" ("{\"__CURSOR\":\"cur-bad\",\"__REALTIME_TIMESTAMP\":\"1795155006000000\",\"_COMM\":\"cetta\",\"MESSAGE\":\""
                                  (oq:pad 80000 "k"))))
          (($dir-bad $res-bad) (oq:journal-stream-case "large-journal-trunc" (str:join "\n" ($j2 $j-trunc)) 256))
          ($fin-bad (obs:finish-collection True True $res-bad))
          ($t7 (nt:require large-truncated-journal-rejected
                 (case $res-bad (((ObservatoryFailureV1 $_) True) ($other False)))))
          ($t8 (nt:require large-truncated-journal-refuses-publication (== $fin-bad 1)))
          ($t9 (nt:require large-truncated-journal-preserves-cursor
                 (== (sf:get (sf:native-object (path:join $dir-bad "cursor.json")) "journal_cursor" "") "before"))))
     True))

(= (oq:test-power-loss)
   (let* (
          ; 1) Newline alignment in op:aligned-bytes (never splits an ordinary JSONL line across 64 KiB write)
          ($row (str:concat "{\"t\":1,\"turn\":1,\"kind\":\"shell\",\"disposition\":\"returned\",\"reason\":\"ok\"}" "\n"))
          ($chunk (str:concat (oq:pad 65490 $row) "{\"t\":2,\"partial\":"))
          ($aligned (op:aligned-bytes $chunk (str:byte-length $chunk) 0 131072))
          ($t1 (nt:require aligned-chunk-ends-with-newline (str:ends-with $aligned "\n")))
          ($t2 (nt:require aligned-chunk-drops-trailing-partial (not (nt:contains $aligned "\"partial\":"))))
          ($final-piece (op:aligned-bytes $chunk (str:byte-length $chunk) 0 (str:byte-length $chunk)))
          ($t3 (nt:require final-chunk-keeps-all-bytes (== (str:byte-length $final-piece) (str:byte-length $chunk))))
          ; 2) End-to-end >130 KiB multi-chunk publish under newline-aligned op:copy
          ($dir (oq:dir "power-loss"))
          ($r0 (oq:reset-state $dir 262144))
          ($c0 (sf:replace (path:join $dir "cursor.json") "{\"journal_cursor\":\"p0\"}\n" 384 True))
          ($rec0 (op:recover $dir))
          ($beg0 (op:begin $dir))
          ($big-stage (oq:repeat $row 1800 ""))
          ($stage-path (op:writer-path $dir "effects.jsonl"))
          ($ws (fs:write $stage-path $big-stage))
          ($sc (obs:set-cursor "journal_cursor" "p1"))
          ($fin (obs:finish-collection True True True))
          ($published (fs:read-text (path:join $dir "effects.jsonl")))
          ($t4 (nt:require multichunk-publish-succeeds (== $fin 0)))
          ($t5 (nt:require multichunk-publish-exact-bytes (== $published $big-stage)))
          ; 3) Orphan stage sweep after post-receipt-removal power loss
          ($inert1 (path:join $dir ".observation-0123456789abcdef0123456789abcdef-turns.jsonl.stage"))
          ($inert2 (path:join $dir ".observation-fedcba9876543210fedcba9876543210-effects.jsonl.stage"))
          ($keep (path:join $dir "keep.txt"))
          ($w1 (fs:write $inert1 "{}\n"))
          ($w2 (fs:write $inert2 "{}\n"))
          ($w3 (fs:write $keep "untouched"))
          ($t6 (nt:require quiescent-when-no-receipt (op:quiescent? $dir)))
          ($rec1 (op:recover $dir))
          ($t7 (nt:require recover-sweeps-inert-stages
                 (and (== $rec1 True) (and (not (fs:exists $inert1)) (not (fs:exists $inert2))))))
          ($t8 (nt:require recover-preserves-non-stage-files (== (fs:read-text $keep) "untouched")))
          ; 4) Active receipt protects stage files from sweep-inert
          ($beg1 (op:begin $dir))
          ($active-stage (op:writer-path $dir "turns.jsonl"))
          ($w4 (fs:write $active-stage "{\"t\":1}\n"))
          ($t9 (nt:require non-quiescent-with-active-receipt (not (op:quiescent? $dir))))
          ($sw (op:sweep-inert $dir))
          ($t10 (nt:require sweep-inert-refuses-while-receipt-exists (fs:exists $active-stage)))
          ($rec2 (op:recover $dir)))
     True))

(= (oq:obs-entry) (path:join (nt:required-env "TEST_REPOSITORY") "scripts/observatory_cetta.metta"))
(= (oq:pub-entry) (path:join (nt:required-env "TEST_REPOSITORY") "tests/fixtures/observatory_concurrent_pub.metta"))

(= (oq:run-report-cli $dir $days $now $extra)
   (let $base ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
               "--import-mode" "ancestor-walk" (oq:obs-entry) "report"
               "--data" $dir "--days" (repr $days) "--now" (repr $now))
     (proc:run (list:append $base $extra) (system:getcwd) inherit 15000 1048576)))

(= (oq:check-snapshot-invariant $text)
   (let* (($lines (filter-atom (str:split "\n" $text) $l (!= (str:trim $l) "")))
          ($day-rows (filter-atom $lines $l (str:starts-with $l "2026-")))
          ($sample-rows (filter-atom $lines $l (str:starts-with $l "samples: "))))
     (if (== $day-rows ())
       (== $sample-rows ())
       (let* (($parts (str:split-whitespace (car-atom $day-rows)))
              ($turns (cli:number (obs:nth $parts 1 "-1") -1))
              ($effects (cli:number (obs:nth $parts 8 "-2") -2))
              ($sends (cli:number (obs:nth $parts 11 "-3") -3))
              ($sparts (if (== $sample-rows ()) () (str:split-whitespace (car-atom $sample-rows))))
              ($samples (cli:number (obs:nth $sparts 1 "-4") -4)))
         (and (== $turns $effects) (and (== $turns $sends) (== $turns $samples)))))))

(= (oq:poll-readers $dir $now $proc $count)
   (let* (($res (oq:run-report-cli $dir 30 $now ()))
          ($ok (case $res
                 (((proc:exited 0 $out $_err) (oq:check-snapshot-invariant $out))
                  ($bad False))))
          ($chk (nt:require interleaved-report-sees-consistent-snapshot $ok)))
     (case (proc:status $proc)
       ((proc:ready (+ $count 1))
        (proc:pending (if (>= $count 40) (+ $count 1) (oq:poll-readers $dir $now $proc (+ $count 1))))
        ($other (+ $count 1))))))

(= (oq:wait-started $path $left)
   (if (or (fs:exists $path) (<= $left 0)) True
     (progn (host:wait () 10) (oq:wait-started $path (- $left 1)))))

(= (oq:drain-stdout $proc $acc)
   (case (proc:read $proc stdout 200)
     (((proc:chunk $bytes) (oq:drain-stdout $proc (str:concat $acc $bytes)))
      (proc:pending (oq:drain-stdout $proc $acc))
      ($done $acc))))

(= (oq:test-reader-visibility-and-report)
   (let* (($dir (oq:dir "reader-vis"))
          ($now 1795155200)
          ; 1) Fail-closed when unrecovered .observation-publication.json exists
          ($r0 (oq:reset-state $dir 262144))
          ($c0 (sf:replace (path:join $dir "cursor.json") "{\"journal_cursor\":\"r0\"}\n" 384 True))
          ($beg (op:begin $dir))
          ($a1 (obs:append "turns.jsonl" (JsonObject ((JsonPair "t" 1795155000.0) (JsonPair "engine" "cetta")))))
          ($cl1 (obs:close-writers))
          ($prep (op:prepare $dir (JsonObject ((JsonPair "journal_cursor" "r1")))))
          ($res-fail (oq:run-report-cli $dir 30 $now ()))
          ($t1 (nt:require report-fails-closed-on-unrecovered-publication
                 (case $res-fail (((proc:exited 1 $_out1 $err1) (nt:contains $err1 "incomplete")) ($fail-other1 False)))))
          ($res-be (oq:run-report-cli $dir 30 $now ("--best-effort")))
          ($t2 (nt:require report-best-effort-succeeds-while-unrecovered
                 (case $res-be (((proc:exited 0 $out2 $_err2) (nt:contains $out2 "turns_cetta")) ($fail-other2 False)))))
          ($rec (op:recover $dir))
          ($res-rec (oq:run-report-cli $dir 30 $now ()))
          ($t3 (nt:require report-succeeds-after-recovery
                 (case $res-rec (((proc:exited 0 $out3 $_err3) (nt:contains $out3 "2026-")) ($fail-other3 False)))))
          ; 2) Concurrent publisher + interleaved readers under .collector.lock
          ($cdir (oq:dir "concurrent-vis"))
          ($cinit (sf:replace (path:join $cdir "cursor.json") "{\"journal_cursor\":\"batch-0\"}\n" 384 True))
          ($pub (proc:spawn ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                  "--import-mode" "ancestor-walk" (oq:pub-entry) $cdir "1795155000" "6")
                  ((mode stream) (max-bytes 65536) (timeout-ms 30000) (lifetime owner))))
          ($ci (proc:close-input $pub))
          ($ws (oq:wait-started (path:join $cdir "publisher-started") 200))
          ($reads (oq:poll-readers $cdir $now $pub 0))
          ($ended (proc:await $pub))
          ($cp (proc:close $pub))
          ($t4 (nt:require concurrent-publisher-exited-cleanly
                 (case $ended (((proc:result (proc:exited 0) $_1 $_2 $_3 $_4) True) ($fail-other4 False)))))
          ($t5 (nt:require interleaved-readers-ran-multiple-times (>= $reads 1)))
          ($res-final (oq:run-report-cli $cdir 30 $now ()))
          ($t6 (nt:require final-report-sees-all-six-batches
                 (case $res-final
                   (((proc:exited 0 $out4 $_err4)
                     (and (oq:check-snapshot-invariant $out4) (nt:contains $out4 "samples: 6 ")))
                    ($fail-other5 False)))))
          ; 2b) Deterministic shared-lock barrier proof: while collector holds
          ; exclusive .collector.lock mid-publish (turns written, effects/samples
          ; not yet written), report MUST block and ONLY observe the complete
          ; post-release snapshot (turns == effects == samples == 1).
          ($ldir (oq:dir "lock-barrier"))
          ($lk (fs:open-write (path:join $ldir ".collector.lock") ("create" "append") 384))
          ($held (fs:lock $lk exclusive))
          ($t-lk (nt:require barrier-acquired-exclusive-lock (== $held fs:locked)))
          ($w-turn (fs:write (path:join $ldir "turns.jsonl") "{\"t\":1795155000.0,\"engine\":\"cetta\"}\n"))
          ($rproc (proc:spawn ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                    "--import-mode" "ancestor-walk" (oq:obs-entry) "report"
                    "--data" $ldir "--days" "30" "--now" (repr $now))
                    ((mode stream) (max-bytes 65536) (timeout-ms 15000) (lifetime owner))))
          ($rci (proc:close-input $rproc))
          ($wait-mid (host:wait () 250))
          ($st-mid (proc:status $rproc))
          ($t-blk (nt:require report-blocks-while-collector-holds-lock (== $st-mid proc:pending)))
          ($w-eff (fs:write (path:join $ldir "effects.jsonl")
                    "{\"t\":1795155000.0,\"turn\":1,\"kind\":\"send\",\"disposition\":\"returned\",\"reason\":\"ok\"}\n"))
          ($w-sam (fs:write (path:join $ldir "samples.jsonl")
                    "{\"t\":1795155000.0,\"engine\":\"cetta\",\"model\":\"m\",\"rss_mb\":40,\"swap_mb\":0,\"uptime_s\":3600}\n"))
          ($ul (fs:unlock $lk))
          ($cl-lk (fs:close $lk))
          ($rout (oq:drain-stdout $rproc ""))
          ($rres (proc:await $rproc))
          ($rcp (proc:close $rproc))
          ($t-post (nt:require report-after-unlock-sees-complete-snapshot
                     (case $rres
                       (((proc:result (proc:exited 0) $_o $_rerr $_t1 $_t2)
                         (and (oq:check-snapshot-invariant $rout) (nt:contains $rout "samples: 1 ")))
                        ($fail-other6 False)))))
          ; 3) Report formatting, window filtering, and JsonNull -> None parity
          ($fdir (oq:dir "report-fmt"))
          ($ft (fs:write (path:join $fdir "turns.jsonl")
                 "{\"t\":1794000000.0,\"engine\":\"cetta\"}\n{\"t\":1795155000.0,\"engine\":\"cetta\"}\n{\"t\":1795155001.0,\"engine\":\"swipl\"}\n"))
          ($fl (fs:write (path:join $fdir "llm.jsonl")
                 "{\"t\":1795155000.0,\"engine\":\"cetta\",\"provider\":\"anthropic\",\"in\":100,\"cache_read\":50,\"cache_write\":10,\"out\":25}\n{\"t\":1795155001.0,\"engine\":\"cetta\",\"transient\":true}\n"))
          ($fe (fs:write (path:join $fdir "effects.jsonl")
                 "{\"t\":1795155000.0,\"turn\":1,\"kind\":\"send-telegram-chat\",\"disposition\":\"returned\",\"reason\":\"ok\"}\n{\"t\":1795155001.0,\"turn\":2,\"kind\":\"rest\",\"disposition\":\"exception\",\"reason\":\"err\"}\n{\"t\":1795155002.0,\"turn\":3,\"kind\":\"shell\",\"disposition\":\"withheld\",\"reason\":\"pol\"}\n"))
          ($flc (fs:write (path:join $fdir "lifecycle.jsonl")
                  "{\"t\":1795155000.0,\"event\":\"Started Lila\"}\n{\"t\":1795155001.0,\"event\":\"Stopped Lila\"}\n{\"t\":1795155002.0,\"event\":\"engine_failed\",\"engine\":\"cetta\",\"why\":\"boom\"}\n"))
          ($fs (fs:write (path:join $fdir "samples.jsonl")
                 "{\"t\":1795155000.0,\"engine\":null,\"model\":null,\"rss_mb\":null,\"swap_mb\":0,\"uptime_s\":null}\n"))
          ($txt (orep:render $fdir 2 $now False))
          ($t7 (nt:require report-filters-old-day-outside-window (not (nt:contains $txt "2026-11-07"))))
          ($t8 (nt:require report-formats-header-exact
                 (nt:contains $txt "day           turns_cetta   turns_swipl calls_anthrop llm_transient     tokens_in tokens_cached    tokens_out       effects eff_exception  eff_withheld         sends         rests        starts engine_failur")))
          ($t9 (nt:require report-formats-null-sample-fields-as-none
                 (nt:contains $txt "now engine=None model=None rss=NoneMB swap=0MB uptime=0.0h")))
          ($t10 (nt:require report-counts-all-columns-correctly
                  (nt:contains $txt "            1             1             1             1           100            50            25             3             1             1             1             1             1             1"))))
     True))

!(let* (($p1 (oq:test-large-receipts))
        ($p2 (oq:test-large-journal))
        ($p3 (oq:test-power-loss))
        ($p4 (oq:test-reader-visibility-and-report)))
   (println! UNIT_OBSERVATORY_QUALIFICATION_OK))
