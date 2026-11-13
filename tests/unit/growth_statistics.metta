; Unit tests of the growth gate's statistics (tests/growth_support.metta).
!(import! &self ../native_test_support.metta)
!(import! &self ../growth_support.metta)

; The middle of three, in every order.
!(nt:require median-of-three
   (nt:equal (collapse (let ($a $b $c) (superpose ((1 2 3) (1 3 2) (2 1 3) (2 3 1) (3 1 2) (3 2 1)))
                         (growth:median3 $a $b $c)))
             (2 2 2 2 2 2)))
!(nt:require median-with-ties (nt:equal (growth:median3 5 5 1) 5))
; One run whose resident peak read low does not move the memory statistic;
; time still keeps the fastest run.
!(nt:require low-memory-reading-discarded
   (nt:equal (growth:statistic (3000 4600) (1000 4088) (2000 4576)) (1000 4576)))
!(nt:require high-memory-reading-discarded
   (nt:equal (growth:statistic (2000 4576) (3000 9000) (1000 4512)) (1000 4576)))
; A step breaks the time bound, the memory bound, or either.
!(let $bounds (growth:linear)
   (let* (($_ (nt:require time-step-broken
                (nt:equal (collapse (let $d (superpose (time memory either))
                                      (growth:breaks? $d (1 1000000 1000) (4 40000000 4000) $bounds)))
                          (True False True))))
          ($_ (nt:require memory-step-broken
                (nt:equal (collapse (let $d (superpose (time memory either))
                                      (growth:breaks? $d (1 1000000 1000) (4 4000000 6049) $bounds)))
                          (False True True)))))
     (nt:require bounds-met
       (nt:equal (collapse (let $d (superpose (time memory either))
                             (growth:breaks? $d (1 1000000 1000) (4 8000000 6048) $bounds)))
                 (False False False)))))
!(println! UNIT_GROWTH_STATISTICS_OK)
