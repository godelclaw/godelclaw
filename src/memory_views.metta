; SPDX-License-Identifier: MIT
; Composable, inert retrieval predicates. A view never moves source records.
; Masks select before top-k and deduplicate overlapping memberships by row.
!(import! &self (library lib_memory_collection))

(= (view:matches $meta $filter)
   (case $filter
     ((All True)
      ((Eq $key $value) (memory:filter $meta (Eq $key $value)))
      ((Exists $key) (not (== (memory:field $meta $key) ())))
      ((Not $part) (not (view:matches $meta $part)))
      ((And $parts) (view:every $meta $parts))
      ((Or $parts) (view:any $meta $parts))
      ($other False))))
(= (view:every $meta $parts)
   (if (== $parts ()) True
     (and (view:matches $meta (car-atom $parts)) (view:every $meta (cdr-atom $parts)))))
(= (view:any $meta $parts)
   (if (== $parts ()) False
     (or (view:matches $meta (car-atom $parts)) (view:any $meta (cdr-atom $parts)))))
(= (view:allows $filter $id)
   (case (collapse (memory:record $id))
     ((((Memory $i $r $p $v $d $meta)) (view:matches $meta $filter)) ($other False))))

(= (view:mask $stamp $index $filter)
   (let (RowIndex $built $count $fields $masks $capacity) $index
     (if (not (== $stamp $built)) (IndexFailure stale-view)
       (case $filter
         (((Exists $key)
           (tensor:mask-indices $count
             (tensor:indices (unique-atom (collapse (match $fields (Field $key $value $row) $row))))))
          ((Not $part) (tensor:mask-not (view:mask $stamp $index $part)))
          ((And $parts) (view:mask-fold $stamp $index $parts (tensor:full u8 ($count) 1) And))
          ((Or $parts) (view:mask-fold $stamp $index $parts (tensor:full u8 ($count) 0) Or))
          ($positive (index:mask $stamp $index $positive)))))))
(= (view:mask-fold $stamp $index $parts $acc $kind)
   (if (== $parts ()) $acc
     (let* (($next (view:mask $stamp $index (car-atom $parts)))
            ($merged (if (== $kind And) (tensor:mask-and $acc $next) (tensor:mask-or $acc $next))))
       (view:mask-fold $stamp $index (cdr-atom $parts) $merged $kind))))
(= (view:query-packed $vector $count $filter (Packed $stamp $rows $n $matrix $index $live $screen))
   (case (view:mask $stamp $index $filter)
     (((IndexFailure $why) (MemoryFailure filter $why))
      ((TensorFailure $why) (MemoryFailure filter $why))
      ($selected (case (tensor:nonzero (tensor:mask-and $live $selected))
        (((TensorFailure $why) (MemoryFailure filter $why))
         ($indices (memory:query-tier $rows $matrix $screen $vector $count $indices))))))))
(= (view:query $profile $vector $count $filter)
   (if (== $filter All) (memory:dispatch (MemoryQuery $profile $vector $count All))
     (case (get-state &memory-profile)
       (((Profile $profile $dim)
         (if (== (tensor:shape $vector) ($dim)) (view:query-indexed $vector $count $filter)
           (MemoryFailure query dimensions)))
        ($other (MemoryFailure query profile-mismatch))))))
(= (view:query-indexed $vector $count $filter)
   (let $index (case (get-state &memory-index)
                 ((Missing (memory:build-index (car-atom (tensor:shape $vector)))) ($ready $ready)))
     (case $index
       (((MemoryFailure $at $why) $index)
        ($packed (case (view:query-packed $vector $count $filter $packed)
          (((MemoryFailure $at $why) (MemoryFailure $at $why))
           ($hits (if (== (collapse (get-atoms &memory-dirty)) ()) $hits
             (let $delta (case (get-state &memory-delta-index)
                           ((Missing (memory:build-delta (car-atom (tensor:shape $vector)))) ($ready $ready)))
               (case $delta
                 (((MemoryFailure $at $why) $delta)
                  ($packed (case (view:query-packed $vector $count $filter $packed)
                    (((MemoryFailure $at $why) (MemoryFailure $at $why))
                     ($recent (list:sort-by memory:precedes
                       (retain-top-k-keyed-atom (list:append $hits $recent) $count))))))))))))))))))
