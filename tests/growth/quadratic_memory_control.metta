; The growth gate's memory mutant: every step keeps the whole text so far, so
; the memory held grows quadratically; the gate's memory bound must reject it.
!(import! &self ../growth_support.metta)
(= (growth-control:prefixes $n $text $kept)
   (if (== $n 0) $kept
     (let $longer (str:concat $text "abcdefghijklmnop")
       (growth-control:prefixes (- $n 1) $longer (cons-atom $longer $kept)))))
!(growth:measure (size-atom (growth-control:prefixes (growth:size) "" ())))
