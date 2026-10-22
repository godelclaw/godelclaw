; A small HOL model, not a refinement proof of the runtime.
; Church-encoded turn results carry two independently typed fields. A provider
; native tool call or a compatibility JSON envelope may instantiate the
; Commands field; dispatch selects only that field. No parser, transport,
; broker or privacy claim follows without a separate implementation
; correspondence argument.
!(import! &self holkernel)

; Primitive HOL BETA applies its abstraction to its own bound variable.
; Checked capture-avoiding INST derives the general application instance.
(= (cb:beta $k $binder $body $argument)
   (hol:inst (hol:beta $k (A (L $binder $body) $binder))
             (($argument $binder))))

(= (cb:selector)
   (L (v "ignored" (V "Commentary"))
      (L (v "selected" (V "Commands"))
         (v "selected" (V "Commands")))))
(= (cb:selector-type)
   (T "fun" (V "Commentary") (T "fun" (V "Commands") (V "Commands"))))
(= (cb:envelope-type)
   (T "fun" (cb:selector-type) (V "Commands")))
(= (cb:envelope $note $commands)
   (L (v "pick" (cb:selector-type))
      (A (A (v "pick" (cb:selector-type)) $note) $commands)))
(= (cb:dispatcher)
   (L (v "packet" (cb:envelope-type))
      (A (v "packet" (cb:envelope-type)) (cb:selector))))
(= (cb:dispatch $note $commands)
   (A (cb:dispatcher) (cb:envelope $note $commands)))

; All four reductions are checked; TRANS verifies the intermediate endpoints.
(= (cb:selection-proof $k $note $commands)
   (let* (($pick (v "pick" (cb:selector-type)))
          ($packet (v "packet" (cb:envelope-type)))
          ($ignored (v "ignored" (V "Commentary")))
          ($selected (v "selected" (V "Commands")))
          ($first (cb:beta $k $packet (A $packet (cb:selector))
                           (cb:envelope $note $commands)))
          ($second (cb:beta $k $pick (A (A $pick $note) $commands) (cb:selector)))
          ($third (hol:mk-comb
                    (cb:beta $k $ignored (L $selected $selected) $note)
                    (hol:refl $k $commands)))
          ($fourth (cb:beta $k $selected $selected $commands)))
      (hol:trans (hol:trans (hol:trans $first $second) $third) $fourth)))

; Derive symmetry from congruence and equality modus ponens, not an axiom.
(= (cb:sym $k $h $left)
   (let* (($eq (c "=" (T "fun" (V "Commands")
                             (T "fun" (V "Commands") (T "bool")))))
          ($contexts (hol:mk-comb (hol:mk-comb (hol:refl $k $eq) $h)
                                 (hol:refl $k $left))))
      (hol:eq-mp $contexts (hol:refl $k $left))))

; Free variables in the checked theorems are schematic. No ASSUME or axiom
; introduction is used. The second theorem changes commentary arbitrarily
; while keeping commands fixed (model-level noninterference).
!(let* (($k (hol:kernel sealed))
        ($note (v "public-note" (V "Commentary")))
        ($other (v "other-note" (V "Commentary")))
        ($commands (v "command-batch" (V "Commands")))
        ($left (cb:selection-proof $k $note $commands))
        ($right (cb:selection-proof $k $other $commands))
        ($independent (hol:trans $left
            (cb:sym $k $right (cb:dispatch $other $commands)))))
   (CommentaryBoundary
      (Selection (hol:sequent $left) (Axioms (hol:axioms $left)))
      (Noninterference (hol:sequent $independent)
                       (Axioms (hol:axioms $independent)))))
