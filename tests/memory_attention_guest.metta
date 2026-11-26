; Imported by memory guests (tests/memory_attention.metta): the attention
; transport over four memories, computed in exact rationals, against the
; engine's am:rounds on random link graphs. Five rounds with restart 1/4 from
; "a": each node sends 3/4 of its mass along its links in proportion to their
; weights, or keeps it when it has none.
!(import! &self (library math))

; Exact rationals (Q numerator denominator), reduced, denominator positive.
(= (q:gcd $a $b) (if (== $b 0) $a (let (IntegerDivmodV1 $q $r) (math:integer-divmod $a $b) (q:gcd $b $r))))
(= (q:make $n $d)
   (let $g (q:gcd $n $d)
     (let (IntegerDivmodV1 $nn $r1) (math:integer-divmod $n $g)
       (let (IntegerDivmodV1 $dd $r2) (math:integer-divmod $d $g) (Q $nn $dd)))))
(= (q:add (Q $a $b) (Q $c $d)) (q:make (+ (* $a $d) (* $c $b)) (* $b $d)))
(= (q:mul (Q $a $b) (Q $c $d)) (q:make (* $a $c) (* $b $d)))
(= (q:float (Q $a $b)) (/ $a $b))

(= (ox:ids) ("a" "b" "c" "d"))
(= (ox:sum $row) (if (== $row ()) 0 (+ (car-atom $row) (ox:sum (cdr-atom $row)))))
; $y with $v added at index $j.
(= (ox:add-at $y $j $v)
   (if (== $j 0) (cons-atom (q:add (car-atom $y) $v) (cdr-atom $y))
     (cons-atom (car-atom $y) (ox:add-at (cdr-atom $y) (- $j 1) $v))))
(= (ox:send-row $y $row $total $w $j)
   (if (== $j 4) $y
     (ox:send-row (ox:add-at $y $j (q:mul $w (q:make (index-atom $row $j) $total))) $row $total $w (+ $j 1))))
(= (ox:send $y $xi $row $i)
   (let* (($total (ox:sum $row)) ($w (q:mul (Q 3 4) $xi)))
     (if (== $total 0) (ox:add-at $y $i $w) (ox:send-row $y $row $total $w 0))))
(= (ox:step-rows $x $edges $i $y)
   (if (== $i 4) $y (ox:step-rows $x $edges (+ $i 1) (ox:send $y (index-atom $x $i) (index-atom $edges $i) $i))))
(= (ox:steps $x $edges $n)
   (if (== $n 0) $x (ox:steps (ox:step-rows $x $edges 0 ((Q 1 4) (Q 0 1) (Q 0 1) (Q 0 1))) $edges (- $n 1))))

; Pseudo-random link weights 0..3: a 64-bit LCG's high bits.
(= (ox:next $s)
   (let (IntegerDivmodV1 $q $r) (math:integer-divmod (+ (* 6364136223846793005 $s) 1442695040888963407) 18446744073709551616) $r))
(= (ox:weight $s)
   (let (IntegerDivmodV1 $high $low) (math:integer-divmod $s 8589934592)
     (let (IntegerDivmodV1 $q $w) (math:integer-divmod $high 4) $w)))
(= (ox:row $s $n) (if (== $n 0) (() $s) (let $t (ox:next $s) (let ($rest $u) (ox:row $t (- $n 1)) ((cons-atom (ox:weight $t) $rest) $u)))))
(= (ox:graph $s $n) (if (== $n 0) (() $s) (let ($row $t) (ox:row $s 4) (let ($rest $u) (ox:graph $t (- $n 1)) ((cons-atom $row $rest) $u)))))

(= (ox:link-row $src $row $j)
   (if (== $j 4) True
     (let $_ (attention-link $src (index-atom (ox:ids) $j) (index-atom $row $j)) (ox:link-row $src $row (+ $j 1)))))
(= (ox:link-all $edges $i)
   (if (== $i 4) True (let $_ (ox:link-row (index-atom (ox:ids) $i) (index-atom $edges $i) 0) (ox:link-all $edges (+ $i 1)))))
(= (ox:mass $id $masses)
   (if (== $masses ()) 0
     (let (AttentionMass $k $v) (car-atom $masses) (if (== $k $id) $v (ox:mass $id (cdr-atom $masses))))))
