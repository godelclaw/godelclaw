; Initialize a fresh instance from selected release files, without a checkout's
; ignored channel configuration or a developer's private configuration.
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/state_file_cetta.metta)
!(import! &self (library list))
(= (boot:path-guard $operator $artifacts $name $relative $allowed)
   (let* (($root (path:join $artifacts $name)) ($source (path:join $root "release"))
          ($instance (path:join $root "instance")) ($state (path:join $root "state"))
          ($file (path:join (path:join $source "files") $relative))
          ($_ (fs:make-directories (path:dirname $file) 448)) ($_ (fs:make-directories $instance 448))
          ($_ (fs:make-directories $state 448)) ($_ (fs:write $file "# fixture-only data\n"))
          ((fs:hashed-v1 $hash $_) (fs:sha256-file $file))
          ($_ (sf:replace (path:join $source "manifest.json") (json:stringify-python
             (JsonObject ((JsonPair "schema" 1) (JsonPair "release" (system:uuid4-hex))
               (JsonPair "files" (JsonObject ((JsonPair $relative $hash))))))) 384 True))
          ($unit (str:concat "native-template-test-" (str:concat (system:uuid4-hex) ".service")))
          ($_ (nt:require operator-test-has-no-live-service
              (== (str:trim (nt:exit-ok (proc:run ("systemctl" "--user" "show" $unit "-p" "LoadState" "--value")
                    (system:cwd) inherit 5000 65536))) "not-found")))
          ($plan (path:join $root "plan.json"))
          ($_ (sf:replace $plan (json:stringify-python (JsonObject ((JsonPair "instances" (JsonArray
             ((JsonObject ((JsonPair "name" "fixture") (JsonPair "service" $unit) (JsonPair "root" $instance)
               (JsonPair "state_dir" $state) (JsonPair "activate_stopped" True) (JsonPair "checks" (JsonArray ()))
               (JsonPair "expected" (JsonObject ((JsonPair $relative JsonNull)))))))))))) 384 True))
          ; This is the existing permitted deployment operator, under a CeTTa
          ; test controller. It is never a candidate agent or guest fixture.
          ($result (proc:run ("/usr/bin/python3" $operator
                  "--plan" $plan "--release" $source "--receipt-dir" (path:join $root "receipt"))
                  (system:cwd) inherit 10000 1048576)))
     (let $verified
       (case $result
         (((proc:exited $code $out $err)
           (if $allowed (and (== $code 0) (nt:contains $out "prepared"))
             (and (!= $code 0) (nt:contains $err "instance data cannot be a shared release file"))))
          ($bad False)))
       (if $verified (nt:require (release-path-boundary $name) True)
         (let $_ (println! (RELEASE_PATH_UNEXPECTED $name $result))
           (nt:require (release-path-boundary $name) False))))))
!(let* (($release (nt:required-env "NATIVE_TEST_RELEASE"))
         ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
         ($instance (path:join $artifacts (str:concat "fresh-" (system:uuid4-hex))))
         ($home (path:join $instance "test-home")) ($engine (nt:required-env "NATIVE_TEST_CORE"))
         ($_ (fs:make-directories $instance 448)) ($_ (fs:make-directories $home 448))
         ($_ (nt:exit-ok (proc:run ("cp" "-a" (str:concat (path:join $release "files") "/.") $instance)
                                  (system:cwd) inherit 15000 1048576)))
         ($env (list:append (("CETTA_ROOT" $engine) ("CETTA_STDLIB" (path:join $engine "lib"))
                            ("CETTA_BIN" (path:join $engine "cetta"))) (nt:environment $home)))
         ($_ (nt:exit-ok (proc:run ("bash" (path:join $instance "initialize.sh")) $instance $env 15000 1048576)))
         ($_ (nt:require channel-config-is-generated-by-native-initialization
              (nt:contains (fs:read-text (path:join $instance "config/channel.metta")) "configureChannel")))
         ($_ (nt:require environment-is-generated-by-native-configuration
              (nt:contains (fs:read-text (path:join $instance ".env")) "native CeTTa configuration")))
         ($_ (nt:require initialized-native-instance-has-its-prompt-template
              (fs:is-file (path:join $instance "memory/soul.md"))))
         ($secret-copy (path:join $instance "config/secrets.env"))
         ((fs:hashed-v1 $before $_) (fs:sha256-file $secret-copy))
         ($_ (fs:write (path:join $instance "memory/soul.md") "fixture identity; preserve this\n"))
         ($_ (nt:exit-ok (proc:run ("bash" (path:join $instance "initialize.sh")) $instance $env 15000 1048576)))
         ((fs:hashed-v1 $after $_) (fs:sha256-file $secret-copy))
         ($_ (nt:require repeated-initialization-preserves-private-configuration-and-identity
              (and (== $before $after) (== (fs:read-text (path:join $instance "memory/soul.md")) "fixture identity; preserve this\n"))))
         ($operator (path:join (nt:required-env "NATIVE_TEST_SOURCE") "scripts/update_instances.py"))
         ($_ (boot:path-guard $operator $artifacts "public-template" "config/secrets.env.example" True))
         ($_ (boot:path-guard $operator $artifacts "private-secrets" "config/secrets.env" False))
         ($_ (boot:path-guard $operator $artifacts "private-local" "config/local.toml" False))
         ($_ (boot:path-guard $operator $artifacts "private-environment" ".env" False)))
   (println! (NATIVE_ARTIFACT_BOOTSTRAP_PASSED $instance)))
