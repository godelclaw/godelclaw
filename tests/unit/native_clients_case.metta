!(import! &self ../native_test_support.metta)
!(import! &self ../../src/mcp_bridge_cetta.metta)
!(import! &self ../../src/gateway_client_cetta.metta)
(= (client-case:mcp)
   (let* (($listed (json:parse (mcp:tools "peer")))
          ($_ (nt:require native-tools-list
            (== (sf:get $listed "server" "") "peer")))
          ($tools (sf:get $listed "tools" (JsonArray ())))
          ($_ (nt:require native-tools-wire-schema
            (case $tools
              (((JsonArray ($tool)) (and (== (sf:get $tool "name" "") "echo")
                                        (== (sf:get $tool "description" "") "Echo locally")))
               ($bad False)))))
          ($info (json:parse (mcp:tool-info "peer" "echo")))
          ($_ (nt:require native-tool-information (== (sf:get $info "name" "") "echo")))
          ($called (json:parse (mcp:call-tool "peer" "echo" "{\"value\":\"ček\"}")))
          ($structured (sf:get $called "structuredContent" JsonNull))
          ($_ (nt:require native-tool-effect-once (== (sf:get $structured "effects" 0) 1)))
          ($_ (nt:require stdio-does-not-inherit-provider-secrets
                (== (sf:get $structured "sawPrivate" True) False)))
          ; An Atlas answer: its structured content wins over the text, its view,
          ; state and receipt are recorded, and the next context shows them.
          ($atlas (json:parse (mcp:call-tool "peer" "atlas_query" "{\"text\":\"integration witness\",\"limit\":5}")))
          ($_ (nt:require atlas-answer-is-returned (== (sf:get $atlas "isError" True) False)))
          ($receipts (er:view 10))
          ($_ (nt:require atlas-view-receipt-is-recorded
                (and (nt:contains $receipts "disposition=atlas-result-returned server=peer tool=atlas_query")
                (and (nt:contains $receipts "\"view_id\":\"view:fixture\"")
                (and (nt:contains $receipts "\"state_revision\":\"state:fixture\"")
                     (nt:contains $receipts "\"receipt_id\":\"receipt:fixture\""))))))
          ($_ (nt:require atlas-receipt-reaches-the-next-context
                (case (er:context-load) ((("value" $text) (nt:contains $text "receipt:fixture")) ($o False))))))
     (println! NATIVE_MCP_CLIENT_OK)))
(= (client-case:atlas-payloads)
   (let* (($structured (json:parse "{\"view_id\":\"view:structured\",\"state_revision\":\"state:one\",\"receipt\":{\"receipt_id\":\"receipt:one\"}}"))
          ($payload (json:parse "{\"revision_id\":\"state:two\",\"receipt\":{\"receipt_id\":\"receipt:two\"}}"))
          ($text (json:stringify-python $payload))
          ($_ (nt:require structured-content-precedes-the-text
                (== (mcp:atlas-payload (JsonObject ((JsonPair "structuredContent" $structured)
                      (JsonPair "content" (JsonArray ((JsonObject ((JsonPair "type" "text") (JsonPair "text" "not json"))))))
                      (JsonPair "isError" False))))
                    $structured)))
          ($_ (nt:require text-is-the-fallback
                (== (json:stringify-python (mcp:atlas-payload (JsonObject ((JsonPair "content" (JsonArray ((JsonObject ((JsonPair "type" "text") (JsonPair "text" $text))))))
                                                                         (JsonPair "isError" False)))))
                    $text)))
          ($_ (nt:require an-error-answer-records-nothing
                (== (mcp:atlas-payload (JsonObject ((JsonPair "structuredContent" $payload) (JsonPair "isError" True)))) JsonNull))))
     (println! NATIVE_ATLAS_PAYLOADS_OK)))
(= (client-case:gateway)
   (let $reply (json:parse (gateway:tools))
     (let* (($_ (nt:require unix-native-result (== (sf:get $reply "ok" False) True)))
            ($_ (nt:require unix-split-unicode
                  (== (sf:get (sf:get $reply "value" JsonNull) "text" "") "ček"))))
       (println! NATIVE_GATEWAY_CLIENT_OK))))
(= (client-case:incomplete)
   (let $reply (json:parse (gateway:tools))
     (let* (($_ (nt:require incomplete-line-is-not-a-reply (== (sf:get $reply "ok" True) False)))
            ($_ (nt:require no-implicit-replay
                  (== (str:trim (fs:read-text (path:join (nt:required-env "NATIVE_CASE_DIR") "count"))) "1"))))
       (println! NATIVE_GATEWAY_INCOMPLETE_OK))))
!(case (turn:text (system:arg 0))
   (("mcp" (client-case:mcp)) ("atlas-payloads" (client-case:atlas-payloads)) ("gateway" (client-case:gateway))
    ("incomplete" (client-case:incomplete)) ($bad (system:exit 2))))