(= (ox:near $x $y) (< (math:abs (- $x $y)) 0.000000000001))
(= (ox:agrees $masses $expected $j)
   (if (== $j 4) True
     (if (ox:near (ox:mass (index-atom (ox:ids) $j) $masses) (q:float (index-atom $expected $j)))
         (ox:agrees $masses $expected (+ $j 1))
         (let $_ (println! (TRANSPORT_DIFFERS (index-atom (ox:ids) $j) $masses $expected)) False))))
(= (ox:total $masses) (if (== $masses ()) 0 (let (AttentionMass $k $v) (car-atom $masses) (+ $v (ox:total (cdr-atom $masses))))))
; The number of trials, of $n, whose transport matches the oracle.
(= (ox:trials $s $n $passed)
   (if (== $n 0) $passed
     (let* ((($edges $t) (ox:graph $s 4))
            ($_ (ox:link-all $edges 0))
            ($masses (am:rounds (am:bank) ((AttentionMass "a" 1)) ((AttentionMass "a" 1)) 5 0.25))
            ($expected (ox:steps ((Q 1 1) (Q 0 1) (Q 0 1) (Q 0 1)) $edges 5))
            ($ok (and (ox:agrees $masses $expected 0) (ox:near (ox:total $masses) 1))))
       (ox:trials $t (- $n 1) (if $ok (+ $passed 1) $passed)))))

; The (sti mti lti) of $id among attention-focus entries (score (ECANValue ...)).
(= (ox:ecan $id $focus)
   (if (== $focus ()) (Missing $id)
     (let ($score $value) (car-atom $focus)
       (case $value
         (((ECANValue $key $s $m $l) (if (== $key $id) ($s $m $l) (ox:ecan $id (cdr-atom $focus))))
          ($other (ox:ecan $id (cdr-atom $focus))))))))
(= (ox:close3 ($a $b $c) ($x $y $z) $tolerance)
   (and (<= (math:abs (- $a $x)) $tolerance) (and (<= (math:abs (- $b $y)) $tolerance) (<= (math:abs (- $c $z)) $tolerance))))
(= (ox:close3 (Missing $id) $want $tolerance) False)

; Five-place agreement, as the studies' reference arithmetic asks.
(= (ox:near5 $x $y) (< (math:abs (- $x $y)) 0.00001))
; The score of $id among (score id) pairs.
(= (ox:score $id $pairs)
   (if (== $pairs ()) (Missing $id)
     (let ($s $k) (car-atom $pairs) (if (== $k $id) $s (ox:score $id (cdr-atom $pairs))))))
(= (ox:scores-sum $pairs) (if (== $pairs ()) 0 (let ($s $k) (car-atom $pairs) (+ $s (ox:scores-sum (cdr-atom $pairs))))))
; Every subexpression of $atom headed by $head, in order.
(= (ox:heads $head $atom)
   (if (not (== (get-metatype $atom) Expression)) ()
     (if (== $atom ()) ()
       (let $inner (ox:heads-all $head $atom)
         (if (== (car-atom $atom) $head) (cons-atom $atom $inner) $inner)))))
(= (ox:heads-all $head $items)
   (if (== $items ()) () (ox:append (ox:heads $head (car-atom $items)) (ox:heads-all $head (cdr-atom $items)))))
(= (ox:append $a $b) (if (== $a ()) $b (cons-atom (car-atom $a) (ox:append (cdr-atom $a) $b))))
(= (ox:mass-of $id $masses)
   (if (== $masses ()) (Missing $id)
     (let (AttentionMass $k $v) (car-atom $masses) (if (== $k $id) $v (ox:mass-of $id (cdr-atom $masses))))))
; A pseudo-random number in [0, 1) and the next state.
(= (ox:unit $s) (let $t (ox:next $s) ((/ $t 18446744073709551616) $t)))
; The number of $n random bounded Hebbian updates within [0, 1] that equal
; (1 - rate) old + rate x y.
(= (ox:hebb-rows $s $n $passed)
   (if (== $n 0) $passed
     (let* ((($old $s1) (ox:unit $s)) (($x $s2) (ox:unit $s1)) (($y $s3) (ox:unit $s2)) (($rate $s4) (ox:unit $s3))
            ($v (study:bounded-hebb $old $x $y $rate))
            ($ok (and (>= $v 0) (and (<= $v 1) (ox:near5 $v (+ (* (- 1 $rate) $old) (* $rate (* $x $y))))))))
       (ox:hebb-rows $s4 (- $n 1) (if $ok (+ $passed 1) $passed)))))
