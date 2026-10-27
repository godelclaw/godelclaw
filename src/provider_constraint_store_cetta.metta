; Inactive constraint boundary for complete, already-bound provider records.
; Input affinity and Python parameter conversion belong to the transition
; kernel. In particular, this is not an SQL assignment/coercion interface.
; Validation sees the original captured index view plus earlier mutations.
; Publication combines its negative/positive reads with the caller's complete
; transition reads. It never opens a replacement view or dispatches an effect.
!(import! &self ./provider_index_query_cetta.metta)

(= (provider-constraint:spec $row)
  (case $row
    (((ProviderRequestV1 $id $ordinal $started $reserved $status $output $provider $mode $finish $owner $fingerprint)
      (ProviderConstraintSpecV1 "requests"
        (("started" $started) ("reserved" $reserved) ("status" $status) ("provider" $provider)
         ("mode" $mode) ("owner" $owner) ("fingerprint" $fingerprint))
        (ProviderConstraintUniqueV1 "id" $id)))
     ((ProviderCooldownV1 $provider $ordinal $until)
      (ProviderConstraintSpecV1 "cooldowns" (("until" $until))
        (ProviderConstraintUniqueV1 "provider" $provider)))
     ((ProviderCallV1 $id $ordinal $epoch $pid $start $created $updated $state $guard $transport
                     $elapsed $bytes $quiet $result $reason $activity $lane $purpose $notified)
      (ProviderConstraintSpecV1 "native_calls"
        (("epoch" $epoch) ("pid" $pid) ("created" $created) ("updated" $updated) ("state" $state)
         ("soft_elapsed" $elapsed) ("bytes" $bytes) ("quiet_ms" $quiet) ("activity" $activity)
         ("lane" $lane) ("purpose" $purpose) ("notified" $notified))
        (ProviderConstraintUniqueV1 "id" $id)))
     ((ProviderEventV1 $id $event $sequence $observed $detail)
      (ProviderConstraintSpecV1 "llm_call_history"
        (("call_id" $id) ("event" $event) ("observed" $observed) ("detail" $detail))
        (ProviderConstraintUniqueV1 "identity" (ProviderEventIdentityV1 $id $event))))
     ((ProviderAdmissionRecoveryV1 $id $ordinal $recovered $reason $cutoff)
      (ProviderConstraintSpecV1 "admission_recovery"
        (("recovered_at" $recovered) ("reason" $reason) ("cutoff" $cutoff))
        (ProviderConstraintUniqueV1 "id" $id)))
     ((ProviderSequenceV1 $table $maximum)
      (ProviderConstraintSpecV1 "frontier" () ProviderConstraintNoUniqueV1))
     ($other (ProviderConstraintRefusedV1 record-shape)))))

(= (provider-constraint:not-null $table $fields)
  (if (== $fields ()) ProviderConstraintValidV1
    (let ($column $value) (car-atom $fields)
      (if (== $value JsonNull) (ProviderConstraintViolationV1 not-null $table $column)
        (provider-constraint:not-null $table (cdr-atom $fields))))))

(= (provider-constraint:prepend $items $tail)
  (if (== $items ()) $tail
    (cons-atom (car-atom $items) (provider-constraint:prepend (cdr-atom $items) $tail))))

; Keep only the most recent state of a touched primary key. Tombstones must
; shadow the original snapshot, including when a unique value is reused.
(= (provider-constraint:forget $overlay $key)
  (if (== $overlay ()) ()
    (let (ProviderIndexOverlayV1 $stored $value) (car-atom $overlay)
      (if (== $stored $key) (cdr-atom $overlay)
        (cons-atom (car-atom $overlay) (provider-constraint:forget (cdr-atom $overlay) $key))))))

