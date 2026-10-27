; Pure retained numeric-to-TEXT semantics, pinned to SQLite 3.53.2's default
; 17-digit conversion. No database is loaded or called here. The arithmetic
; follows src/util.c (sqlite3Fp2Convert10/sqlite3FpDecode) and the %!g rendering
; rules in src/printf.c at version-3.53.2, both public-domain source.
; All powers are computed as integers rather than copied lookup tables.
!(import! &self (library math))
!(import! &self (library str))
!(import! &self (library json))

(= (sql-num:quot $left $right)
  (case (math:integer-divmod $left $right)
    (((IntegerDivmodV1 $quotient $remainder) $quotient)
     ($error $error))))

(= (sql-num:power $base $exponent)
  (if (== $exponent 0) 1
    (case (math:integer-divmod $exponent 2)
      (((IntegerDivmodV1 $half $odd)
        (let* (($value (sql-num:power $base $half)) ($square (* $value $value)))
          (if (== $odd 0) $square (* $square $base))))
       ($error $error)))))

; Upper 64 significant bits of 10^p. The exponent formula agrees with
; floor(log2(10^p)) throughout the binary64 conversion's required range.
(= (sql-num:power-high $power)
  (let* (($binary (sql-num:quot (* $power 108853) 32768))
         ($shift (- 63 $binary)))
    (if (>= $power 0)
      (let $ten (sql-num:power 10 $power)
        (if (>= $shift 0) (* $ten (sql-num:power 2 $shift))
          (sql-num:quot $ten (sql-num:power 2 (- 0 $shift)))))
      (let $ten (sql-num:power 10 (- 0 $power))
        (if (>= $shift 0) (sql-num:quot (sql-num:power 2 $shift) $ten)
          (sql-num:quot 1 (* $ten (sql-num:power 2 (- 0 $shift)))))))))

(= (sql-num:normalize $original $sign $mantissa $exponent)
  (if (< $mantissa 9223372036854775808)
    (sql-num:normalize $original $sign (* $mantissa 2) (- $exponent 1))
    (let* (($power (- 17 (sql-num:quot (* (+ $exponent 63) 78913) 262144)))
           ($high (sql-num:quot (* $mantissa (sql-num:power-high $power)) 18446744073709551616))
           ($binary (sql-num:quot (* $power 108853) 32768))
           ($shift (- 0 (+ $exponent (+ $binary 2))))
           ($scaled (sql-num:quot $high (sql-num:power 2 $shift)))
           ($coefficient (sql-num:quot (+ $scaled 1) 2))
           ($digits (json:stringify-python $coefficient))
           ($count (str:byte-length $digits))
           ($point (- $count $power)))
      (sql-num:finish $original $sign $digits $point (- 0 $power)))))

(= (sql-num:digit $digits $index) (str:byte-slice $digits $index (+ $index 1)))
(= (sql-num:nines-start $digits $index)
  (if (== $index 0) 0
    (if (== (sql-num:digit $digits (- $index 1)) "9")
      (sql-num:nines-start $digits (- $index 1)) $index)))
(= (sql-num:zeroes-start $digits $index)
  (if (== $index 1) 1
    (if (== (sql-num:digit $digits (- $index 1)) "0")
      (sql-num:zeroes-start $digits (- $index 1)) $index)))

(= (sql-num:roundtrips $original $coefficient $exponent)
  (let* (($digits (json:stringify-python $coefficient))
         ($power (str:number->string $exponent))
         ($text (str:join "" ($digits "e" $power)))
         ($parsed (json:parse-python $text)))
    (== (math:abs $original) $parsed)))

(= (sql-num:nines-round $original $digits $exponent)
  (let* (($start (sql-num:nines-start $digits 14))
         ($prefix (if (== $start 0) 1
                    (+ (str:string->number (str:byte-slice $digits 0 $start)) 1)))
         ($power (+ $exponent (- (str:byte-length $digits) $start))))
    (if (sql-num:roundtrips $original $prefix $power) (+ $start 1) 17)))

