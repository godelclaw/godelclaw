; The build/admin tool is the subject; test orchestration is native MeTTa.
!(import! &self ../tests/provider_request_contracts.metta)
(= (source-snapshot-test:run)
   (let* (($root (system:cwd)) ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($id (system:uuid4-hex)) ($name (str:concat "runtime-source-" (str:byte-slice $id 0 10)))
          ($snapshot (nt:join $artifacts $name)) ($plan (nt:required-env "NATIVE_INSTANCES_PLAN"))
          ($env (nt:environment $artifacts))
          ($revision (str:strip-python (nt:exit-ok (proc:run ("git" "rev-parse" "HEAD") $root $env 5000 1024))))
          ($tool (nt:join $root "scripts/sync_instances.py"))
          ($result (proc:run ("/usr/bin/python3" $tool "--source" $root "--source-snapshot" $snapshot
                             "--ref" $revision "--config" $plan "--status") $root $env 30000 1048576))
          ($_ (nt:exit-ok $result))
          ($_ (nt:require snapshot-is-not-checkout (not (fs:exists (nt:join $snapshot ".git")))))
          ($committed (nt:exit-ok (proc:run ("git" "show" (str:concat $revision ":src/native_llm.metta"))
                                           $root $env 5000 1048576)))
          ($_ (nt:require committed-source-not-working-edits
               (nt:equal (fs:read-text (nt:join $snapshot "src/native_llm.metta")) $committed)))
          ($_ (println! (NATIVE_SOURCE_SNAPSHOT $revision $snapshot)))) NativeSourceSnapshotPassedV1))
!(nt:require source-snapshot-driver-completed
   (nt:equal (collapse (source-snapshot-test:run)) (NativeSourceSnapshotPassedV1)))
!(println! NATIVE_RUNTIME_SOURCE_SNAPSHOT_PASSED)
