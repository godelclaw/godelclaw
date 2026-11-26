; The provider index layout, called in-process. A candidate prefix hashes the
; complete typed wire value: SHA-256 of its CAV1 encoding, checked against
; digests an independent encoder computed (the Python client, frozen here),
; across nulls, empty and NUL text, bytes, blobs, numbers, both boolean forms
; and a long value. Every entry points to the original ordinal and field;
; events index by compound identity and sequence; frontiers have no entries
; and bad fields are explicit.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/provider_indexes_cetta.metta)
!(import! &self ./store_keys.metta)
!(import! &self (library list))

(= (pi:wire $cav1) (let (StoreDecoded $value) (store:decode $cav1) $value))
(= (pi:long) (str:concat "long-value-" (str:join "" (list:repeat 100000 "x"))))
; (value digest) pairs; the digests are SHA-256 of each value's CAV1 bytes.
(= (pi:values)
   ((JsonNull "6aad53158ef563b122e208d1e4533abd39beb2cef65abf777783f5879de9e6dc")
    ("JsonNull" "b979bf818e54376cb1efd09993e0c6ead0a00ef8be79ab779d06cd51505387d5")
    ("" "e16c27906e7c5c3213deb60e994e994af3c6d577b0d6e9cb70ab966973e8b0e3")
    (False "274b953e90c241ed7e460a3b917f559ed49fe18a98998198beedc136e2453879")
    (0 "00f28a422ccce89c7855546792cb1de89a9ff4777d7cda9244b0a1dea69ab0f7")
    (True "de20ac8ec134803ed206d53530d75803f1fafe8699aba6b962431c78b97d7ace")
    ((pi:wire "CAV1N") "f96c8c8fedd0fa1fd2c326953bca170b3e5feb15e27f34cff8ca58f696db859c")
    ((pi:wire "CAV1Y") "f4f22e48807fe48ce04e3f32e70f482805c94d317489a36b10c8fd3dc135f4b4")
    ((pi:wire "CAV1S\x04\x00\x00\x00True") "b681ffe86c7cd222c308a7adfa8af83681ff6d651d54c6ef77753e6001c42a0c")
    (1 "2bc0dc628e2006ce56924c718a956ab71dc6eb23d60216035a8906a7f935e95d")
    (1.0 "24bcf11329b0884266e3fa6b1205865a7b50fb9da87ba6b35380552d555daad1")
    ("λ\x00value" "649883e3026fb6be89f0e2d16e17b84839ace248afa5faef14b4fe6311ad87ac")
    ("invalid-\xff-text" "2dcf871f81cca2ddaa72f519d30f0fc78b74682cf1bc86c466c10856cce55205")
    ((SqlBlobV1 "invalid-\xff-text") "f1a9286e204ff0503ff53c5bb972b715a9f484fc065acf0177da8b82a1e50115")
    ((pi:long) "a3f51837ac9d54c9b487038362cff9abeac60defdbb3a1fbabe19d87a516b795")))
(= (pi:digests)
   (("id" "3a6aa767aa8221589c706dedaa9cb2dff232dc685a0870807fc02719525aa2e4")
    ("status" "9830fb4977b502ee7b6d0f798508c0cc088a4a30c706727c7c16c7fedbcb96d2")
    ("provider" "02b149e679229ac863c80cd1296026a75cfc242cf9792026fc76a0a18939a0ab")
    ("mode" "8d6810ff067833123a3229b845f6947c6e88c12968747bc6098428fb2f924e19")
    ("owner" "de8048118a00d3694a5a95d5d672dd45e7f88b649931c7ec064f1c703dfb3e22")))
(= (pi:prefix $table $column $digest) (str:join "" ("index/v1/" $table $column "/" $digest "/")))
(= (pi:upper $prefix) (str:concat $prefix "\xff\xff\xff\xff\xff\xff\xff\xff\x00"))
(= (pi:range-ok ($value $digest))
   (let $prefix (pi:prefix "requests/" "id" $digest)
     (== (provider-index:call (Prefix ProviderRequestV1 "id" $value)) (ProviderIndexRangeV1 $prefix (pi:upper $prefix)))))
