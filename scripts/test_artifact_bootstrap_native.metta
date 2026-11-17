; Initialize a fresh instance from selected release files, without a checkout's
; ignored channel configuration or a developer's private configuration.
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/state_file_cetta.metta)
!(import! &self (library list))
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
              (fs:is-file (path:join $instance "memory/soul.md")))))
   (println! (NATIVE_ARTIFACT_BOOTSTRAP_PASSED $instance)))
