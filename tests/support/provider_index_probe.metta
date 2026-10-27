; Controlled small-fixture inspection and the collision-footprint witness.
; These entry points are not runtime queries and never contact a provider.
!(import! &self ../../src/provider_index_query_cetta.metta)

(= (provider-index-probe:scan $view $scan $position $reverse)
  (case (store:scan-next $scan $position)
    ((StoreEnd (ProviderIndexAuditV1 (provider-store:reverse $reverse ())))
     ((StoreKey $key $size $next)
       (case (store:get $view $key 1048576)
         (((StoreValue $bytes)
           (let $rows (cons-atom ($key $bytes) $reverse)
             (provider-index-probe:scan $view $scan $next $rows)))
          ($error $error))))
     ($error $error))))

(= (provider-index-probe:audit-view $view)
  (case (store:scan-open $view NoBound NoBound)
    (((StoreScan $scan)
      (let* (($answer (provider-index-probe:scan $view $scan 0 ()))
             ($closed (store:close $scan))) $answer))
     ($error $error))))

(= (provider-index-probe:audit $path)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-index-probe:audit-view $view))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

(= (provider-index-probe:bucket-only $scopes)
  (if (== $scopes ()) ()
    (let* (($scope (car-atom $scopes)) ($tail (provider-index-probe:bucket-only (cdr-atom $scopes))))
      (case $scope
        (((range $lower $upper) (cons-atom $scope $tail))
         ((point $key) (if (== $key (provider-index:layout-key)) (cons-atom $scope $tail) $tail))
         ($other $tail))))))

(= (provider-index-probe:weak-reply $answer)
  (case $answer
    (((StoreSnapshot $updated $epoch $revision)
      (let $closed (store:close $updated) (WeakAcceptedV1 $revision)))
     ($error $error))))

(= (provider-index-probe:after-count $owner)
  (case (store:snapshot $owner)
    (((StoreSnapshot $view $epoch $revision)
      (let $answer
        (case (provider-index:query-page $view $epoch $revision ProviderRequestV1 "status" "unknown" NoBound 100)
          (((ProviderIndexPageV1 $rows ProviderIndexDoneV1 $reads) (size-atom $rows))
           ($error $error)))
        (let $closed (store:close $view) $answer)))
     ($error $error))))

(= (provider-index-probe:validate $owner $view $rows $reads)
  (let* (($strong (store:commit-scoped $owner $view $reads (writes) (ops)))
         ($weak-scopes (provider-index-probe:bucket-only (cdr-atom $reads)))
         ($weak-reads (cons-atom reads $weak-scopes))
         ($weak (provider-index-probe:weak-reply (store:commit-scoped $owner $view $weak-reads (writes) (ops))))
         ($after (provider-index-probe:after-count $owner)))
    (ProviderIndexWitnessV1 (size-atom $rows) $after $strong $weak)))

(= (provider-index-probe:witness-view $path $owner $view $epoch $revision $replacement)
  (case (provider-index:query-page $view $epoch $revision ProviderRequestV1 "status" "unknown" NoBound 100)
    (((ProviderIndexPageV1 $rows ProviderIndexDoneV1 $reads)
      (case (provider-index:apply $path $epoch $revision ((put $replacement)))
        (((ProviderIndexAppliedV1 $same-epoch $after)
          (provider-index-probe:validate $owner $view $rows $reads))
         ($error $error))))
     ($error $error))))

(= (provider-index-probe:witness $path $replacement)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-index-probe:witness-view $path $owner $view $epoch $revision $replacement))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

(= (provider-index-probe:call (Audit $path)) (provider-index-probe:audit $path))
(= (provider-index-probe:call (Witness $path $replacement)) (provider-index-probe:witness $path $replacement))
