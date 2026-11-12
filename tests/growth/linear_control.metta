; The growth gate's own control: n pieces joined once is linear.
!(import! &self ../growth_support.metta)
(= (growth-control:copies $n $acc)
   (if (== $n 0) $acc (growth-control:copies (- $n 1) (cons-atom "abcdefghijklmnop" $acc))))
!(let $pieces (growth-control:copies (growth:size) ())
   (growth:measure (str:byte-length (str:join "" $pieces))))
