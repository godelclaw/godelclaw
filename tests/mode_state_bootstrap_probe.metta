; TEST-ONLY shared, environment-only bootstrap. Prolog getenv/string_codes are
; available in BOTH evaluators. No Python active_engine call, native import,
; filesystem access, or state owner is needed to choose the facade.
!(import_prolog_function getenv)
!(import_prolog_function string_codes)

(= (mode-bootstrap:env $name $default)
   (case (collapse (getenv $name)) ((($value) $value) (() $default))))
(= (mode-bootstrap:space? $cp)
   (or (and (>= $cp 9) (<= $cp 13))
     (or (and (>= $cp 28) (<= $cp 32))
       (or (== $cp 133) (or (== $cp 160) (or (== $cp 5760)
         (or (and (>= $cp 8192) (<= $cp 8202))
           (or (== $cp 8232) (or (== $cp 8233)
             (or (== $cp 8239) (or (== $cp 8287) (== $cp 12288))))))))))))
(= (mode-bootstrap:reverse $items)
   (foldl-atom $items () $acc $item (cons-atom $item $acc)))
(= (mode-bootstrap:lstrip $points)
   (if (== $points ()) ()
     (if (mode-bootstrap:space? (car-atom $points))
       (mode-bootstrap:lstrip (cdr-atom $points)) $points)))
(= (mode-bootstrap:codes $text)
   (let $points (string_codes $text)
     (map-atom (mode-bootstrap:reverse (mode-bootstrap:lstrip
       (mode-bootstrap:reverse (mode-bootstrap:lstrip $points)))) $cp
       (if (and (>= $cp 65) (<= $cp 90)) (+ $cp 32) $cp))))
(= (mode-bootstrap:cetta?)
   (== (mode-bootstrap:codes
     (mode-bootstrap:env "METTACLAW_ACTIVE_ENGINE"
       (mode-bootstrap:env "METTACLAW_ENGINE" "petta"))) (99 101 116 116 97)))
