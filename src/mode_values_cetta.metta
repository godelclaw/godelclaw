; Reuse pure, qualified receipt value operations; no receipt state or I/O.
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self ./src/effect_receipt_values_cetta.metta)
!(import! &self ./src/mode_text_cetta.metta)

(= (mv:failed? $value) (erv:failed? $value))
(= (mv:raise $value)
   (case $value
     (((ErFailure $class $message $detail)
       (Error mode-state (PythonException $class $message $detail)))
      ($other $other))))
(= (mv:name $value)
   (let $text (erv:str $value)
     (if (mv:failed? $text) $text (mode-text:name $text))))
(= (mv:requested $value)
   (let $crossed (erv:cross $value)
     (if (mv:failed? $crossed) $crossed
       (mv:name (if (erv:truth $crossed) $crossed "")))))
; Python int(str) is NOT int(str.strip()): the four ASCII separators
; U+001C..U+001F are stripped by str.strip, but are never legal in int text.
; Other Unicode whitespace, Unicode Nd digits and underscores use the pure
; value parser, including CPython's 4300 decimal-digit conversion limit.
(= (mv:int $value)
   (if (== (get-type $value) String)
     (if (mv:ascii-separator? (mode-text:points $value))
       (erv:fail ValueError (str:concat "invalid literal for int() with base 10: "
         (erv:slice (erv:quote $value) 0 200)))
       (erv:int-string $value))
     (erv:int $value)))
(= (mv:ascii-separator? $points)
   (if (== $points ()) False
     (if (and (>= (car-atom $points) 28) (<= (car-atom $points) 31)) True
       (mv:ascii-separator? (cdr-atom $points)))))
(= (mv:budget $value)
   (let $integer (mv:int (erv:cross $value))
     (case $integer
       (((ErFailure TypeError $_ $_) 0) ((ErFailure ValueError $_ $_) 0)
        ((ErFailure $_ $_ $_) $integer) ($number (max 0 $number))))))
(= (mv:contains $items $value) (erv:contains $items $value))
