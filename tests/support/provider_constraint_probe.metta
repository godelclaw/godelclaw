; Fixture operations have explicit rows; they are not a provider gateway.
!(import! &self ../../src/provider_constraint_store_cetta.metta)
!(import! &self ./provider_index_probe.metta)

(= (provider-constraint-probe:apply $path $mutations $page $reads)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-constraint:commit-view $owner $view $epoch $revision $reads $mutations $page))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

(= (provider-constraint-probe:commit $owner $view $delta)
  (case $delta
    (((ProviderConstraintDeltaV1 $operations $reads $writes)
      (case (store:commit-scoped $owner $view $reads $writes $operations)
        (((StoreSnapshot $updated $epoch $revision)
          (let $closed (store:close $updated) (ConstraintAcceptedV1 $revision)))
         ($error $error))))
     ($error $error))))

(= (provider-constraint-probe:point-reads $scopes)
  (if (== $scopes ()) ()
    (let $tail (provider-constraint-probe:point-reads (cdr-atom $scopes))
      (case (car-atom $scopes)
        (((point $key) (cons-atom (point $key) $tail))
         ($other $tail))))))

; Deliberately insufficient negative control, reachable only in this fixture.
(= (provider-constraint-probe:weaken $delta)
  (case $delta
    (((ProviderConstraintDeltaV1 $operations $reads $writes)
      (ProviderConstraintDeltaV1 $operations
        (cons-atom reads (provider-constraint-probe:point-reads (cdr-atom $reads))) $writes))
     ($error $error))))

(= (provider-constraint-probe:bucket-only $delta)
  (case $delta
    (((ProviderConstraintDeltaV1 $operations $reads $writes)
      (ProviderConstraintDeltaV1 $operations
        (cons-atom reads (provider-index-probe:bucket-only (cdr-atom $reads))) $writes))
     ($error $error))))

(= (provider-constraint-probe:choose $mode $delta)
  (case $mode
    ((FixtureBucketOnly (provider-constraint-probe:bucket-only $delta))
     ($boolean (if $boolean (provider-constraint-probe:weaken $delta) $delta)))))

(= (provider-constraint-probe:race-view $owner $view $epoch $revision $first $second $weak)
  (let* (($left (provider-constraint:plan-view $view $epoch $revision $first 1))
         ($right (provider-constraint:plan-view $view $epoch $revision $second 1))
         ($a (provider-constraint-probe:commit $owner $view $left))
         ($chosen (provider-constraint-probe:choose $weak $right))
         ($b (provider-constraint-probe:commit $owner $view $chosen)))
    (ProviderConstraintRaceV1 $a $b)))

(= (provider-constraint-probe:race $path $first $second $weak)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-constraint-probe:race-view $owner $view $epoch $revision $first $second $weak))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

(= (provider-constraint-probe:guard-view $owner $view $epoch $revision $first $second $reads)
  (let* (($delta (provider-constraint:plan-view $view $epoch $revision $first 1))
         ($a (provider-constraint-probe:commit $owner $view $delta))
         ($b (provider-constraint:commit-view $owner $view $epoch $revision $reads $second 1)))
    (ProviderConstraintRaceV1 $a $b)))

(= (provider-constraint-probe:guard $path $first $second $reads)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($answer (provider-constraint-probe:guard-view $owner $view $epoch $revision $first $second $reads))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

; Deliberately place a false-positive candidate in a fixture bucket. This
; tests collision filtering and dependencies without requiring a SHA collision.
(= (provider-constraint-probe:raw $path $key $bytes)
  (case (store:open $path "existing")
    (((StoreOpened $owner)
      (let $answer
        (case (store:snapshot $owner)
          (((StoreSnapshot $view $epoch $revision)
            (let* (($delta (ProviderConstraintDeltaV1 (ops (put $key $bytes)) (reads) (writes (point $key))))
                   ($answer (provider-constraint-probe:commit $owner $view $delta))
                   ($closed (store:close $view))) $answer))
           ($error $error)))
        (let $closed (store:close $owner) $answer)))
     ($error $error))))

(= (provider-constraint-probe:call (Apply $path $mutations $page $reads))
  (provider-constraint-probe:apply $path $mutations $page $reads))
(= (provider-constraint-probe:call (Race $path $first $second))
  (provider-constraint-probe:race $path $first $second False))
(= (provider-constraint-probe:call (WeakRace $path $first $second))
  (provider-constraint-probe:race $path $first $second True))
(= (provider-constraint-probe:call (BucketRace $path $first $second))
  (provider-constraint-probe:race $path $first $second FixtureBucketOnly))
(= (provider-constraint-probe:call (GuardRace $path $first $second $reads))
  (provider-constraint-probe:guard $path $first $second $reads))
(= (provider-constraint-probe:call (Audit $path)) (provider-index-probe:audit $path))
(= (provider-constraint-probe:call (RawPut $path $key $bytes)) (provider-constraint-probe:raw $path $key $bytes))
