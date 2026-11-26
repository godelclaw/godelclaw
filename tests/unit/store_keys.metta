; Store keys built independently of the store: a prefix, then a signed 64-bit
; ordinal with its sign bit flipped, as eight big-endian bytes written as a
; literal and read back.
!(import! &self (library math))
(= (sk:digit $d) (str:byte-slice "0123456789abcdef" $d (+ $d 1)))
(= (sk:hex2 $b) (let (IntegerDivmodV1 $hi $lo) (math:integer-divmod $b 16) (str:concat (sk:digit $hi) (sk:digit $lo))))
(= (sk:escaped $u $n $acc)
   (if (== $n 0) $acc
     (let (IntegerDivmodV1 $q $r) (math:integer-divmod $u 256)
       (sk:escaped $q (- $n 1) (str:concat (str:concat "\\x" (sk:hex2 $r)) $acc)))))
; parse takes its argument unevaluated: the text is built first.
(= (sk:suffix $ordinal)
   (let $text (str:join "" ("\"" (sk:escaped (+ $ordinal 9223372036854775808) 8 "") "\"")) (parse $text)))
(= (sk:key $prefix $ordinal) (str:concat $prefix (sk:suffix $ordinal)))
