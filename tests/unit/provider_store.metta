; The provider checkpoint store, called in-process as the agent calls it.
; Records read back exactly as SQLite stored them (nullable identities, NUL
; and non-ASCII text, the extreme ordinals), in the order of their keys;
; assignments apply in input order; epoch and revision each guard a read; a
; malformed batch imports nothing of its valid prefix; frontier and event
; sequences keep keys of their own. Keys are built independently: the prefix,
; then the ordinal with its sign bit flipped as eight big-endian bytes.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/provider_store_cetta.metta)
!(import! &self (library sqlite))
!(import! &self ./store_keys.metta)

(= (ps:same $actual $expected) (== (store:encode $actual) (store:encode $expected)))
(= (ps:request $ordinal $output)
   (ProviderRequestV1 "request" $ordinal 1.25 20 "unknown" $output "fixture" "coding" JsonNull "owner" "fingerprint"))
; A fresh store: (Store path epoch).
(= (ps:store $home $name)
   (let* (($path (path:join $home $name)) ($_ (fs:make-directories $path 448)))
     (case (provider-store:checkpoint-call (Create $path))
       (((ProviderStoreReady $epoch 0) (Store $path $epoch))
        ($other (let $_ (println! (PROVIDER_STORE_NOT_READY $other)) (system:exit 2)))))))
(= (ps:put (Store $path $epoch) $revision $records)
   (ps:same (provider-store:checkpoint-call (Import $path $records)) (ProviderStoreWritten $epoch $revision)))
(= (ps:read (Store $path $epoch) $at-epoch $revision $keys) (provider-store:checkpoint-call (Read $path $at-epoch $revision $keys)))
(= (ps:null $v) (if (== $v sqlite:null) JsonNull $v))
(= (ps:record ($rowid $id $started $reserved $status $output $provider $mode $finish $owner $fingerprint))
   (ProviderRequestV1 (ps:null $id) $rowid (ps:null $started) (ps:null $reserved) (ps:null $status) (ps:null $output)
                      (ps:null $provider) (ps:null $mode) (ps:null $finish) (ps:null $owner) (ps:null $fingerprint)))
(= (ps:reverse $xs $acc) (if (== $xs ()) $acc (ps:reverse (cdr-atom $xs) (cons-atom (car-atom $xs) $acc))))
(= (ps:insert $ordinal $identity)
   (sqlite:statement "INSERT INTO requests(rowid,id,started,reserved,status,output,provider,mode,finish,owner,fingerprint) VALUES(?,?,1.25,20,'unknown',NULL,'fixture','coding',NULL,'owner','fingerprint')"
                     ($ordinal $identity)))
(= (ps:malformed-refused $store $malformed)
   (let (Store $path $epoch) $store
     (and (case (provider-store:checkpoint-call (Import $path ((ps:request 1 JsonNull) $malformed)))
            (((ProviderStoreRefused $why) True) ($other False)))
          (and (ps:same (ps:read $store $epoch 0 ((sk:key "requests/" 1))) StoreAbsent)
               (ps:same (ps:read $store $epoch 0 ()) (ProviderStoreRows ()))))))

