; Native unit tests for incremental semantic-memory writes (#9) and
; workspace-filtered delta queries in src/memory_views.metta.
; Spawns a process-owned cetta-space-service so zero servers can leak.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/semantic_memory_cetta.metta)
!(import! &self (library io))
!(import! &self (library list))

(= (smi:wait-socket $sock $left)
   (if (fs:exists $sock)
     True
     (if (<= $left 0)
       False
       (let (io:pending $h) (io:submit (timer:after 20))
         (let $_ (io:wait $h 1000) (smi:wait-socket $sock (- $left 1)))))))

(= (smi:hit-ids $res)
   (case $res
     (((MemoryResults $hits $un)
       (map-atom $hits $h
         (case $h (((MemoryHit $id $rev $tm $dist $doc) $id) ($o $o)))))
      ($other $other))))

(= (smi:hit-docs $res)
   (case $res
     (((MemoryResults $hits $un)
       (map-atom $hits $h
         (case $h (((MemoryHit $id $rev $tm $dist $doc) ($id $doc)) ($o $o)))))
      ($other $other))))

(= (smi:setup-store $sock $name $profile)
   (case (space:open $sock $name native)
     (((reply $rid ok $space)
       (let* (($v1 (tensor:from f64 (3) (1.0 0.0 0.0)))
              ($v2 (tensor:from f64 (3) (0.0 1.0 0.0)))
              ($desc "{\"embedding_output_dim\":3,\"embedding_protocol\":\"unit-v1\"}")
              ($r1 (Memory "a" 1 $profile $v1 "alpha base" (("time" "1"))))
              ($r2 (Memory "b" 1 $profile $v2 "beta base" (("time" "2"))))
              ($ops ((add (MemoryProfile $profile $desc))
                     (add $r1)
                     (add $r2))))
         (case (hm:commit $sock $space $ops)
           (((reply $cid ok (committed $cu $ci $nrev)) True)
            ($err $err)))))
      ($err $err))))

(= (smi:external-add $sock $name $id $profile $vec $doc)
   (case (space:open $sock $name native)
     (((reply $rid ok (space $u $sid $sn $b $k $rev $cnt))
       (let* (($sp (space $u $sid $sn $b $k $rev $cnt))
              ($row (Memory $id (+ $rev 1) $profile $vec $doc (("time" "ext")))))
         (case (hm:commit $sock $sp ((add $row)))
           (((reply $cid ok (committed $cu $ci $nrev)) True)
            ($err $err)))))
      ($err $err))))

