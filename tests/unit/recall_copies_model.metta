; Recall's copy handling against the RecallProvenance model, on seeded random
; rankings. The model keeps the first candidate of each text from a best-first
; ranking; each kept slot carries its group's oldest copy (by time, then id)
; and best cosine; a fetch that yields k distinct texts, or exhausts the
; ranking, answers what the whole ranking answers (refetch_exact). The model
; is stated here independently of src/attention_memory_cetta.metta.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/attention_memory_cetta.metta)
!(import! &self (library list))

; A seeded linear congruential generator: (value next-state).
(= (rc:next $s) (% (+ (* 1103515245 $s) 12345) 2147483648))
; Draws use the high bits: an LCG's low bits cycle with short periods.
(= (rc:below $s $n) (% (math:floor (/ (rc:next $s) 65536)) $n))
(= (rc:pick $s $items) (list:at $items (rc:below $s (size-atom $items))))

; Lexicographic order on strings, by code point.
(= (rc:less? $a $b) (rc:less-points? (str:python-codepoints $a) (str:python-codepoints $b)))
(= (rc:less-points? $a $b)
   (if (== $b ()) False
     (if (== $a ()) True
       (if (< (car-atom $a) (car-atom $b)) True
         (if (> (car-atom $a) (car-atom $b)) False (rc:less-points? (cdr-atom $a) (cdr-atom $b)))))))

; A row is (cos id text time).
(= (rc:key $row) (let ($cos $id $text $time) $row (str:concat $time (str:concat " " $id))))
(= (rc:text $row) (let ($cos $id $text $time) $row $text))
; Groups: (text best key representative), first appearance first.
(= (rc:groups $rows $groups) (if (== $rows ()) $groups (rc:groups (cdr-atom $rows) (rc:join (car-atom $rows) $groups))))
(= (rc:join $row $groups)
   (let ($cos $id $text $time) $row
     (if (== $groups ()) (($text $cos (rc:key $row) $id))
       (let ($gtext $best $gkey $rep) (car-atom $groups)
         (if (== $gtext $text)
           (cons-atom ($gtext (max $best $cos) (if (rc:less? (rc:key $row) $gkey) (rc:key $row) $gkey)
                                (if (rc:less? (rc:key $row) $gkey) $id $rep))
                      (cdr-atom $groups))
           (cons-atom (car-atom $groups) (rc:join $row (cdr-atom $groups))))))))
(= (rc:group-of $text $groups)
   (let ($gtext $best $gkey $rep) (car-atom $groups) (if (== $gtext $text) ($best $rep) (rc:group-of $text (cdr-atom $groups)))))
; The model: one slot per text in ranking order, up to count (0: all).
(= (rc:model $rows $count) (rc:keep $rows (rc:groups $rows ()) () () $count))
(= (rc:keep $rows $groups $seen $kept $count)
   (if (or (== $rows ()) (and (> $count 0) (== (size-atom $kept) $count))) (list:reverse $kept)
     (let $text (rc:text (car-atom $rows))
       (if (rc:member? $text $seen) (rc:keep (cdr-atom $rows) $groups $seen $kept $count)
         (rc:keep (cdr-atom $rows) $groups (cons-atom $text $seen) (cons-atom (rc:group-of $text $groups) $kept) $count)))))
(= (rc:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (rc:member? $x (cdr-atom $xs)))))
(= (rc:distinct $rows) (size-atom (rc:model $rows 0)))

; The runtime's answer, as (cos id) pairs.
(= (rc:runtime $rows $count)
   (map-atom (am:distinct-rows (map-atom $rows $row (let ($cos $id $text $time) $row
                                  ($cos (MemoryInfo $id 1 $text (("time" $time))))))
                               $count)
     $slot (let ($cos (MemoryInfo $id $n $text $meta)) $slot ($cos $id))))
(= (rc:texts-of $pairs $rows) (map-atom $pairs $pair (let ($best $id) $pair (rc:text-of $id $rows))))
(= (rc:text-of $id $rows) (let ($cos $rid $text $time) (car-atom $rows) (if (== $rid $id) $text (rc:text-of $id (cdr-atom $rows)))))

; One random case from state $s: (rows count prefix next-state).
(= (rc:case $s)
   (let* (($s1 (rc:next $s)) ($size (rc:below $s1 10))
          (($cosines $s2) (rc:draw-cosines $s1 $size ()))
          (($rows $s3) (rc:draw-rows $s2 (list:sort-by rc:greater-cos? $cosines) 0 ()))
          ($s4 (rc:next $s3)) ($count (rc:below $s4 5))
          ($s5 (rc:next $s4)) ($prefix (rc:below $s5 (+ $size 1))))
     ($rows $count $prefix $s5)))
(= (rc:greater-cos? $a $b) (>= $a $b))
(= (rc:draw-cosines $s $n $acc)
   (if (== $n 0) ($acc $s) (let $t (rc:next $s) (rc:draw-cosines $t (- $n 1) (cons-atom (rc:pick $t (0.25 0.5 0.75 1.0)) $acc)))))
(= (rc:draw-rows $s $cosines $i $acc)
   (if (== $cosines ()) ((list:reverse $acc) $s)
     (let* (($t (rc:next $s)) ($u (rc:next $t)))
       (rc:draw-rows $u (cdr-atom $cosines) (+ $i 1)
         (cons-atom ((car-atom $cosines) (str:concat "m" (repr $i)) (rc:pick $t ("a" "b" "c" "d"))
                     (rc:pick $u ("2026-08-01 10:00:00" "2026-08-01 10:00:05" "2026-09-02 08:00:00")))
                    $acc)))))
; Check n cases; the number that disagree.
(= (rc:check $s $n $bad)
   (if (== $n 0) $bad
     (let ($rows $count $prefix $next) (rc:case $s)
       (let* (($part (list:take $prefix $rows))
              ($agrees (and (== (rc:runtime $rows $count) (rc:model $rows $count))
                            (== (rc:runtime $part $count) (rc:model $part $count))))
              ; refetch_exact: enough distinct texts in the prefix, or all of it.
              ($exact (if (and (> $count 0) (or (>= (rc:distinct $part) $count) (== $prefix (size-atom $rows))))
                        (== (rc:texts-of (rc:model $part $count) $rows) (rc:texts-of (rc:model $rows $count) $rows))
                        True)))
         (rc:check $next (- $n 1) (if (and $agrees $exact) $bad (cons-atom ($rows $count $prefix) $bad)))))))

!(let* (($disagreeing (rc:check 20261105 60 ()))
         ($_ (if (== $disagreeing ()) True (println! (DISAGREEING $disagreeing))))
         ($_ (nt:require distinct-rows-match-the-model-and-its-prefix-equation (== $disagreeing ())))
         ; The model itself, on a fixed ranking: oldest copy, best cosine, one slot per text.
         ($_ (nt:require the-model-keeps-the-oldest-copy-with-the-best-cosine
               (== (rc:model ((1.0 "m0" "a" "2026-09-02 08:00:00") (0.75 "m1" "b" "2026-08-01 10:00:00")
                              (0.5 "m2" "a" "2026-08-01 10:00:05")) 0)
                   ((1.0 "m2") (0.75 "m1"))))))
   (println! UNIT_RECALL_COPIES_MODEL_OK))
