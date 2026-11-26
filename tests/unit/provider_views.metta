; The retained-call views, called in-process, against answers the retired
; SQLite reference reader gave on the same inputs (frozen in
; tests/fixtures/provider_views_reference.json; the reader is
; tests/oracles/provider_sqlite at tag python-reference-2026-11-26). Status
; and notices keep their purpose filters and timestamp ties whatever the
; input order, including after a row is replaced; a retained result renders,
; slices and digests as the reader did at every offset; a missing identity
; and a negative offset are explicit.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/provider_views_cetta.metta)
!(import! &self (library json))
!(import! &self (library list))

(= (vw:field (JsonObject $pairs) $key)
   (if (== $pairs ()) (Missing $key)
     (let (JsonPair $k $v) (car-atom $pairs) (if (== $k $key) $v (vw:field (JsonObject (cdr-atom $pairs)) $key)))))
(= (vw:items (JsonArray $items)) $items)
(= (vw:record $row) (cons-atom ProviderCallV1 (vw:items $row)))
; call-1 replaced: more bytes, a later update; its ordinal is unchanged.
(= (vw:updated $row)
   (let $items (vw:items $row)
     (if (== (car-atom $items) "call-1")
         (vw:record (JsonArray (vw:set (vw:set $items 11 999999) 6 2000.0)))
         (vw:record $row))))
(= (vw:set $items $at $value)
   (if (== $at 0) (cons-atom $value (cdr-atom $items)) (cons-atom (car-atom $items) (vw:set (cdr-atom $items) (- $at 1) $value))))
(= (vw:purposes) ("turn" "coding" "compaction"))
; The result rows, in the reference's insertion order, rowids from 1.
(= (vw:result-rows $texts $canonical $number)
   (if (== $texts ()) ()
     (list:append (vw:purpose-rows (car-atom $texts) $canonical $number (vw:purposes) 0)
                  (vw:result-rows (cdr-atom $texts) $canonical (+ $number 1)))))
(= (vw:purpose-rows $text $canonical $number $purposes $p)
   (if (== $purposes ()) ()
     (cons-atom
       (ProviderCallV1 (format-args "r-{}-{}" ($number (car-atom $purposes))) (+ (* $number 3) (+ $p 1)) "epoch" 1 "born"
                       1000.0 1000.125 "completed" JsonNull 3 0 100 12 $text
                       (if (== $text $canonical) "command-output:invalid-native-tool" JsonNull) "" "" (car-atom $purposes) 0)
       (vw:purpose-rows $text $canonical $number (cdr-atom $purposes) (+ $p 1)))))
; The results that differ from the reference's, as (id offset).
(= (vw:result-misses $records $expected)
   (if (== $expected ()) ()
     (let* (((JsonArray ($id $offset $want)) (car-atom $expected))
            ($rest (vw:result-misses $records (cdr-atom $expected))))
       (if (== (provider-view:result $records $id $offset) $want) $rest (cons-atom ($id $offset) $rest)))))

!(let* (($fixture (json:parse-python (fs:read-text (path:join (nt:required-env "TEST_REPOSITORY") "tests/fixtures/provider_views_reference.json"))))
         ($rows (vw:items (vw:field $fixture "status_records")))
         ($records (map-atom $rows $row (vw:record $row)))
         ($_ (nt:require the-fixture-holds-the-status-rows (== (size-atom $records) 140)))
         ($_ (nt:require status-matches-the-reference
               (== (json:stringify-python (provider-view:status $records)) (vw:field $fixture "status"))))
         ($_ (nt:require notices-keep-purpose-filters-and-timestamp-ties
               (== (provider-view:notices $records) (map-atom (vw:items (vw:field $fixture "notices")) $pair (vw:items $pair)))))
         ($_ (nt:require a-replaced-row-keeps-its-ordinal-and-ties
               (== (json:stringify-python (provider-view:status (map-atom $rows $row (vw:updated $row)))) (vw:field $fixture "updated_status"))))
         ($canonical (vw:field $fixture "canonical"))
         ($texts (JsonNull "" "short" (str:join "" (list:repeat 7000 "a")) (str:join "" (list:repeat 1100 "λ🌱\x00\n")) $canonical))
         ($results (vw:result-rows $texts $canonical 0))
         ($misses (vw:result-misses $results (vw:items (vw:field $fixture "results"))))
         ($_ (if (== $misses ()) True (println! (RESULT_MISSES $misses))))
         ($_ (nt:require retained-results-render-slice-and-digest-as-the-reference
               (and (== (size-atom (vw:items (vw:field $fixture "results"))) 108) (== $misses ()))))
         ($_ (nt:require a-huge-offset-is-exact
               (== (provider-view:result $results "r-4-turn" 1000000000000000000000000000000) (vw:field $fixture "huge_offset_result"))))
         ($_ (nt:require missing-identities-and-negative-offsets-are-explicit
               (and (== (json:stringify-python (provider-view:status ())) "[]")
               (and (== (provider-view:notices ()) ())
               (and (== (provider-view:result () "absent" 0) JsonNull)
                    (== (provider-view:result () "absent" -1) (ProviderViewError "result offset must be nonnegative"))))))))
   (println! UNIT_PROVIDER_VIEWS_OK))
