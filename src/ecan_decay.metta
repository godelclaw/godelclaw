; SPDX-License-Identifier: MIT
; Caller-owned decay and consolidation settings, in the same attention bank.
; Half-lives are seconds. Zero disables that horizon's decay. Defaults are
; starting hypotheses; the caller can replace them without a tuning gate.
; Retention over an elapsed interval, independent of model-call cadence.
(= (ecan:retention $seconds $half-life)
   (let $time-check (ecan:nonnegative retention elapsed-seconds $seconds)
     (if (== $time-check True)
       (let $half-check (ecan:nonnegative retention half-life $half-life)
         (if (== $half-check True)
           (if (== $half-life 0) 1 (pow-math 0.5 (/ $seconds $half-life))) $half-check))
       $time-check)))
; Calibrate from a desired retained fraction after an elapsed interval.
; Fraction 1 gives disabled decay; otherwise this uses the logarithmic inverse.
(= (ecan:half-life-for $seconds $fraction)
   (if (ecan:finite-number $seconds)
     (if (> $seconds 0)
       (if (ecan:finite-number $fraction)
         (if (and (> $fraction 0) (<= $fraction 1))
           (if (== $fraction 1) 0 (- 0 (/ $seconds (log-math 2 $fraction))))
           (ECANError half-life-for fraction "expected a finite fraction in (0, 1]" $fraction))
         (ECANError half-life-for fraction "expected a finite fraction in (0, 1]" $fraction))
       (ECANError half-life-for elapsed-seconds "expected a finite positive number" $seconds))
     (ECANError half-life-for elapsed-seconds "expected a finite positive number" $seconds)))
(= (ecan:decay-factors $bank $seconds)
   (let $s (ecan:retention $seconds (ecan:setting $bank sti-half-life))
     (case $s
       (((ECANError $op $field $reason $value) $s)
        ($sr (let $m (ecan:retention $seconds (ecan:setting $bank mti-half-life))
          (case $m
            (((ECANError $op $field $reason $value) $m)
             ($mr (let $l (ecan:retention $seconds (ecan:setting $bank lti-half-life))
               (case $l
                 (((ECANError $op $field $reason $value) $l)
                  ($lr (ECANRetention $sr $mr $lr))))))))))))))
; Preview leaves attention and settings untouched.
(= (ecan:preview $bank $id $seconds)
   (let $factors (ecan:decay-factors $bank $seconds)
     (case $factors
       (((ECANRetention $sr $mr $lr)
         (let* (((ECANValue $id $sti $mti $lti) (ecan:current $bank $id))
                ($s (* $sti $sr)) ($m (* $mti $mr)) ($l (* $lti $lr)))
           (ECANValue $id $s $m $l))) ($error $error)))))
(= (ecan:advance $bank $seconds)
   (let $factors (ecan:decay-factors $bank $seconds)
     (case $factors
       (((ECANRetention $s $m $l) (ecan:decay $bank $s $m $l)) ($error $error)))))
; The caller reports use here; get/focus/preview do not stimulate anything.
(= (ecan:use $bank $id $amount)
   (let* (($sm (ecan:setting $bank sti-mti-rate))
          ($ml (ecan:setting $bank mti-lti-rate))
          ($check (ecan:validate-consolidation $sm $ml)))
     (if (== $check True)
       (let $_ (ecan:stimulate $bank $id $amount)
         (ecan:consolidate $bank $id $sm $ml)) $check)))
