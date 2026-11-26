; Actual native durable memory plus a local native embedding protocol peer.
!(import! &self ./provider_native_qualification.metta)
!(import! &self (library lib_tensor))
(= (memory-flow:run)
   (let* (($root (system:cwd)) ($core (nt:required-env "NATIVE_TEST_CORE"))
          ($world (nq:world $root $core $core (nt:required-env "NATIVE_TEST_ARTIFACTS")
                    (nt:required-env "NATIVE_TEST_HTTP_FIXTURE") "memory"))
          ($directory (nq:path $world "")) ($socket (nq:path $world "m.sock"))
          ($data (nq:path $world "memory-data")) ($service (path:join $core "cetta-space-service"))
          ($server (proc:spawn ($service "--socket" $socket "--data" $data)
                    ((env (nt:environment $directory)) (max-bytes 65536))))
          ($_ (nq:wait-file $server $socket 100))
          ($opened (space:open $socket "unit-memory" native))
          ($_ (nt:require native-memory-space-opened (case $opened (((reply $opened-id ok $space) True) ($bad False)))))
          ((reply $opened-id ok $view) $opened)
          ($a (tensor:from f64 (3) (1.0 0.0 0.0)))
          ($b (tensor:from f64 (3) (0.0 1.0 0.0)))
          ($seed (space:batch $socket $view
            (ops (add (MemoryProfile "unit-profile" "{\"embedding_output_dim\":3}"))
                 (add (Memory "a" 1 "unit-profile" $a "old alpha" (("time" "original"))))
                 (add (Memory "b" 1 "unit-profile" $b "beta reference" (("time" "original")))))))
          ($_ (println! (NATIVE_MEMORY_SEED $seed)))
          ((reply $seed-id ok $seed-result) $seed)
          ($config (nq:path $world "backend.json"))
          ($_ (fs:write $config (str:join "" ("{\"mode\":\"native\",\"name\":\"unit-memory\",\"socket\":" (repr $socket) "}"))))
          ($_ (fs:write (nq:path $world "response.body")
            "{\"protocol\":\"fixture-v1\",\"dim\":3,\"embeddings\":[[1.0,0.0,0.0]]}"))
          ($module (repr (path:join $root "src/semantic_memory_cetta.metta")))
          ($prefix (str:join "" ("!(import! &self " $module ")\n")))
          ($body "
(= (memory-flow:drain $left)
   (let $_ (semantic:maintain)
     (if (== (memory-pending) ()) True
       (if (<= $left 0) False
         (let (io:pending $timer) (io:submit (timer:after 10))
           (progn (io:wait $timer 100) (memory-flow:drain (- $left 1))))))))
!(nt:require real-native-retrieval
   (case (semantic:query \"alpha\" 2)
     (((MemoryResults $hits (Unindexed 0)) (not (== $hits ()))) ($bad False))))
!(nt:require edit-retains-durable-intent
   (case (memory-edit \"a\" \"corrected alpha Ω\") (((MemoryQueued \"a\" $op) True) ($bad False))))
!(nt:require native-embedding-write-settles (memory-flow:drain 100))
!(nt:require corrected-record-is-readable
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"corrected alpha Ω\" $meta ()) True) ($bad False))))
!(nt:require original-record-is-in-history (nt:contains (repr (memory-history \"a\")) \"old alpha\"))
!(nt:require forget-has-native-receipt (== (forget \"b\") (MemoryForgotten \"b\")))
!(println! NATIVE_MEMORY_MUTATION_OK)")
          ((NativeProviderTestWorldV1 $_ $_ $_ $_ $_ $endpoint $_ $_ $_ $_) $world)
          ($environment (("METTACLAW_MEMORY_BACKEND_CONFIG" $config) ("METTACLAW_EMBED_ENDPOINT" $endpoint)
                        ("METTACLAW_EMBED_PROTOCOL" "fixture-v1")))
          ($output (nq:output (nq:guest $world "fixture-model" "petta" (str:concat $prefix $body) $environment)))
          ($_ (nt:require native-memory-mutation-completed (nt:contains $output "NATIVE_MEMORY_MUTATION_OK")))
          ($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))
          ($restarted (proc:spawn ($service "--socket" $socket "--data" $data)
                       ((env (nt:environment $directory)) (max-bytes 65536))))
          ($_ (nq:wait-file $restarted $socket 100))
          ($restored (nq:output (nq:guest $world "fixture-model" "petta"
            (str:concat $prefix "
!(nt:require corrected-record-survives-service-restart
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"corrected alpha Ω\" $meta ()) True) ($bad False))))
!(nt:require forgotten-record-stays-absent
   (case (memory-read \"b\") (((MemoryCurrent \"b\" absent \"\" () ()) True) ($bad False))))
!(println! NATIVE_MEMORY_RESTART_OK)") $environment)))
          ($_ (nt:require fresh-evaluator-and-store-recovered (nt:contains $restored "NATIVE_MEMORY_RESTART_OK")))
          ($_ (proc:cancel $restarted)) ($_ (proc:await $restarted)) ($_ (proc:close $restarted))
          ($_ (nq:close $world)))
     NativeCognitiveMemoryPassedV1))
!(nt:require cognitive-memory-flow-completed
   (== (collapse (memory-flow:run)) (NativeCognitiveMemoryPassedV1)))
;
; Controls over the same native store and embedding peer, each case on a
; freshly seeded store: an embedding outage, an in-flight embedding overtaken
; by forget and by a newer edit, a retry that wakes the idle loop, the public
; skills through the agent bootstrap, health over the provider and an empty
; collection, a provider in another protocol, and a disconnected store.
(= (mc:prefix $root)
   (str:join "" ("!(import! &self " (repr (path:join $root "src/semantic_memory_cetta.metta")) ")
(= (fixture:drain $n)
   (let $_ (semantic:maintain)
     (if (== (memory-pending) ()) True
       (if (== $n 0) (FixturePending (memory-pending))
         (let (io:pending $h) (io:submit (timer:after 10)) (let $_ (io:wait $h 1000) (fixture:drain (- $n 1))))))))
")))
(= (mc:guest $world $root $forms $env)
   (nq:output (nq:guest $world "fixture-model" "petta" (str:concat (mc:prefix $root) $forms) $env)))
(= (mc:env $world $config $retry)
   (let (NativeProviderTestWorldV1 $_ $_ $_ $_ $_ $endpoint $_ $_ $_ $_) $world
     (("METTACLAW_MEMORY_BACKEND_CONFIG" $config) ("METTACLAW_EMBED_ENDPOINT" $endpoint)
      ("METTACLAW_EMBED_PROTOCOL" "fixture-v1") ("METTACLAW_MEMORY_RETRY_SECONDS" $retry))))
(= (mc:serve $service $socket $data $directory)
   (let* (($_ (if (fs:exists $socket) (fs:remove $socket) True))
          ($server (proc:spawn ($service "--socket" $socket "--data" $data)
                    ((env (nt:environment $directory)) (max-bytes 65536))))
          ($_ (nq:wait-file $server $socket 100)))
     $server))
(= (mc:stop $server) (let* (($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))) True))
; A store named $case holding the two original records.
(= (mc:store $world $service $case)
   (let* (($socket (nq:path $world (str:concat $case ".sock"))) ($data (nq:path $world (str:concat $case "-data")))
          ($server (mc:serve $service $socket $data (nq:path $world "")))
          ((reply $opened-id ok $view) (space:open $socket $case native))
          ($a (tensor:from f64 (3) (1.0 0.0 0.0))) ($b (tensor:from f64 (3) (0.0 1.0 0.0)))
          ((reply $seed-id ok $seeded)
           (space:batch $socket $view
             (ops (add (MemoryProfile "unit-profile" "{\"embedding_output_dim\":3}"))
                  (add (Memory "a" 1 "unit-profile" $a "old alpha" (("time" "original"))))
                  (add (Memory "b" 1 "unit-profile" $b "beta reference" (("time" "original")))))))
          ($config (nq:path $world (str:concat $case ".json")))
          ($_ (fs:write $config (str:join "" ("{\"mode\":\"native\",\"name\":" (repr $case) ",\"socket\":" (repr $socket) "}")))))
     (MemoryCase $server $socket $data $config)))

(= (mc:outage $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "outage"))
          ($env (mc:env $world $config ".05"))
          ($_ (fs:write (nq:path $world "status") "503"))
          ($queued (mc:guest $world $root "
!(println! (memory-edit \"a\" \"saved through outage Ω\"))
!(fixture:drain 10)
!(println! (PENDING (memory-pending)))
!(nt:require a-query-in-an-outage-never-reads-as-absence
   (case (semantic:query \"alpha\" 2) (((MemoryUnavailable embedding $why) True) ($bad False))))
!(nt:require an-outage-keeps-the-committed-record
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"old alpha\" $meta $pending) (not (== $pending ()))) ($bad False))))
!(println! MEMORY_OUTAGE_QUEUED)" $env))
          ($_ (nt:require an-outage-queues-the-edit-durably
                (and (nt:contains $queued "(MemoryQueued \"a\"")
                     (and (nt:contains $queued "saved through outage Ω")
                          (and (nt:contains $queued "MemoryUnavailable embedding") (nt:contains $queued "MEMORY_OUTAGE_QUEUED"))))))
          ($_ (mc:stop $server))
          ($restarted (mc:serve $service $socket $data (nq:path $world "")))
          ($_ (fs:write (nq:path $world "status") "200"))
          ($recovered (mc:guest $world $root "
!(nt:require a-restart-recovers-the-edit-without-a-new-remember (fixture:drain 100))
!(nt:require the-recovered-edit-is-committed
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"saved through outage Ω\" $meta ()) True) ($bad False))))
!(println! MEMORY_OUTAGE_RECOVERED)" $env))
          ($_ (nt:require an-outage-recovers-after-restart (nt:contains $recovered "MEMORY_OUTAGE_RECOVERED"))))
     (mc:stop $restarted)))

(= (mc:inflight $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "inflight"))
          ($_ (fs:write (nq:path $world "delay-ms") "100"))
          ($out (mc:guest $world $root "
!(memory-edit \"a\" \"obsolete in-flight alpha\")
!(println! (forget \"a\" \"withdrawn\"))
!(memory-edit \"b\" \"first superseded alpha\")
!(memory-edit \"b\" \"newest beta Ω\")
!(nt:require the-writes-settle (fixture:drain 100))
!(nt:require forget-wins-over-an-inflight-embedding
   (case (memory-read \"a\") (((MemoryCurrent \"a\" absent \"\" $meta $pending) True) ($bad False))))
!(nt:require the-newest-edit-wins-over-an-inflight-one
   (case (memory-read \"b\") (((MemoryCurrent \"b\" $rev \"newest beta Ω\" $meta ()) True) ($bad False))))
!(println! MEMORY_INFLIGHT_OK)" (mc:env $world $config ".05")))
          ($_ (fs:write (nq:path $world "delay-ms") "0"))
          ($_ (nt:require inflight-embeddings-yield-to-later-intent (nt:contains $out "MEMORY_INFLIGHT_OK"))))
     (mc:stop $server)))

; Only the next embedding request fails; its retry must wake the idle loop.
(= (mc:idle $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "idle"))
          ($_ (fs:write (nq:path $world (format-args "status-{}" ((+ (nq:posts $world) 1)))) "503"))
          ($out (mc:guest $world $root (str:join "" ("
!(import! &self " (repr (path:join $root "src/idle_cetta.metta")) ")
(= (mail:enabled?) False)
(= (fixture:idle-drain $n)
   (let $_ (semantic:maintain)
     (if (== (memory-pending) ()) $n
       (if (>= $n 8) (FixtureIdleFailed (memory-pending))
         (let* (($subscriptions (idle:arm 1000 True))
                ($_ (idle:wait $subscriptions 1000 True))
                ($_ (idle:finish $subscriptions)))
           (fixture:idle-drain (+ $n 1)))))))
!(memory-edit \"a\" \"idle recovered beta\")
; Each idle wait ends after a second unless something wakes it: the retry,
; due after 0.12 s, has to.
!(nt:require a-retry-wakes-the-idle-loop
   (let* (($start (system:monotonic-ns)) ($drained (fixture:idle-drain 0))
          ($ms (/ (- (system:monotonic-ns) $start) 1000000)) ($_ (println! (IDLE_WAKES $drained $ms))))
     (case $drained (((FixtureIdleFailed $pending) False) ($wakes (and (>= $wakes 1) (and (<= $wakes 3) (< $ms 900))))))))
!(nt:require the-retried-edit-is-committed
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"idle recovered beta\" $meta ()) True) ($bad False))))
!(println! MEMORY_IDLE_RETRY_OK)")) (mc:env $world $config ".12")))
          ($_ (nt:require retries-wake-the-idle-loop-without-model-polling (nt:contains $out "MEMORY_IDLE_RETRY_OK"))))
     (mc:stop $server)))

; The agent's public skills, through the same bootstrap the launcher runs.
(= (mc:public $world $service $root $sdk)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "public"))
          ($probe (nq:path $world "public.metta"))
          ($_ (fs:write $probe "
!(println! (PUBLIC_QUERY (query \"alpha\" 2)))
!(println! (PUBLIC_EDIT (memory-edit \"a\" \"public corrected beta Ω\")))
!(println! (PUBLIC_READ (memory-read \"a\")))
!(println! (PUBLIC_FORGET (forget \"a\" \"fixture finished\")))
!(println! (PUBLIC_HEALTH (memory-health)))
!(println! (PUBLIC_HELP (help \"memory-edit\")))
"))
          ($env (nq:guest-env $world "fixture-model" (mc:env $world $config ".05")))
          ($out (nt:exit-ok (proc:run ((path:join $sdk "cetta") "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk"
                                       "--quiet" (path:join $root "cetta_bootstrap.metta") $probe)
                                      $root $env 30000 16777216)))
          ($_ (nt:require the-public-memory-skills-work-through-the-bootstrap
                (and (nt:contains $out "(MemoryHit \"a\"")
                     (and (nt:contains $out "(MemoryQueued \"a\"")
                          (and (nt:contains $out "public corrected beta Ω")
                               (and (nt:contains $out "(MemoryForgotten \"a\")")
                                    (and (nt:contains $out "(MemoryHealth healthy native")
                                         (nt:contains $out "Revise semantic memory under the same ID")))))))))
     (mc:stop $server)))

(= (mc:health $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "health"))
          ($env (mc:env $world $config ".05"))
          ($healthy (mc:guest $world $root "!(println! (semantic:health))" $env))
          ($_ (nt:require health-reports-the-working-store (nt:contains $healthy "(MemoryHealth healthy native 1")))
          ($_ (fs:write (nq:path $world "status") "503"))
          ($down (mc:guest $world $root "!(println! (semantic:health))" $env))
          ($_ (fs:write (nq:path $world "status") "200"))
          ($_ (nt:require health-checks-the-embedding-provider
                (and (nt:contains $down "(MemoryHealth unavailable") (not (nt:contains $down "MemoryHealth healthy")))))
          ($empty (mc:guest $world $root "!(forget \"a\")\n!(forget \"b\")\n!(println! (semantic:health))" $env))
          ($_ (nt:require health-accepts-an-empty-working-collection (nt:contains $empty "(MemoryHealth healthy native 0"))))
     (mc:stop $server)))

; A provider that answers in another protocol is refused as such, never read
; as an outage or as absence, and nothing is saved with its vector.
(= (mc:drift $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "drift"))
          ($body (nq:path $world "response.body")) ($good (fs:read-text $body))
          ($_ (fs:write $body "{\"protocol\":\"other\",\"dim\":3,\"embeddings\":[[1.0,0.0,0.0]]}"))
          ($out (mc:guest $world $root "
!(nt:require protocol-drift-is-refused-by-name
   (== (semantic:query \"alpha\" 2) (MemoryUnavailable embedding protocol-mismatch)))
!(println! (memory-edit \"a\" \"drifted alpha\"))
!(fixture:drain 5)
!(nt:require nothing-is-saved-under-a-drifted-protocol
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"old alpha\" $meta $pending) (not (== $pending ()))) ($bad False))))
!(nt:require health-reports-the-drift
   (case (semantic:health) (((MemoryHealth unavailable $why) True) ((MemoryHealth unavailable $why $more) True) ($bad False))))
!(println! MEMORY_DRIFT_REFUSED)" (mc:env $world $config ".05")))
          ($_ (fs:write $body $good))
          ($_ (nt:require a-drifted-provider-is-refused (nt:contains $out "MEMORY_DRIFT_REFUSED"))))
     (mc:stop $server)))

; Every write is refused explicitly while the store is gone, and nothing is lost.
(= (mc:disconnected $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "gone"))
          ($env (mc:env $world $config ".05"))
          ($_ (mc:stop $server))
          ($out (mc:guest $world $root "
!(println! (memory-edit \"a\" \"uncommitted\"))
!(println! (forget \"a\"))
!(println! (semantic:query \"alpha\" 2))
!(println! (semantic:health))" $env))
          ($_ (nt:require a-disconnected-store-is-explicit
                (and (nt:contains $out "MemoryUnavailable store")
                     (and (not (nt:contains $out "MemoryQueued")) (not (nt:contains $out "MemoryForgotten"))))))
          ($back (mc:serve $service $socket $data (nq:path $world "")))
          ($kept (mc:guest $world $root "
!(nt:require the-records-outlast-the-disconnection
   (case (memory-read \"a\") (((MemoryCurrent \"a\" $rev \"old alpha\" $meta ()) True) ($bad False))))
!(println! MEMORY_DISCONNECTED_KEPT)" $env))
          ($_ (nt:require a-disconnection-loses-no-records (nt:contains $kept "MEMORY_DISCONNECTED_KEPT"))))
     (mc:stop $back)))

(= (memory-controls:run)
   (let* (($root (system:cwd)) ($core (nt:required-env "NATIVE_TEST_CORE"))
          ($world (nq:world $root $core $core (nt:required-env "NATIVE_TEST_ARTIFACTS")
                    (nt:required-env "NATIVE_TEST_HTTP_FIXTURE") "memory-controls"))
          ($service (path:join $core "cetta-space-service"))
          ($_ (fs:write (nq:path $world "response.body")
            "{\"protocol\":\"fixture-v1\",\"dim\":3,\"embeddings\":[[1.0,0.0,0.0]]}"))
          ($_ (mc:outage $world $service $root))
          ($_ (mc:inflight $world $service $root))
          ($_ (mc:idle $world $service $root))
          ($_ (mc:public $world $service $root $core))
          ($_ (mc:health $world $service $root))
          ($_ (mc:drift $world $service $root))
          ($_ (mc:disconnected $world $service $root))
          ($_ (nq:close $world)))
     NativeMemoryControlsPassedV1))
!(nt:require memory-controls-completed
   (== (collapse (memory-controls:run)) (NativeMemoryControlsPassedV1)))
!(println! NATIVE_COGNITIVE_MEMORY_PASSED)
