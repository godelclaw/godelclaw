; A tool whose native evaluator fails is an ordinary failed receipt: its
; status, its error output naming the uncaught PeTTa error, nothing after the
; failure run, and the gateway serves the next request.
!(import! &self ../gateway_native_support.metta)

!(let* (($root (nt:required-env "TEST_REPOSITORY"))
        ($config (JsonObject ((JsonPair "agent" "test-agent") (JsonPair "state_directory" (gn:root))
          (JsonPair "tools" (JsonObject
            ((JsonPair "native-fault" (JsonObject ((JsonPair "command" (JsonArray ((system:executable) "--lang" "petta"
                                                      (path:join $root "tests/fixtures/gateway_native_fault.metta"))))
                                                    (JsonPair "cwd" $root) (JsonPair "effect" "read") (JsonPair "result_format" "text"))))
             (JsonPair "echo" (gn:fixture-tool "echo" "read" "json"))))))))
        ($server (gn:start $config))
        ($_ (gn:submit "native-error" "native-fault" (JsonObject ())))
        ($receipt (gn:await "native-error" 300))
        ($result (sf:get $receipt "result" JsonNull))
        ($_ (nt:require a-native-evaluator-failure-is-an-ordinary-failed-receipt
              (and (== (sf:get $receipt "state" "") "failed")
              (and (== (sf:get $result "exit_code" 0) 2)
              (and (nt:contains (sf:get $result "stderr" "") "uncaught PeTTa error")
                   (not (nt:contains (sf:get $result "stdout" "") "SHOULD_NOT_RUN")))))))
        ($_ (gn:submit "after-native-error" "echo" "alive"))
        ($after (gn:await "after-native-error" 300))
        ($_ (nt:require the-gateway-serves-the-next-request (== (sf:get $after "state" "") "succeeded")))
        ($_ (proc:cancel $server)))
   (println! UNIT_NATIVE_GATEWAY_FAULT_OK))
