!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/omega/native/bootstrap.metta)
!(let* (($tools (omega-host:collect-tools (getSkills)))
        ($names (map-atom $tools $tool (sf:get (sf:get $tool "function" JsonNull) "name" "")))
        ($_ (nt:require all-sixteen-omega-tools-retained (== (size-atom $names) 16)))
        ($_ (nt:require web-tools-advertised (and (erv:contains $names "websearch") (and (erv:contains $names "read-url") (erv:contains $names "find-in-url")))))
        ($_ (nt:require zero-argument-tools-retained (and (erv:contains $names "version") (erv:contains $names "nop"))))
        ($_ (nt:require metadata-names-are-text
              (== (filter-atom $names $name (str:starts-with $name "<internal-tag")) ())))
        ($request (JsonObject ((JsonPair "tools" (JsonArray $tools)))))
        ($reply (json:parse "{\"message\":{\"tool_calls\":[{\"id\":\"t1\",\"function\":{\"name\":\"send-channel\",\"arguments\":\"{\\\"content\\\":\\\"literal (nop)\\\",\\\"channel\\\":\\\"telegram\\\"}\"}}]}}"))
        ($response (omega-host:response $request $reply))
        ($source (omega-host:response-source $response))
        ($_ (nt:require ordered-parameters-and-inert-source
              (== $source "((\"t1\" (send-channel \"telegram\" \"literal (nop)\")))")))
        ($bad (json:parse "{\"message\":{\"tool_calls\":[{\"id\":\"t2\",\"function\":{\"name\":\"shell\",\"arguments\":\"{\\\"cmd\\\":7}\"}}]}}"))
        ($rejected (omega-host:response $request $bad))
        ($_ (nt:require invalid-argument-is-an-error
              (nt:contains (omega-host:response-source $rejected) "(Error ")))
        ($unknown (json:parse "{\"message\":{\"tool_calls\":[{\"id\":\"t3\",\"function\":{\"name\":\"not-advertised\",\"arguments\":\"{}\"}}]}}"))
        ($_ (nt:require unknown-tool-is-not-a-command
              (nt:contains (omega-host:response-source (omega-host:response $request $unknown)) "Unknown tool")))
        ($_ (nt:require completed-no-calls-is-observable
              (== (omega-host:response-source (omega-host:response $request (JsonObject ())))
                  "(Error () \"List of tool calls is empty\")")))
        ($_ (nt:require leap-day-accepted (!= (mode-history:civil-seconds "2024-02-29 00:00:00") none)))
        ($_ (nt:require invalid-leap-day-refused (== (mode-history:civil-seconds "2023-02-29 00:00:00") none)))
        ($_ (nt:require gregorian-century-rule
              (and (== (mode-history:civil-seconds "1900-02-29 00:00:00") none)
                   (!= (mode-history:civil-seconds "2000-02-29 00:00:00") none))))
        ($_ (nt:require adjacent-date-distance
              (== (- (mode-history:civil-seconds "2024-03-01 00:00:00")
                     (mode-history:civil-seconds "2024-02-29 00:00:00")) 86400)))
        ($_ (nt:require full-casefold-preserved (== (unicode:casefold "Straße ΣΟΣ") "strasse σοσ"))))
   (println! UNIT_OMEGA_NATIVE_OK))
