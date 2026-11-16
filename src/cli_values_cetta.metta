; Command-line values are data. Numeric text is checked before parsing; no
; argument is interpreted as a program.
!(import! &self ./state_file_cetta.metta)
!(import! &self (library re))
(= (cli:text $value) (if (== (get-type $value) String) $value (repr (quote $value))))
(= (cli:strings $args) (map-atom $args $arg (cli:text $arg)))
(= (cli:number $text $fallback)
   (if (and (== (get-type $text) String)
         (re:is-match (re:compile "\\A[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?\\z") $text))
     (parse $text) $fallback))
(= (cli:pair-args $args $switches $object)
   (if (== $args ()) $object
     (let $flag (car-atom $args)
       (if (cli:member? $flag $switches)
         (cli:pair-args (cdr-atom $args) $switches (sf:put $object $flag True))
         (if (== (cdr-atom $args) ()) (CliFailureV1 "option needs a value")
           (cli:pair-args (cdr-atom (cdr-atom $args)) $switches (sf:put $object $flag (car-atom (cdr-atom $args)))))))))
(= (cli:member? $value $items)
   (if (== $items ()) False (if (== $value (car-atom $items)) True (cli:member? $value (cdr-atom $items)))))
