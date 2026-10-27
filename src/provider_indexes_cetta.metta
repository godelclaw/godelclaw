; Provider index schema and key construction are MeTTa programs. Hash buckets
; locate candidate rows only: readers must recheck full values and capture
; both the bucket and every inspected primary row as read dependencies.
!(import! &self ./provider_store_cetta.metta)

(= (provider-index:positions $kind)
  (case $kind
    ((ProviderRequestV1 (("id" 1) ("status" 5) ("provider" 7) ("mode" 8) ("owner" 10)))
     (ProviderCooldownV1 (("provider" 1)))
     (ProviderCallV1 (("id" 1) ("epoch" 3) ("state" 8) ("activity" 16) ("lane" 17)
                      ("purpose" 18) ("notified" 19)))
     (ProviderEventV1 (("call_id" 1) ("event" 2)))
     (ProviderAdmissionRecoveryV1 (("id" 1)))
     ($other ()))))

; Bind stored data structurally. Evaluating a projected Boolean as an
; intermediate computation can turn it into the dialect's truth symbol.
(= (provider-index:fields $row)
  (case $row
    (((ProviderRequestV1 $id $ordinal $started $reserved $status $output $provider $mode $finish $owner $fingerprint)
      ((ProviderIndexFieldV1 "id" $id) (ProviderIndexFieldV1 "status" $status)
       (ProviderIndexFieldV1 "provider" $provider) (ProviderIndexFieldV1 "mode" $mode)
       (ProviderIndexFieldV1 "owner" $owner)))
     ((ProviderCooldownV1 $provider $ordinal $until)
      ((ProviderIndexFieldV1 "provider" $provider)))
     ((ProviderCallV1 $id $ordinal $epoch $pid $start $created $updated $state $guard $transport
                     $elapsed $bytes $quiet $result $reason $activity $lane $purpose $notified)
      ((ProviderIndexFieldV1 "id" $id) (ProviderIndexFieldV1 "epoch" $epoch)
       (ProviderIndexFieldV1 "state" $state) (ProviderIndexFieldV1 "activity" $activity)
       (ProviderIndexFieldV1 "lane" $lane) (ProviderIndexFieldV1 "purpose" $purpose)
       (ProviderIndexFieldV1 "notified" $notified)))
     ((ProviderEventV1 $id $event $sequence $observed $detail)
      ((ProviderIndexFieldV1 "identity" (ProviderEventIdentityV1 $id $event))
       (ProviderIndexFieldV1 "call_id" $id) (ProviderIndexFieldV1 "event" $event)))
     ((ProviderAdmissionRecoveryV1 $id $ordinal $recovered $reason $cutoff)
      ((ProviderIndexFieldV1 "id" $id)))
     ($other ()))))

(= (provider-index:find-field $fields $name)
  (if (== $fields ()) ProviderIndexFieldMissingV1
    (let (ProviderIndexFieldV1 $column $value) (car-atom $fields)
      (if (== $name $column) (ProviderIndexFieldValueV1 $value)
        (provider-index:find-field (cdr-atom $fields) $name)))))

(= (provider-index:field $row $name)
  (case (provider-store:key $row)
    (((ProviderStoreRefused $why) (ProviderIndexRefusedV1 $why))
     ($key (provider-index:find-field (provider-index:fields $row) $name)))))

(= (provider-index:known-column? $kind $name)
  (if (== $kind ProviderEventV1)
    (if (== $name "identity") True (provider-index:position-known? (provider-index:positions $kind) $name))
    (provider-index:position-known? (provider-index:positions $kind) $name)))
(= (provider-index:position-known? $positions $name)
  (if (== $positions ()) False
    (let ($column $position) (car-atom $positions)
      (if (== $column $name) True (provider-index:position-known? (cdr-atom $positions) $name)))))

(= (provider-index:prefix $kind $name $value)
  (if (provider-index:known-column? $kind $name)
    (case (store:encode $value)
      (((StoreEncoded $bytes)
        (let* (($table (provider-store:prefix $kind)) ($digest (str:sha256 $bytes))
               ($prefix (str:join "" ("index/v1/" $table $name "/" $digest "/"))))
          (case (store:key-i64 9223372036854775807)
            (((StoreKeyBytes $last)
              (case (store:key-i64 -9223372036854775808)
                (((StoreKeyBytes $zeroes)
                  (let* (($through-last (str:concat $prefix $last))
                         ($zero (str:byte-slice $zeroes 0 1)))
                    (ProviderIndexRangeV1 $prefix (str:concat $through-last $zero))))
                 ($error $error))))
             ($error $error)))))
       ($error $error)))
    (ProviderIndexRefusedV1 unknown-column)))

