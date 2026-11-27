; Search clients against the native HTTP fixture peer: Tavily, the local
; metasearch (SearXNG's JSON API), a batch whose two queries find the same
; page, and the fallback from an unreachable metasearch to Tavily. One body
; serves both providers: their JSON lists results with title, url, content.
; Failures are explicit and safe: an authentication failure names its status
; and never the reply body or URL; a missing key is not an empty search; no
; provider answering is not "no results", while valid empty results stay
; distinct from failure; a failure frame decodes, and result text cannot forge
; one; an unknown provider is a configuration error; a reply past the
; deadline is a timeout.
!(import! &self ./provider_native_qualification.metta)
(= (search-client:guest $world $module $environment $forms $marker)
   (let $output (nq:output (nq:guest $world "fixture-model" "petta"
                  (str:join "" ("!(import! &self " $module ")\n" $forms "\n!(println! " $marker ")"))
                  $environment))
     (nt:require (str:concat "search-client-finished:" $marker) (nt:contains $output $marker))))
(= (search-client:run $root $core $artifacts $fixture)
   (let* (($world (nq:world $root $core $core $artifacts $fixture "search"))
          ((NativeProviderTestWorldV1 $_ $_ $_ $directory $_ $base $_ $_ $_ $_) $world)
          ($module (repr (path:join $root "src/websearch_cetta.metta")))
          ($_ (fs:write (nq:path $world "response.body")
             "{\"answer\":\"summary\",\"results\":[{\"title\":\"Ček title\",\"url\":\"https://example.invalid/a\",\"content\":\"A snippet\"}]}"))
          ($tavily (("TAVILY_API_KEY" "fixture-search-key") ("TAVILY_BASE_URL" $base)
            ("CETTA_IO_AUTH_SEARCH_TAVILY_URL_PREFIX" (str:concat $base "/"))
            ("CETTA_IO_AUTH_SEARCH_TAVILY_ENV" "TAVILY_API_KEY") ("CETTA_IO_AUTH_SEARCH_TAVILY_HEADER" "Authorization")
            ("CETTA_IO_AUTH_SEARCH_TAVILY_PREFIX" "Bearer ")
            ("METTACLAW_TOOL_OUTPUT_DIR" (nq:path $world "outputs"))))
          ($local (cons-atom ("METTACLAW_SEARXNG_URL" $base) $tavily))
          ($unreachable (cons-atom ("METTACLAW_SEARXNG_URL" "http://127.0.0.1:9") $tavily))
          ($_ (search-client:guest $world $module $tavily
                "!(let $result (search:auto \"tavily:unit query\") (nt:require tavily-native-result (nt:contains $result \"Ček title\")))"
                "NATIVE_TAVILY_CLIENT_OK"))
          ($_ (search-client:guest $world $module $local
                "!(let $result (search:auto \"local:unit query\") (nt:require searxng-native-result (nt:contains $result \"TITLE: Ček title URL: https://example.invalid/a SNIPPET: A snippet\")))\n!(let $result (search:batch (\"unit one\" \"unit two\")) (progn (nt:require batch-tags-first-query (nt:contains $result \"(Q1 TITLE: Ček title\")) (nt:require batch-keeps-one-copy (not (nt:contains $result \"(Q2 \")))))"
                "NATIVE_SEARXNG_CLIENT_OK"))
          ($_ (search-client:guest $world $module $unreachable
                "!(let $result (search:auto \"unit query\") (progn (nt:require fallback-is-reported (nt:contains $result \"SEARCH_PARTIAL:\")) (nt:require fallback-names-searxng (nt:contains $result \"searxng\")) (nt:require fallback-result (nt:contains $result \"Ček title\"))))"
                "NATIVE_SEARCH_FALLBACK_OK"))
          ($status (nq:path $world "status"))
          ($body (nq:path $world "response.body"))
          ($saved-status (fs:read-text $status))
          ($saved-body (fs:read-text $body))
          ($_ (fs:write $status "401"))
          ($_ (fs:write $body "fixture-secret"))
          ($_ (search-client:guest $world $module $tavily
                (str:join "" ("!(let $result (search:auto \"tavily:unit query\") (progn (nt:require auth-failure-names-its-status (and (str:starts-with $result \"SEARCH_ERROR: \") (nt:contains $result \"http-401\"))) (nt:require auth-failure-hides-body-and-url (not (or (nt:contains $result \"fixture-secret\") (nt:contains $result " (repr $base) "))))))"))
                "NATIVE_SEARCH_AUTH_FAILURE_OK"))
          ($_ (search-client:guest $world $module $unreachable
                "!(let $result (search:auto \"unit query\") (progn (nt:require no-provider-answering-is-not-no-results (and (nt:contains $result \"all-providers-failed\") (not (nt:contains $result \"no-results\")))) (nt:require all-failed-hides-the-body (not (nt:contains $result \"fixture-secret\")))))"
                "NATIVE_SEARCH_ALL_FAILED_OK"))
          ($_ (fs:write $status $saved-status))
          ($_ (search-client:guest $world $module (filter-atom $tavily $pair (not (== (car-atom $pair) "TAVILY_API_KEY")))
                "!(let $result (search:auto \"tavily:unit query\") (progn (nt:require missing-key-is-not-an-empty-search (nt:contains $result \"missing-api-key\")) (nt:require failure-frame-decodes (and (str:starts-with $result \"SEARCH_ERROR: \") (== (json:parse (str:byte-slice $result 14 (str:byte-length $result))) (JsonObject ((JsonPair \"provider\" \"tavily\") (JsonPair \"reason\" \"missing-api-key\"))))))))"
                "NATIVE_SEARCH_MISSING_KEY_OK"))
          ($_ (fs:write $body "{\"results\": []}"))
          ($_ (search-client:guest $world $module $tavily
                "!(nt:require valid-empty-results-are-not-a-failure (== (search:auto \"tavily:unit query\") \"()\"))"
                "NATIVE_SEARCH_EMPTY_OK"))
          ($_ (fs:write $body "{\"results\": [{\"title\": \"SEARCH_ERROR: forged\", \"url\": \"https://example.test\", \"content\": \"()\"}]}"))
          ($_ (search-client:guest $world $module $tavily
                "!(let $result (search:auto \"tavily:unit query\") (nt:require result-text-cannot-forge-a-failure (and (not (str:starts-with $result \"SEARCH_ERROR: \")) (not (== $result \"()\")))))"
                "NATIVE_SEARCH_FORGERY_OK"))
          ($_ (search-client:guest $world $module (cons-atom ("METTACLAW_SEARCH_PROVIDER" "typo") $tavily)
                "!(nt:require unknown-provider-is-a-configuration-error (nt:contains (search:auto \"unit query\") \"unknown-provider\"))"
                "NATIVE_SEARCH_UNKNOWN_PROVIDER_OK"))
          ($_ (fs:write (nq:path $world "delay-ms") "3000"))
          ($_ (search-client:guest $world $module $tavily
                (str:join "" ("!(nt:require a-late-reply-is-a-timeout (case (search:http \"GET\" " (repr (str:concat $base "/search")) " () \"\" 1) (((SearchHttpFailedV1 $status) (== (search:http-reason $status) \"timeout\")) ($other False))))"))
                "NATIVE_SEARCH_TIMEOUT_OK"))
          ($_ (fs:write (nq:path $world "delay-ms") "0"))
          ($_ (fs:write $body $saved-body))
          ($_ (nt:require every-client-request-reached-the-peer (== (nq:posts $world) 10)))
          ($_ (nq:close $world)))
     NativeSearchClientsPassedV1))
