; The growth gate's mutant: each step copies the whole accumulated text, so the
; time grows quadratically; the gate must reject it.
!(import! &self ../growth_support.metta)
(= (growth-control:appended $n $acc)
   (if (== $n 0) $acc (growth-control:appended (- $n 1) (str:concat $acc "abcdefghijklmnop"))))
!(growth:measure (str:byte-length (growth-control:appended (growth:size) "")))
