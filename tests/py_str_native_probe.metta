!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
;; Python's comparison as an integer: the engines return its Boolean differently.
!(py-eval "setattr(__import__('builtins'), 'py_str_probe_same', lambda a, b: int(a == b)) or 0")

;; utils:py-str returns py-str's text once, for every kind of part.  The
;; oracle is py-str's own Python conversion with exclusive branches, so that
;; it too returns once on CeTTa.
(= (oracle-py-str $parts $out)
   (if (== $parts ()) $out
     (let* (($head (car-atom $parts))
            ($tail (cdr-atom $parts))
            ($next (py-call (operator.add $out (py-call (str $head))))))
       (oracle-py-str $tail $next))))

(= (same-text $parts)
   (let* (($native (collapse (utils:py-str $parts)))
          ($_ (assertEqual (cdr-atom $native) ()))
          ($oracle (oracle-py-str $parts "")))
     (assertEqual (py-call (py_str_probe_same (car-atom $native) $oracle)) 1)))

(= (each-part-same $parts)
   (if (== $parts ()) True
     (let $_ (same-text (cons-atom (car-atom $parts) ()))
       (each-part-same (cdr-atom $parts)))))

(= (pow10 $n) (if (== $n 0) 1 (* 10 (pow10 (- $n 1)))))

(= (portable-parts)
   ("plain" "" "q\"x\"'y'" "ünï ✓ tab\tline\nend" plain-symbol NEW_ACTIVITY:
    (py-call (str "Output a ((skill args) ...) S-expr")) (py-call (str ","))
    True False 0 1 -7 1000000000000000000000000000000 2.5 0.1 100.0 (x "y" 3)))

;; Python's text where CeTTa's own number text differs (1e+16 against
;; 1.0000000000000000e+16), the non-finite floats, a returned "True", an empty
;; returned text, and the integer digit limit on both sides of it.
(= (cetta-parts)
   ((py-call (str "")) (py-call (str "a\"b")) (py-call (str "True"))
    1e16 1e22 1.0e-5 -0.0 123456789012345680.0
    (py-call (float "inf")) (py-call (float "-inf")) (py-call (float "nan"))
    (pow10 4299) (py-call (dict))))

!(let* (($portable (portable-parts))
        ($_ (each-part-same $portable))
        ($_ (same-text $portable))
        ($_ (assertEqual (utils:py-str ()) ""))
        ($_ (if (utils:cetta? (engine:active))
                (let* (($cetta (cetta-parts))
                       ($_ (each-part-same $cetta))
                       ($_ (same-text $cetta))
                       ($big (pow10 4400))
                       ($_ (assertEqual
                             (case (catch (oracle-py-str ($big) ""))
                               (((Error $where1 $why1) raised) ($text1 $text1)))
                             raised)))
                  (assertEqual
                    (case (catch (utils:py-str ($big)))
                      (((Error $where2 $why2) raised) ($text2 $text2)))
                    raised))
                True)))
    (println! PY_STR_NATIVE_OK))
