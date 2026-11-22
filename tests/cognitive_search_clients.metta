; Search clients against the native HTTP fixture peer: Tavily, the local
; metasearch (SearXNG's JSON API), a batch whose two queries find the same
; page, and the fallback from an unreachable metasearch to Tavily. One body
; serves both providers: their JSON lists results with title, url, content.
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
          ($_ (nt:require every-client-request-reached-the-peer (== (nq:posts $world) 5)))
          ($_ (nq:close $world)))
     NativeSearchClientsPassedV1))