(= (pi:unique $xs) (if (== $xs ()) True (if (pi:member (car-atom $xs) (cdr-atom $xs)) False (pi:unique (cdr-atom $xs)))))
(= (pi:member $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (pi:member $x (cdr-atom $xs)))))
(= (pi:request $value $ordinal)
   (ProviderRequestV1 $value $ordinal 1.0 20 "pending" JsonNull "provider" "coding" JsonNull "owner" "fingerprint"))
; Each entry: the column's prefix and the ordinal's suffix as key, and a
; reference that decodes to the primary record's key.
(= (pi:entries-ok $entries $digests $table $primary $ordinal)
   (if (== $entries ()) (== $digests ())
     (if (== $digests ()) False
       (let* (((ProviderIndexEntryV1 $key $reference) (car-atom $entries))
              (($column $digest) (car-atom $digests))
              ($decoded (store:decode $reference)))
         (and (== $key (sk:key (pi:prefix $table $column $digest) $ordinal))
              (and (== $decoded (StoreDecoded (ProviderIndexV1 $primary)))
                   (pi:entries-ok (cdr-atom $entries) (cdr-atom $digests) $table $primary $ordinal)))))))
(= (pi:ordinal-ok $ordinal)
   (case (provider-index:call (Entries (pi:request "id" $ordinal)))
     (((ProviderIndexEntriesV1 $entries)
       (pi:entries-ok $entries (pi:digests) "requests/" (sk:key "requests/" $ordinal) $ordinal))
      ($other False))))

!(let* (($values (pi:values))
         ($_ (nt:require a-prefix-hashes-the-complete-typed-wire-value
               (== (filter-atom $values $pair (not (pi:range-ok $pair))) ())))
         ($_ (nt:require typed-values-get-distinct-prefixes
               (pi:unique (map-atom $values $pair (index-atom $pair 1)))))
         ($_ (nt:require every-entry-points-to-the-original-ordinal-and-field
               (== (filter-atom (-9223372036854775808 -7 0 1 9223372036854775807) $o (not (pi:ordinal-ok $o))) ())))
         ($event (ProviderEventV1 "call" "usage" 97 1.5 "{}"))
         ($_ (nt:require events-index-by-compound-identity-and-sequence
               (case (provider-index:call (Entries $event))
                 (((ProviderIndexEntriesV1 $entries)
                   (pi:entries-ok $entries
                     (("identity" "79ebafc621ea325cd92199f3f4650ff6c0df703daf3ec4441c4603bdb9d3ff63")
                      ("call_id" "19e26bfc7cbc0a0c88e8b7a5d8286732384679c35faa973d4a92f0b1fe711e69")
                      ("event" "779ff7c9d3309f45b683ffd77f7b024672ba54d03b1fe2da26d9cf72d2f9e94c"))
                     "llm_call_history/" (sk:key "llm_call_history/" 97) 97))
                  ($other False)))))
         ($_ (nt:require an-event-reads-back-its-indexed-fields
               (and (== (provider-index:call (Field $event "identity")) (ProviderIndexFieldValueV1 (ProviderEventIdentityV1 "call" "usage")))
               (and (== (provider-index:call (Field $event "call_id")) (ProviderIndexFieldValueV1 "call"))
                    (== (provider-index:call (Field $event "event")) (ProviderIndexFieldValueV1 "usage"))))))
         ($_ (nt:require frontiers-and-bad-fields-are-explicit
               (and (== (provider-index:call (Entries (ProviderSequenceV1 "requests" 100))) (ProviderIndexEntriesV1 ()))
               (and (== (provider-index:call (Entries (ProviderRequestV1 "short"))) (ProviderIndexRefusedV1 record-shape))
               (and (== (provider-index:call (Prefix ProviderRequestV1 "unknown" "x")) (ProviderIndexRefusedV1 unknown-column))
                    (== (provider-index:call (Field (pi:request "x" 1) "unknown")) ProviderIndexFieldMissingV1)))))))
   (println! UNIT_PROVIDER_INDEXES_OK))
