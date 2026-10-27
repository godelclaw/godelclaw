; Small-fixture witness: a maximum observes its suffix, not all earlier rows.
; Point-only validation is deliberately insufficient and is never production.
!(import! &self ../../src/provider_order_cetta.metta)

(= (provider-order-probe:reply $answer)
  (case $answer
    (((StoreSnapshot $view $epoch $revision)
      (let $closed (store:close $view) (MaximumAcceptedV1 $revision)))
     ($error $error))))

(= (provider-order-probe:points $reads)
  (if (== $reads ()) ()
    (let $tail (provider-order-probe:points (cdr-atom $reads))
      (case (car-atom $reads)
        (((point $key) (cons-atom (point $key) $tail))
         ($other $tail))))))

(= (provider-order-probe:validate $owner $view $before $reads)
  (let* (($strong (provider-order-probe:reply (store:commit-scoped $owner $view $reads (writes) (ops))))
         ($points (provider-order-probe:points (cdr-atom $reads)))
         ($weak-scopes
           (case $before
             (((ProviderMaximumOrdinalV1 $ordinal $key) (cons-atom (point $key) $points))
              ($empty $points))))
         ($weak-reads (cons-atom reads $weak-scopes))
         ($weak (provider-order-probe:reply (store:commit-scoped $owner $view $weak-reads (writes) (ops)))))
    (ProviderMaximumWitnessV1 $before $strong $weak)))

(= (provider-order-probe:captured $path $owner $view $epoch $revision $kind $mutations)
  (case (provider-order:maximum $view $kind)
    (((ProviderTableMaximumV1 $before $reads)
      (case (provider-index:apply $path $epoch $revision $mutations)
        (((ProviderIndexAppliedV1 $same-epoch $next)
          (provider-order-probe:validate $owner $view $before $reads))
         ($error $error))))
     ($error $error))))

(= (provider-order-probe:call (Witness $path $kind $mutations))
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-order-probe:captured $path $owner $view $epoch $revision $kind $mutations))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))
