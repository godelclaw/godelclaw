; Project 04b qualification candidate ONLY. Not imported by runtime callers.
; JSON Python-float emission blocks activating the native writers. This file
; has no filesystem, clock, bridge or writer dependency and cannot persist state.
; A pair is (sti lti). Preserve goals.py's double operation order, including
; consolidation BEFORE budget scaling. Reviewer Python 3.12 sum uses a
; compensated double accumulator, NOT the naive left fold (see bit witness).
!(import! &self (library math))

(= (gka:decay $pair)
   (let* ((($sti $lti) $pair)
          ($next (* $sti 0.95)))
     ($next (+ $lti (* 0.05 (math:max 0.0 (- $next $lti)))))))
(= (gka:total $pairs $total)
   (gka:sum $pairs $total 0.0))
; Finite nonnegative goal STI only (the oracle parser clamps to [0, 1]).
; This is NOT a generic implementation of Python sum for arbitrary objects.
(= (gka:sum $pairs $hi $lo)
   (if (== $pairs ()) (+ $hi $lo)
     (let* ((($sti $_) (car-atom $pairs)) ($next (+ $hi $sti))
            ($error (if (>= $hi $sti) (+ (- $hi $next) $sti)
                                         (+ (- $sti $next) $hi))))
       (gka:sum (cdr-atom $pairs) $next (+ $lo $error)))))
(= (gka:advance $pairs $budget)
   (let* (($decayed (map-atom $pairs $pair (gka:decay $pair)))
          ($total (gka:total $decayed 0)))
     (if (and (> $total $budget) (> $budget 0))
       (let $scale (/ $budget $total)
         (map-atom $decayed $pair
           (let ($sti $lti) $pair ((* $sti $scale) $lti))))
       $decayed)))

; Mood vectors are in MOOD_DIMS order. Missing dimensions retain their previous
; value but still undergo GAMMA*x + (1-GAMMA)*x, just as the oracle does.
(= (gka:seed $sample)
   (map-atom $sample $value (if (== $value GkaAbsent) 0.0 $value)))
(= (gka:mood $old $sample)
   (if (== $old ()) ()
     (let* (($previous (car-atom $old))
            ($value (car-atom $sample))
            ($observed (if (== $value GkaAbsent) $previous $value)))
       (cons-atom (+ (* 0.69 $previous) (* (- 1 0.69) $observed))
         (gka:mood (cdr-atom $old) (cdr-atom $sample))))))

; Replay of the DIFFERENT Nat micro-unit model, NOT the goal kernel's decay.
(= (gka:model-decay-micro $s)
   (let $product (* $s 950000)
     (math:floor (/ (- $product (% $product 1000000)) 1000000))))
