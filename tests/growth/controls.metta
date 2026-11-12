; Controls for the growth gate itself: a linear workload must pass and a
; quadratic one (each step copies the whole accumulated text) must fail.
!(import! &self (library str))
(: growth-control:copies (-> Number %Undefined% %Undefined%))
(= (growth-control:copies $n $acc)
   (if (== $n 0) $acc (growth-control:copies (- $n 1) (cons-atom "abcdefghijklmnop" $acc))))
(= (growth-control:linear $n) (str:byte-length (str:join "" (growth-control:copies $n ()))))
(: growth-control:appended (-> Number %Undefined% %Undefined%))
(= (growth-control:appended $n $acc)
   (if (== $n 0) $acc (growth-control:appended (- $n 1) (str:concat $acc "abcdefghijklmnop"))))
(= (growth-control:quadratic $n) (str:byte-length (growth-control:appended $n "")))