(= (sql-num:zeroes-round $original $digits $exponent)
  (let* (($start (sql-num:zeroes-start $digits 13))
         ($prefix (str:string->number (str:byte-slice $digits 0 $start)))
         ($power (+ $exponent (- (str:byte-length $digits) $start))))
    (if (sql-num:roundtrips $original $prefix $power) (+ $start 1) 17)))

(= (sql-num:maybe-zeroes-round $original $digits $point $exponent)
  (if (>= $point (str:byte-length $digits)) (sql-num:zeroes-round $original $digits $exponent)
    (if (== (str:byte-slice $digits 13 16) "000") (sql-num:zeroes-round $original $digits $exponent) 17)))

(= (sql-num:round-index $original $digits $point $exponent)
  (if (== (str:byte-slice $digits 14 16) "99") (sql-num:nines-round $original $digits $exponent)
    (sql-num:maybe-zeroes-round $original $digits $point $exponent)))

(= (sql-num:trim $digits)
  (let $count (str:byte-length $digits)
    (if (> $count 1)
      (if (== (sql-num:digit $digits (- $count 1)) "0")
        (sql-num:trim (str:byte-slice $digits 0 (- $count 1))) $digits)
      $digits)))

(= (sql-num:finish $original $sign $digits $point $exponent)
  (if (> (str:byte-length $digits) 17)
    (let* (($round (sql-num:round-index $original $digits $point $exponent))
           ($prefix (str:string->number (str:byte-slice $digits 0 $round)))
           ($increment (if (>= (str:compare (sql-num:digit $digits $round) "5") 0) 1 0))
           ($rounded (json:stringify-python (+ $prefix $increment)))
           ($next-point (if (> (str:byte-length $rounded) $round) (+ $point 1) $point)))
      (sql-num:render $sign (sql-num:trim $rounded) $next-point))
    (sql-num:render $sign (sql-num:trim $digits) $point)))

(= (sql-num:zeros $count)
  (if (== $count 0) "" (str:concat "0" (sql-num:zeros (- $count 1)))))

(= (sql-num:scientific $digits $exponent)
  (let* (($count (str:byte-length $digits))
         ($fraction (if (== $count 1) "0" (str:byte-slice $digits 1 $count)))
         ($sign (if (< $exponent 0) "-" "+"))
         ($absolute (if (< $exponent 0) (- 0 $exponent) $exponent))
         ($power (str:number->string $absolute))
         ($padded (if (< $absolute 10) (str:concat "0" $power) $power)))
    (str:join "" ((str:byte-slice $digits 0 1) "." $fraction "e" $sign $padded))))

(= (sql-num:fixed $digits $point)
  (if (<= $point 0) (str:join "" ("0." (sql-num:zeros (- 0 $point)) $digits))
    (let $count (str:byte-length $digits)
      (if (>= $point $count) (str:join "" ($digits (sql-num:zeros (- $point $count)) ".0"))
        (str:join "" ((str:byte-slice $digits 0 $point) "." (str:byte-slice $digits $point $count)))))))

(= (sql-num:render $sign $digits $point)
  (let* (($exponent (- $point 1))
         ($text (if (< $exponent -4) (sql-num:scientific $digits $exponent)
                  (if (> $exponent 16) (sql-num:scientific $digits $exponent)
                    (sql-num:fixed $digits $point)))))
    (if (< $sign 0) (str:concat "-" $text) $text)))

(= (sql-value:float-text $value)
  (case (math:binary64-parts $value)
    (((Binary64PartsV1 $sign $mantissa $exponent)
      (if (== $mantissa 0) "0.0" (sql-num:normalize $value $sign $mantissa $exponent)))
     ((Binary64InfinityV1 $sign) (if (< $sign 0) "-Inf" "Inf"))
     ((Binary64NaNV1 $sign $payload) JsonNull)
     ($error $error))))

(= (sql-value:call (FloatText $value)) (sql-value:float-text $value))
(= (sql-value:call (PowerHigh $exponent)) (sql-num:power-high $exponent))