(= (smi:run)
   (let* (($core (nt:required-env "NATIVE_TEST_CORE"))
          ($server-bin (path:join $core "cetta-space-service"))
          ($home (nt:required-env "HOME"))
          ($data (nt:join $home "space-data"))
          ($_ (fs:make-directories $data 448))
          ($sock (str:concat "/tmp/smi-" (str:concat (str:byte-slice (system:uuid4-hex) 0 12) ".sock")))
          ($server (proc:spawn ($server-bin "--socket" $sock "--data" $data)
                               ((max-bytes 65536) (lifetime owner))))
          ($_ (nt:require space-server-ready (smi:wait-socket $sock 200)))
          ($cfg (nt:join $home "backend.json"))
          ($cfg-json (json-stringify
            (JsonObject ((JsonPair "mode" (JsonString "native"))
                         (JsonPair "socket" (JsonString $sock))
                         (JsonPair "name" (JsonString "sem-inc-unit"))
                         (JsonPair "state_dir" (JsonString $home))))))
          ($_ (fs:write $cfg $cfg-json))
          ($_ (nt:require backend-config-exists (fs:exists $cfg)))
          ($profile "unit-profile-v1")
          ($_ (nt:require seed-store-ok (== (smi:setup-store $sock "sem-inc-unit" $profile) True)))
          ; Use explicit METTACLAW_MEMORY_BACKEND_CONFIG set by test_units_cetta.
          ($v1 (tensor:from f64 (3) (1.0 0.0 0.0)))
          ($v2 (tensor:from f64 (3) (0.0 1.0 0.0)))
          ($v3 (tensor:from f64 (3) (0.0 0.0 1.0)))

          ; 1. Cold query triggers exactly 1 full load.
          ($q0 (sm:query-vector $v1 5 "probe"))
          ($_ (nt:require initial-load-count (== (sm:reload-count) 1)))
          ($_ (nt:require initial-hit-a (== (car-atom (smi:hit-ids $q0)) "a")))

          ; 2. Remember with workspace capture: queue + apply without full reload.
          ($_ (workspace "proof" ("task:proof") () "task:proof"))
          ($_ (workspace "proof"))
          ($_ (ws:begin "t1" "unit" "ch1"))
          ($qr (sm:queue "c" remember "gamma remembered"))
          ($_ (nt:require queue-no-reload (== (sm:reload-count) 1)))
          ((MStore $sp-q $pq $dq) (sm:open))
          ($pend-c (car-atom (sm:pending-id $sp-q "c")))
          ($_ (nt:require apply-c-ok (== (sm:apply-write $pend-c $v3) True)))
          ($_ (nt:require apply-c-no-reload (== (sm:reload-count) 1)))

          ; Workspace-filtered query (twice in a row to catch $packed & $ready rebinding!)
          ($qw1 (sm:query-vector $v3 5 "probe"))
          ($qw2 (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require filtered-query-1-finds-c (== (smi:hit-ids $qw1) ("c"))))
          ($_ (nt:require filtered-query-2-finds-c (== (smi:hit-ids $qw2) ("c"))))
          ($_ (nt:require filtered-queries-no-reload (== (sm:reload-count) 1)))
          ($_ (workspace "all"))

          ; All-workspace query finds c at top for v3 without reload.
          ($qa1 (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require all-query-finds-c-first (== (car-atom (smi:hit-ids $qa1)) "c")))
          ($_ (nt:require all-query-no-reload (== (sm:reload-count) 1)))

          ; 3. Edit an existing base row ("a") and a delta row ("c") without reload.
          ($_ (memory-edit "a" "alpha edited to v3"))
          ((MStore $sp-ea $pea $dea) (sm:open))
          ($pend-a (car-atom (sm:pending-id $sp-ea "a")))
          ($_ (nt:require apply-edit-a-ok (== (sm:apply-write $pend-a $v3) True)))
          ($_ (memory-edit "c" "gamma edited"))
          ((MStore $sp-ec $pec $dec) (sm:open))
          ($pend-c2 (car-atom (sm:pending-id $sp-ec "c")))
          ($_ (nt:require apply-edit-c-ok (== (sm:apply-write $pend-c2 $v3) True)))
          ($q-ed (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require edit-no-reload (== (sm:reload-count) 1)))
          ($_ (nt:require edited-a-visible (memory:member ("a" "alpha edited to v3") (smi:hit-docs $q-ed))))
          ($_ (nt:require edited-c-visible (memory:member ("c" "gamma edited") (smi:hit-docs $q-ed))))
          ; Scope on "c" survived the edit!
          ($_ (workspace "proof"))
          ($q-ed-ws (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require edited-c-kept-scope (== (smi:hit-docs $q-ed-ws) (("c" "gamma edited")))))
          ($_ (workspace "all"))

          ; 4. Workspace-tag add, duplicate add (idempotent), and remove without reload.
          ($_ (workspace-tag "a" ("task:proof" "group:lab") "add"))
          ($_ (workspace-tag "a" ("task:proof") "add"))
          ($_ (workspace-tag "b" ("group:lab" "person:blocked") "add"))
          ($_ (nt:require tag-add-no-reload (== (sm:reload-count) 1)))
          ; Same-row local-upsert must never delete the existing record!
          ($rec-a (car-atom (collapse (memory:record "a"))))
          ($_ (nt:require identical-upsert-ok (== (sm:local-upsert $profile 3 $rec-a) True)))
          ($_ (nt:require identical-upsert-preserved (== (collapse (memory:record "a")) ($rec-a))))
          ($_ (workspace "complex" ("task:proof" "group:lab") ("person:blocked") ""))
          ($_ (workspace "complex"))
          ($q-cx-inc (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require complex-excludes-blocked (not (memory:member "b" (smi:hit-ids $q-cx-inc)))))
          ($_ (nt:require complex-includes-a (memory:member "a" (smi:hit-ids $q-cx-inc))))
          ($_ (nt:require complex-includes-c (memory:member "c" (smi:hit-ids $q-cx-inc))))
          ($_ (workspace-tag "a" ("task:proof" "group:lab") "remove"))
          ($q-cx-rem (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require tag-remove-hides-a (== (smi:hit-ids $q-cx-rem) ("c"))))
          ($_ (nt:require tag-remove-no-reload (== (sm:reload-count) 1)))

          ; 5. Exact parity vs fresh full reload (both complex workspace and All).
          ($q-all-inc (sm:query-vector $v1 5 "probe"))
          ($_ (workspace "all"))
          ($q-all-inc2 (sm:query-vector $v3 5 "probe"))
          ($_ (change-state! &semantic-cache Missing))
          ($q-all-full (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require forced-reload-bumped (== (sm:reload-count) 2)))
          ($_ (nt:require parity-all-exact (== $q-all-inc2 $q-all-full)))
          ($_ (workspace "complex"))
          ($q-cx-full (sm:query-vector $v3 5 "probe"))
          ($_ (nt:require parity-complex-exact (== $q-cx-rem $q-cx-full)))
          ($_ (workspace "all"))

          ; 6. Forget base row ("b") and delta row ("c") without reload.
          ($_ (forget "b" "done"))
          ($_ (forget "c" "done"))
          ($q-fg (sm:query-vector $v2 5 "probe"))
          ($_ (nt:require forget-no-reload (== (sm:reload-count) 2)))
          ($_ (nt:require forget-removed-b-and-c (== (smi:hit-ids $q-fg) ("a"))))

          ; 7. External concurrent writer invalidates cache and triggers clean reload.
          ($_ (nt:require ext-write-1-ok (== (smi:external-add $sock "sem-inc-unit" "ext-1" $profile $v2 "external one") True)))
          ($q-ext1 (sm:query-vector $v2 5 "probe"))
          ($_ (nt:require ext-write-1-reloaded (== (sm:reload-count) 3)))
          ($_ (nt:require ext-write-1-visible (== (car-atom (smi:hit-ids $q-ext1)) "ext-1")))

          ; 8. External write BEFORE our own commit forces our commit to clear cache (no stale skip!).
          ($_ (nt:require ext-write-2-ok (== (smi:external-add $sock "sem-inc-unit" "ext-2" $profile $v1 "external two") True)))
          ($_ (workspace-tag "a" ("task:after-ext") "add"))
          ($_ (nt:require own-commit-after-ext-invalidated (== (sm:cache-state) Missing)))
          ($q-ext2 (sm:query-vector $v1 5 "probe"))
          ($_ (nt:require ext-write-2-reloaded (== (sm:reload-count) 4)))
          ($_ (nt:require ext-write-2-visible (memory:member "ext-2" (smi:hit-ids $q-ext2))))

          ; 9. Closed-world unknown op & unexpected commit reply fail closed.
          ($_ (nt:require unknown-op-unsupported (== (sm:classify-ops ((add (MemoryFuture 1 "x"))) () () ()) SMDeltaUnsupported)))
          ($_ (sm:cache-after-commit (space "u" 1 "n" native bag 1 1) () (unexpected-reply)))
          ($_ (nt:require bad-reply-invalidates (== (sm:cache-state) Missing)))

          ; Clean shutdown and socket cleanup.
          ($_ (catch (proc:cancel $server)))
          ($_ (catch (fs:remove $sock))))
     True))

!(nt:require semantic-memory-incremental-suite (smi:run))
!(println! UNIT_SEMANTIC_MEMORY_INCREMENTAL_OK)
