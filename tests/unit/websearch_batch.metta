!(import! &self ../native_test_support.metta)
!(import! &self ../../src/webread_cetta.metta)
!(import! &self ../../src/webbrowse_cetta.metta)
; Batched search and page reading, without the network.

(= (wsb:row $url) (JsonObject ((JsonPair "title" (str:concat "t:" $url)) (JsonPair "url" $url) (JsonPair "snippet" "s"))))
(= (wsb:keys $rows) (map-atom $rows $r (search:key $r)))

; The merge of BatchedQueryMerge: rounds over the lists, the first of each key kept.
!(let* (($l1 ((wsb:row "https://a.example/") (wsb:row "https://b.example") (wsb:row "https://c.example")))
        ($l2 ((wsb:row "https://B.example/#top") (wsb:row "https://d.example")))
        ($merged (search:merge ($l1 $l2 ())))
        ($_ (nt:require merge-rounds-and-first-key
              (== (wsb:keys $merged) ("https://a.example" "https://b.example" "https://d.example" "https://c.example"))))
        ; Fairness at r = 1: each list's first key among the first 3 * 1 results.
        ($first (wsb:keys (list:take 3 $merged)))
        ($_ (nt:require merge-fair-first-results
              (and (erv:contains $first "https://a.example") (erv:contains $first "https://b.example"))))
        ($_ (nt:require merge-empty (== (search:merge (() ())) ())))
        ($_ (nt:require query-lines (== (search:query-lines " a \n\n b\nc\nd\ne\nf\ng ") ("a" "b" "c" "d" "e" "f"))))
        ($_ (nt:require one-line-is-one-query (== (search:query-lines "only one") ("only one")))))
   (println! UNIT_SEARCH_MERGE_OK))

; SearXNG's JSON rows, tagged by query, listed with their tags.
!(let* (($payload (json:parse "{\"results\":[{\"title\":\" One \",\"url\":\"https://one.example\",\"content\":\"first  snippet\"},{\"title\":\"Two\",\"url\":\"https://two.example\",\"content\":\"\"},{\"title\":\"Three\",\"url\":\"https://three.example\",\"content\":\"x\"}]}"))
        ($rows (search:searxng-results $payload 2))
        ($_ (nt:require searxng-count (== (size-atom $rows) 2)))
        ($_ (nt:require searxng-fields
              (and (== (sf:get (car-atom $rows) "title" "") "One") (== (sf:get (car-atom $rows) "snippet" "") "first snippet"))))
        ($text (search:format-batch (search:tag $rows 2)))
        ($_ (nt:require batch-tags (nt:contains $text "(Q2 TITLE: One URL: https://one.example SNIPPET: first snippet)"))))
   (println! UNIT_SEARCH_SEARXNG_OK))

; Page text: navigation before the page heading goes; the budget cuts; passages
; around matches, near windows joined.
!(let* (($page "nav one\nnav two\n# Title\nalpha\nbeta needle\ngamma\ndelta\nepsilon NEEDLE\nzeta\neta\ntheta\niota\nkappa\nlambda needle\nmu")
        ($_ (nt:require main-from-heading (str:starts-with (web:main $page) "# Title")))
        ($_ (nt:require main-keeps-late-heading
              (== (web:main "a\nb\nc\nd\ne\nf\ng\n# Late") "a\nb\nc\nd\ne\nf\ng\n# Late")))
        ($cut (web:cut "abcdefghij" 4))
        ($_ (nt:require cut-keeps-budget (str:starts-with $cut "abcd\n[... 6 more characters")))
        ($passages (web:passages $page "Needle"))
        ($_ (nt:require passages-joined-and-split (== (size-atom $passages) 2)))
        ($_ (nt:require passage-window (== (car-atom $passages) "# Title\nalpha\nbeta needle\ngamma\ndelta\nepsilon NEEDLE\nzeta\neta")))
        ($_ (nt:require no-passage (nt:contains (web:present "https://p.example" $page "absent") "no line of https://p.example contains"))))
   (println! UNIT_WEB_READ_TEXT_OK))

; The fallback text and the URL guard.
!(let* (($plain (web:plain "<html><head><style>x{}</style><script>var a=1;</script></head><body><p>Fish &amp; chips</p><div>a&nbsp;b &lt;c&gt;</div></body></html>"))
        ($_ (nt:require plain-drops-code (not (nt:contains $plain "var a"))))
        ($_ (nt:require plain-text (and (nt:contains $plain "Fish & chips") (nt:contains $plain "a b <c>"))))
        ($_ (nt:require scheme-guard (str:starts-with (web:read "file:///etc/passwd" "") "READ_URL_ERROR: not an http(s) URL"))))
   (println! UNIT_WEB_READ_FALLBACK_OK))

; Browsing without a browser: ports, guards, the budget and unknown actions.
!(let* (($godel (browse:port-of "godel")) ($lila (browse:port-of "lila")))
   (let* (($_ (nt:require browse-ports-differ (not (== $godel $lila))))
          ($_ (nt:require browse-port-range
                (and (>= (str:string->number $godel) 9300) (< (str:string->number $godel) 9500))))
          ($_ (nt:require browse-scheme-guard (str:starts-with (browse:command "open" "file:///etc/passwd" "") "BROWSE_ERROR: not an http(s) URL")))
          ($_ (nt:require browse-unknown-action (str:starts-with (browse:command "teleport" "" "") "BROWSE_ERROR: unknown action")))
          ($_ (nt:require browse-error-shown (== (browse:show (BrowseFailedV1 "x")) "BROWSE_ERROR: x")))
          ($_ (nt:require browse-ok-trimmed (== (browse:show (BrowseOkV1 "  page  ")) "page"))))
     (println! UNIT_WEB_BROWSE_OK)))
