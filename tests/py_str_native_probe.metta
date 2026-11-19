;; utils:py-str gives, once, the text Python's str() gave each part as the
;; retired py-call bridge converted it. The expected texts are that bridge's
;; own, taken from the last Python-enabled engine. Native arithmetic refuses
;; non-finite floats and has no Python objects, so neither can reach py-str.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (same-text $parts $expected) (assertEqual (collapse (utils:py-str $parts)) ($expected)))
(= (pow10 $n) (if (== $n 0) 1 (* 10 (pow10 (- $n 1)))))

!(let* (($_ (same-text ("plain") "plain"))
        ($_ (same-text ("") ""))
        ($_ (same-text ("q\"x\"'y'") "q\"x\"'y'"))
        ($_ (same-text ("ünï ✓ tab\tline\nend") "ünï ✓ tab\tline\nend"))
        ($_ (same-text (plain-symbol) "plain-symbol"))
        ($_ (same-text (NEW_ACTIVITY:) "NEW_ACTIVITY:"))
        ($_ (same-text ("Output a ((skill args) ...) S-expr") "Output a ((skill args) ...) S-expr"))
        ($_ (same-text (True) "True"))
        ($_ (same-text (False) "False"))
        ($_ (same-text (0) "0"))
        ($_ (same-text (-7) "-7"))
        ($_ (same-text (1000000000000000000000000000000) "1000000000000000000000000000000"))
        ($_ (same-text (2.5) "2.5"))
        ($_ (same-text (0.1) "0.1"))
        ($_ (same-text (100.0) "100.0"))
        ($_ (same-text ((x "y" 3)) "['x', 'y', 3]"))
        ;; Quoted Boolean spellings stay text; both unquoted spellings and
        ;; computed Booleans convert as Booleans, inside lists too.
        ($_ (same-text ("true") "true"))
        ($_ (same-text ("False") "False"))
        ($_ (same-text (true) "True"))
        ($_ (same-text (false) "False"))
        ($_ (same-text ((== 1 1)) "True"))
        ($_ (same-text ((== 1 2)) "False"))
        ($_ (same-text ((nested True false (== 1 1))) "['nested', True, False, True]"))
        ;; Python's float text where CeTTa's own differs.
        ($_ (same-text (1e16) "1e+16"))
        ($_ (same-text (1e22) "1e+22"))
        ($_ (same-text (1.0e-5) "1e-05"))
        ($_ (same-text (-0.0) "-0.0"))
        ($_ (same-text (123456789012345680.0) "1.2345678901234568e+17"))
        ;; The parts concatenate.
        ($_ (same-text ("a" plain-symbol 3 True (x "y") ",") "aplain-symbol3True['x', 'y'],"))
        ($_ (assertEqual (utils:py-str ()) ""))
        ;; Python's integer digit limit: 4300 digits convert, 4401 raise.
        ($_ (assertEqual (str:char-length (utils:py-str ((pow10 4299)))) 4300))
        ($_ (assertEqual (case (catch (utils:py-str ((pow10 4400)))) (((Error $where $why) raised) ($text $text)))
                         raised)))
    (println! PY_STR_NATIVE_OK))
