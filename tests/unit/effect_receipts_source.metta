; The effect-receipt modules' authored forms, read by the engine's own parser:
; every if, let, let*, case and equation has the arity it needs, and the
; receipts module renders through display:receipt with no stream-sink or
; command-identity coupling. (Packaging of their imports and the absence of
; foreign calls are the runtime audit's.)
!(import! &self ../native_test_support.metta)
!(import! &self (library re))

(= (er:arity if) 4) (= (er:arity let) 4) (= (er:arity let*) 3) (= (er:arity case) 3) (= (er:arity =) 3)
; The forms of $atom whose head is a checked keyword but whose size differs.
(= (er:bad $atom)
   (if (not (== (get-metatype $atom) Expression)) ()
     (if (== $atom ()) ()
       (let* (($head (car-atom $atom))
              ($own (if (and (== (get-metatype $head) Symbol) (er:checked? $head))
                      (if (== (size-atom $atom) (er:arity $head)) () ((size-atom $atom) $head))
                      ())))
         (er:append $own (er:bad-all $atom))))))
(= (er:bad-all $items) (if (== $items ()) () (er:append (er:bad (car-atom $items)) (er:bad-all (cdr-atom $items)))))
(= (er:checked? $head) (or (== $head if) (or (== $head let) (or (== $head let*) (or (== $head case) (== $head =))))))
(= (er:append $a $b) (if (== $a ()) $b (cons-atom (car-atom $a) (er:append (cdr-atom $a) $b))))
; The source without comments (parse reads ";" as a symbol, the loader drops
; it): its tokens, comments left out, joined by spaces. parse takes its
; argument unevaluated, so the text is built first.
(= (er:uncommented $text $lexer)
   (case (re:find-all $lexer $text)
     (((re:matches $matches) (str:join " " (er:tokens $matches $text ()))) ($bad ""))))
(= (er:tokens $matches $text $done)
   (case $matches
     ((LNil (er:reverse $done ()))
      ((LCons $m $rest) (let $t (re:group-text $m $text 0)
                          (er:tokens $rest $text (if (str:starts-with $t ";") $done (cons-atom $t $done))))))))
(= (er:reverse $xs $acc) (if (== $xs ()) $acc (er:reverse (cdr-atom $xs) (cons-atom (car-atom $xs) $acc))))
(= (er:forms $repo $name $lexer)
   (let $text (str:join "" ("(\n" (er:uncommented (fs:read-text (path:join $repo (str:concat "src/" $name))) $lexer) "\n)"))
     (parse $text)))
(= (er:read? $forms) (and (== (get-metatype $forms) Expression) (and (> (size-atom $forms) 3) (not (== (car-atom $forms) Error)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($lexer (re:compile ";[^\n]*|\"(?:\\\\.|[^\"\\\\])*\"|[()]|[^\\s();\"]+"))
         ($names ("effect_receipt_values_cetta.metta" "effect_receipt_io_cetta.metta" "public_worklog_cetta.metta"
                  "worklog_sink_cetta.metta" "effect_receipts_cetta.metta" "effect_receipts_native.metta"))
         ($_ (nt:require the-parser-reads-each-module
               (== (filter-atom $names $n (not (er:read? (er:forms $repo $n $lexer)))) ())))
         ($bad (map-atom $names $n ($n (er:bad (er:forms $repo $n $lexer)))))
         ($_ (if (== (filter-atom $bad $row (not (== (index-atom $row 1) ()))) ()) True (println! (ARITY_PROBLEMS $bad))))
         ($_ (nt:require every-form-has-its-arity (== (filter-atom $bad $row (not (== (index-atom $row 1) ()))) ())))
         ($_ (nt:require the-lint-finds-a-two-armed-if (== (er:bad (parse "((= (f) (if a b)))")) (3 if))))
         ($receipts (fs:read-text (path:join $repo "src/effect_receipts_cetta.metta")))
         ($_ (nt:require receipts-render-through-the-display
               (and (nt:contains $receipts "display:receipt")
                    (and (not (nt:contains $receipts "stream_sink")) (not (nt:contains $receipts "command_identity")))))))
   (println! UNIT_EFFECT_RECEIPTS_SOURCE_OK))