(= (provider-constraint:overlay-conflict $overlay $key $kind $name $expected)
  (if (== $overlay ()) ProviderConstraintValidV1
    (let (ProviderIndexOverlayV1 $other $value) (car-atom $overlay)
      (if (== $key $other) (provider-constraint:overlay-conflict (cdr-atom $overlay) $key $kind $name $expected)
        (case $value
          (((ProviderIndexPresentV1 $row $bytes)
            (if (== (car-atom $row) $kind)
              (case (provider-index:field $row $name)
                (((ProviderIndexFieldValueV1 $field)
                  (case (store:encode $field)
                    (((StoreEncoded $encoded)
                      (if (== $encoded $expected) (ProviderConstraintDuplicateV1 $other)
                        (provider-constraint:overlay-conflict (cdr-atom $overlay) $key $kind $name $expected)))
                     ($error $error))))
                 ($error $error)))
              (provider-constraint:overlay-conflict (cdr-atom $overlay) $key $kind $name $expected)))
           (ProviderIndexAbsentV1
             (provider-constraint:overlay-conflict (cdr-atom $overlay) $key $kind $name $expected))
           ($error (ProviderConstraintRefusedV1 overlay-shape))))))))

(= (provider-constraint:base-conflict $rows $overlay $key)
  (if (== $rows ()) ProviderConstraintValidV1
    (let* (($row (car-atom $rows)) ($other (provider-store:key $row)))
      (if (== $key $other) (provider-constraint:base-conflict (cdr-atom $rows) $overlay $key)
        (case (provider-index:overlay $overlay $other)
          ((ProviderIndexOverlayMissingV1 (ProviderConstraintDuplicateV1 $other))
           ($shadow (provider-constraint:base-conflict (cdr-atom $rows) $overlay $key))))))))

(= (provider-constraint:query-unique $view $epoch $revision $overlay $key $kind $name $value $cursor $page $reads)
  (case (provider-index:query-page $view $epoch $revision $kind $name $value $cursor $page)
    (((ProviderIndexPageV1 $rows $next $captured)
      (provider-constraint:query-unique-page $view $epoch $revision $overlay $key $kind $name $value
        $page $reads $rows $next (cdr-atom $captured)))
     ($error $error))))

; `reads` is a variable-length expression, so its tail is destructured below
; with cdr-atom rather than using a fixed-arity pattern.
(= (provider-constraint:query-unique-page $view $epoch $revision $overlay $key $kind $name $value
        $page $reads $rows $next $scopes)
  (case (provider-constraint:base-conflict $rows $overlay $key)
    ((ProviderConstraintValidV1
      (let $combined (provider-constraint:prepend $scopes $reads)
        (if (== $next ProviderIndexDoneV1) (ProviderConstraintReadsV1 $combined)
          (provider-constraint:query-unique $view $epoch $revision $overlay $key $kind $name $value $next $page $combined))))
     ($error $error))))

(= (provider-constraint:unique $view $epoch $revision $overlay $row $key $unique $page $reads)
  (case $unique
    ((ProviderConstraintNoUniqueV1 (ProviderConstraintReadsV1 $reads))
     ((ProviderConstraintUniqueV1 $name JsonNull) (ProviderConstraintReadsV1 $reads))
     ((ProviderConstraintUniqueV1 $name $value)
      (case (store:encode $value)
        (((StoreEncoded $encoded)
          (case (provider-constraint:overlay-conflict $overlay $key (car-atom $row) $name $encoded)
            ((ProviderConstraintValidV1
              (provider-constraint:query-unique $view $epoch $revision $overlay $key (car-atom $row)
                $name $value NoBound $page $reads))
             ($error $error))))
         ($error $error))))
     ($error (ProviderConstraintRefusedV1 unique-shape)))))

(= (provider-constraint:check-row $view $epoch $revision $overlay $row $key $page $reads)
  (case (provider-constraint:spec $row)
    (((ProviderConstraintSpecV1 $table $required $unique)
      (case (provider-constraint:not-null $table $required)
        ((ProviderConstraintValidV1
          (case (provider-constraint:unique $view $epoch $revision $overlay $row $key $unique $page $reads)
            (((ProviderConstraintDuplicateV1 $other)
              (ProviderConstraintViolationV1 unique $table $other))
             ($answer $answer))))
         ($error $error))))
     ($error $error))))