!(let* (($home (nt:required-env "HOME"))
         ($_ (nt:require independent-keys-match-the-store
               (case (store:key-i64 9) (((StoreKeyBytes $suffix) (== (str:concat "requests/" $suffix) (sk:key "requests/" 9))) ($bad False)))))
         ; Rows exactly as SQLite keeps them, read in key order either way.
         ($database (path:join $home "requests.db"))
         ($ordinals (-9223372036854775808 -1 0 9223372036854775807))
         ($_ (nt:require the-fixture-table-is-written
               (case (sqlite:transaction $database "immediate"
                       ((sqlite:statement "CREATE TABLE requests(id TEXT PRIMARY KEY,started REAL,reserved INTEGER,status TEXT,output,provider TEXT,mode TEXT,finish,owner TEXT,fingerprint TEXT)" ())
                        (ps:insert -9223372036854775808 sqlite:null) (ps:insert -1 sqlite:null)
                        (ps:insert 0 "Unicode-λ\x00") (ps:insert 9223372036854775807 "literal (+ 2 3)"))
                       5000)
                 (((sqlite:committed $results) True) ($bad (let $_ (println! (SQLITE_REFUSED $bad)) False))))))
         ((sqlite:result $columns $rows $changes $last) (sqlite:query $database "SELECT rowid,* FROM requests ORDER BY rowid" () 5000))
         ($records (map-atom $rows $row (ps:record $row)))
         ($store (ps:store $home "nullable"))
         ((Store $path $epoch) $store)
         ($_ (nt:require sqlite-rows-import (ps:put $store 1 $records)))
         ($keys (map-atom $ordinals $o (sk:key "requests/" $o)))
         ($_ (nt:require nullable-identities-and-duplicate-payloads-keep-their-occurrences
               (and (== (size-atom $records) 4)
                    (and (ps:same (ps:read $store $epoch 1 $keys) (ProviderStoreRows $records))
                         (ps:same (ps:read $store $epoch 1 (ps:reverse $keys ())) (ProviderStoreRows (ps:reverse $records ())))))))
         ; Assignments apply in input order without moving the identity.
         ($ordered (ps:store $home "ordered"))
         ((Store $ordered-path $ordered-epoch) $ordered)
         ($_ (nt:require assignments-apply-in-input-order
               (and (ps:put $ordered 1 ((ps:request 9 "before") (ps:request 9 "after")))
                    (ps:same (ps:read $ordered $ordered-epoch 1 ((sk:key "requests/" 9))) (ProviderStoreRows ((ps:request 9 "after")))))))
         ; Epoch and revision each guard a read.
         ($guarded (ps:store $home "guarded"))
         ((Store $guarded-path $guarded-epoch) $guarded)
         ($_ (nt:require epoch-and-revision-each-guard-a-read
               (and (ps:put $guarded 1 ((ps:request 1 JsonNull)))
               (and (ps:same (ps:read $guarded "foreign-epoch" 1 ()) (ProviderStoreChanged $guarded-epoch 1))
               (and (ps:same (ps:read $guarded $guarded-epoch 0 ()) (ProviderStoreChanged $guarded-epoch 1))
                    (ps:same (ps:read $guarded $guarded-epoch 1 ()) (ProviderStoreRows ())))))))
         ; A malformed batch imports nothing of its valid prefix.
         ($strict (ps:store $home "strict"))
         ($_ (nt:require a-malformed-batch-imports-nothing
               (and (ps:malformed-refused $strict not-a-record)
               (and (ps:malformed-refused $strict ())
               (and (ps:malformed-refused $strict (UnknownRecord 1))
                    (ps:malformed-refused $strict (ProviderRequestV1 "too-short")))))))
         ; Frontier and event sequences keep keys of their own.
         ($sequences (ps:store $home "sequences"))
         ((Store $sequences-path $sequences-epoch) $sequences)
         ($event (ProviderEventV1 "call" "usage" 17 1.5 "{\"known\":false}"))
         ($frontier (ProviderSequenceV1 "llm_call_history" 17))
         ($recovery (ProviderAdmissionRecoveryV1 "id" -2 1.25 "same payload" 0.5))
         ($_ (nt:require frontier-and-event-sequences-are-distinct-from-record-positions
               (and (ps:put $sequences 1 ($event $frontier $recovery))
                    (ps:same (ps:read $sequences $sequences-epoch 1
                               ((sk:key "llm_call_history/" 17) "frontier/llm_call_history" (sk:key "admission_recovery/" -2)))
                             (ProviderStoreRows ($event $frontier $recovery)))))))
   (println! UNIT_PROVIDER_STORE_OK))
