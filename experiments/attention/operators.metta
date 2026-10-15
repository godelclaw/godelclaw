; SPDX-License-Identifier: MIT
; Offline reference operators, imported only by synthetic experiments.
; Domains: nonnegative ages/decay/time, positive resolution/temperature,
; nonnegative integer rounds, and x,y,old,rate in [0,1].
(= (study:actr-sum $now $times $decay $resolution)
   (if (== $times ()) 0
     (+ (pow-math (max $resolution (- $now (car-atom $times))) (- 0 $decay))
        (study:actr-sum $now (cdr-atom $times) $decay $resolution))))
(= (study:bounded-hebb $old $x $y $rate)
   (+ (* (- 1 $rate) $old) (* $rate (* $x $y))))
(= (study:softmax $scores $temperature)
   (let* (($maximum (study:max-score $scores))
          ($rows (study:exponents $scores $maximum $temperature))
          ($sum (study:sum $rows)))
     (study:normalize $rows $sum)))
(= (study:max-score $rows)
   (if (== $rows ()) 0
     (let ($score $id) (car-atom $rows)
       (if (== (cdr-atom $rows) ()) $score
         (max $score (study:max-score (cdr-atom $rows)))))))
(= (study:exponents $rows $maximum $temperature)
   (if (== $rows ()) ()
     (let ($score $id) (car-atom $rows)
       (cons-atom ((pow-math 2.718281828459045 (/ (- $score $maximum) $temperature)) $id)
         (study:exponents (cdr-atom $rows) $maximum $temperature)))))
(= (study:sum $rows)
   (if (== $rows ()) 0
     (let ($score $id) (car-atom $rows) (+ $score (study:sum (cdr-atom $rows))))))
(= (study:normalize $rows $total)
   (if (== $rows ()) ()
     (let ($score $id) (car-atom $rows)
       (cons-atom ((/ $score $total) $id) (study:normalize (cdr-atom $rows) $total)))))

; Minimal focus stickiness, not a Hopfield or hippocampal model. The reset
; is just an empty previous set. A wrong previous set can capture the focus.
(= (study:focus $scores $previous $boost)
   (if (== $scores ()) ()
     (let* ((($score $id) (car-atom $scores))
            ($next (+ $score (if (memory:member $id $previous) $boost 0))))
       (cons-atom ($next $id) (study:focus (cdr-atom $scores) $previous $boost)))))

; e^{-t} sum_{j=0}^rounds t^j/j! s P^j, using the same native graph
; transport as PPR. Report the missing tail rather than silently renormalize.
(= (study:heat $bank $seeds $time $rounds)
   (let* (($sum (new-space)) ($weight (pow-math 2.718281828459045 (- 0 $time)))
          ($_ (am:copy $seeds $sum $weight))
          ($_ (study:heat-loop $bank $seeds $time $rounds 0 $weight $sum))
          ($rows (am:mass-rows $sum)))
     (StudyHeat $rows (MissingMass (max 0 (- 1 (am:sum-mass $rows)))))))
(= (study:heat-loop $bank $current $time $left $step $weight $sum)
   (if (== $left 0) True
     (let* (($next (am:rounds $bank $current $current 1 0))
            ($index (+ $step 1)) ($next-weight (* $weight (/ $time $index)))
            ($_ (am:copy $next $sum $next-weight)))
       (study:heat-loop $bank $next $time (- $left 1) $index $next-weight $sum))))