(= (provider-constraint:check $view $epoch $revision $remaining $overlay $page $reads)
  (if (== $remaining ()) (ProviderConstraintReadsV1 $reads)
    (case (car-atom $remaining)
      ((($verb $row)
        (case (provider-store:key $row)
          (((ProviderStoreRefused $why) (ProviderConstraintRefusedV1 $why))
           ($key
            (if (== $verb remove)
              (provider-constraint:next $view $epoch $revision $remaining $overlay $page $reads $key ProviderIndexAbsentV1)
              (if (or (== $verb add) (== $verb put))
                (case (provider-constraint:check-row $view $epoch $revision $overlay $row $key $page $reads)
                  (((ProviderConstraintReadsV1 $after)
                    (case (store:encode $row)
                      (((StoreEncoded $bytes)
                        (provider-constraint:next $view $epoch $revision $remaining $overlay $page $after $key
                          (ProviderIndexPresentV1 $row $bytes)))
                       ($error $error))))
                   ($error $error)))
                (ProviderConstraintRefusedV1 unknown-mutation)))))))
       ($other (ProviderConstraintRefusedV1 mutation-shape))))))

(= (provider-constraint:next $view $epoch $revision $remaining $overlay $page $reads $key $value)
  (let $updated (cons-atom (ProviderIndexOverlayV1 $key $value) (provider-constraint:forget $overlay $key))
    (provider-constraint:check $view $epoch $revision (cdr-atom $remaining) $updated $page $reads)))

(= (provider-constraint:plan-view $view $epoch $revision $mutations $page)
  (case (store:key-i64 $page)
    (((StoreKeyBytes $ignored)
      (if (> $page 0) (provider-constraint:plan-ready $view $epoch $revision $mutations $page)
        (ProviderConstraintRefusedV1 page-size)))
     ($error (ProviderConstraintRefusedV1 page-size)))))

(= (provider-constraint:plan-ready $view $epoch $revision $mutations $page)
  (case (provider-constraint:check $view $epoch $revision $mutations () $page ())
    (((ProviderConstraintReadsV1 $checked)
      (case (provider-index:plan-mutations $view $mutations)
        (((ProviderIndexDeltaV1 $operations $reads $writes)
          (let $combined (provider-constraint:prepend $checked (cdr-atom $reads))
            (ProviderConstraintDeltaV1 $operations (cons-atom reads $combined) $writes)))
         ($error $error))))
     ($error $error))))

(= (provider-constraint:commit-view $owner $view $epoch $revision $transition-reads $mutations $page)
  (if (== (get-metatype $transition-reads) Expression)
    (if (> (size-atom $transition-reads) 0)
      (if (== (car-atom $transition-reads) reads)
        (provider-constraint:commit-ready $owner $view $epoch $revision $transition-reads $mutations $page)
        (ProviderConstraintRefusedV1 transition-reads))
      (ProviderConstraintRefusedV1 transition-reads))
    (ProviderConstraintRefusedV1 transition-reads)))

(= (provider-constraint:commit-ready $owner $view $epoch $revision $transition-reads $mutations $page)
  (case (provider-constraint:plan-view $view $epoch $revision $mutations $page)
    (((ProviderConstraintDeltaV1 $operations $reads $writes)
      (let $combined (provider-constraint:prepend (cdr-atom $transition-reads) (cdr-atom $reads))
        (case (store:commit-scoped $owner $view (cons-atom reads $combined) $writes $operations)
          (((StoreSnapshot $updated $same-epoch $after)
            (let $closed (store:close $updated) (ProviderConstraintCommittedV1 $same-epoch $after)))
           ($error $error)))))
     ($error $error))))