(= (provider-index:entries-fields $fields $kind $suffix $reference $reverse)
  (if (== $fields ())
    (ProviderIndexEntriesV1 (provider-store:reverse $reverse ()))
    (let (ProviderIndexFieldV1 $name $value) (car-atom $fields)
      (case (provider-index:prefix $kind $name $value)
        (((ProviderIndexRangeV1 $prefix $upper)
          (let* (($key (str:concat $prefix $suffix))
                 ($next (cons-atom (ProviderIndexEntryV1 $key $reference) $reverse)))
            (provider-index:entries-fields (cdr-atom $fields) $kind $suffix $reference $next)))
         ($error $error))))))

(= (provider-index:entries $row)
  (case (provider-store:key $row)
    (((ProviderStoreRefused $why) (ProviderIndexRefusedV1 $why))
     ($primary
       (provider-index:entries-selected $row (provider-index:fields $row))))))

(= (provider-index:entries-selected $row $fields)
  (let* (($kind (car-atom $row)) ($primary (provider-store:key $row)))
    (if (== $fields ()) (ProviderIndexEntriesV1 ())
      (let $ordinal (if (== $kind ProviderEventV1) (index-atom $row 3) (index-atom $row 2))
        (case (store:key-i64 $ordinal)
          (((StoreKeyBytes $suffix)
            (case (store:encode (ProviderIndexV1 $primary))
              (((StoreEncoded $reference)
                (provider-index:entries-fields $fields $kind $suffix $reference ()))
               ($error $error))))
           ($error $error)))))))

; String equality is byte-exact. Other values use their complete CAV1 kind
; and representation, so 1/1.0, null/text and signed zero never merge here.
(= (provider-index:same-value $old $new)
  (if (== (get-type $old) String)
    (if (== (get-type $new) String) (if (== $old $new) ProviderIndexSameV1 ProviderIndexDifferentV1)
      ProviderIndexDifferentV1)
    (case (store:encode $old)
      (((StoreEncoded $before)
        (case (store:encode $new)
          (((StoreEncoded $after) (if (== $before $after) ProviderIndexSameV1 ProviderIndexDifferentV1))
           ($error $error))))
       ($error $error)))))

(= (provider-index:changed-fields $before $after $old $new)
  (if (== $before ())
    (ProviderIndexFieldChangesV1 (provider-store:reverse $old ())
      (provider-index:append (provider-store:reverse $new ()) $after))
    (if (== $after ())
      (ProviderIndexFieldChangesV1 (provider-index:append (provider-store:reverse $old ()) $before)
        (provider-store:reverse $new ()))
      (let* (((ProviderIndexFieldV1 $old-name $old-value) (car-atom $before))
             ((ProviderIndexFieldV1 $new-name $new-value) (car-atom $after)))
        (if (== $old-name $new-name)
          (case (provider-index:same-value $old-value $new-value)
            ((ProviderIndexSameV1 (provider-index:changed-fields (cdr-atom $before) (cdr-atom $after) $old $new))
             (ProviderIndexDifferentV1
               (let* (($old-next (cons-atom (car-atom $before) $old))
                      ($new-next (cons-atom (car-atom $after) $new)))
                 (provider-index:changed-fields (cdr-atom $before) (cdr-atom $after) $old-next $new-next)))
             ($error $error)))
          (ProviderIndexFieldChangesV1 (provider-index:append (provider-store:reverse $old ()) $before)
            (provider-index:append (provider-store:reverse $new ()) $after)))))))

(= (provider-index:append $first $second)
  (if (== $first ()) $second
    (let $tail (provider-index:append (cdr-atom $first) $second)
      (cons-atom (car-atom $first) $tail))))

; Qualification entry. This module neither selects a live backend nor writes.
(= (provider-index:call (Entries $row)) (provider-index:entries $row))
(= (provider-index:call (Prefix $kind $name $value)) (provider-index:prefix $kind $name $value))
(= (provider-index:call (Field $row $name)) (provider-index:field $row $name))
