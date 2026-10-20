; One concrete application of RetryWake, independently checked by the native
; HOL, Megalodon and MM0 kernels.  The result is inert evidence: it grants no
; effect authority and carries the exact Lean-model and host-source digests.
!(import! &self holkernel)
!(import! &self megalodon)
!(import! &self mm0kernel)
!(import! &self seal)

(= (gc:reconcile failed claimed) failed)
(= (gc:reconcile failed submitted) uncertain)
(= (gc:reconcile completed claimed) completed)
(= (gc:reconcile completed submitted) completed)
(= (gc:reconcile unknown claimed) uncertain)
(= (gc:reconcile unknown submitted) uncertain)
(= (gc:reconcile $request $call) $call)

; ------------------------------ HOL: primitive equality and REFL
(= (gc:hol-object uncertain) (v "uncertain" (T "bool")))
(= (gc:hol-object completed) (v "completed" (T "bool")))
(= (gc:hol-check $kernel $left $right)
   (let* (($l (gc:hol-object $left))
          ($r (gc:hol-object $right))
          ($eq (c "=" (T "fun" (T "bool")
                         (T "fun" (T "bool") (T "bool")))))
          ($claim (A (A $eq $l) $r))
          ($rows ((HolNode 0 (REFL $l) () (thm () $claim)))))
     (hol:check-dag $kernel $rows 0)))

; ------------------------------ Megalodon: Leibniz equality
(= (gc:mg-object uncertain) (Tm.named "uncertain"))
(= (gc:mg-object completed) (Tm.named "completed"))
(= (gc:mg-equality $left $right)
   (Tm.all (Tp.arr (Tp.prop) (Tp.prop))
     (Tm.imp (Tm.app (Tm.db 0) (gc:mg-object $left))
             (Tm.app (Tm.db 0) (gc:mg-object $right)))))
(= (gc:mg-proof $left)
   (Pf.termLam (Tp.arr (Tp.prop) (Tp.prop))
     (Pf.proofLam (Tm.app (Tm.db 0) (gc:mg-object $left)) (Pf.hyp 0))))
(= (gc:mg-check $theory $name $left $right)
   (mg:try-prove $theory $name (gc:mg-equality $left $right)
                 (gc:mg-proof $left)))

; ------------------------------ MM0: explicit equality-reflexivity axiom
(= (gc:mm-object uncertain) (uncertain))
(= (gc:mm-object completed) (completed))
(= (gc:mm-check $theory $name $left $right)
   (let $l (gc:mm-object $left)
     (let $r (gc:mm-object $right)
       (mm0:prove $theory (theorem $name () () (equal $l $r)) ()
         ((Mm0Row 0 (thm equality-reflexivity ($l)) ())) 0))))

!(let $kernel (hol:kernel sealed) (bind! &gc-hol (new-state $kernel)))
(= (GC-HOL) (get-state &gc-hol))

!(let $state (mg:new-theory-state custom) (bind! &gc-mg $state))
(= (GC-MG) (mg:use (get-state &gc-mg)))
!(mg:parameter (mg:authority (get-state &gc-mg)) "uncertain" (Tp.prop))
!(mg:parameter (mg:authority (get-state &gc-mg)) "completed" (Tp.prop))

!(let $theory (mm0:theory) (bind! &gc-mm (new-state $theory)))
(= (GC-MM) (get-state &gc-mm))
!(mm0:declare (GC-MM) (sort object (strict)))
!(mm0:declare (GC-MM) (sort wff (strict provable)))
!(mm0:declare (GC-MM) (term uncertain () (object ())))
!(mm0:declare (GC-MM) (term completed () (object ())))
!(mm0:declare (GC-MM)
    (term equal ((var x object) (var y object)) (wff ())))
!(mm0:declare (GC-MM)
    (axiom equality-reflexivity ((var x object)) () (equal x x)))

(= (gc:checked-incident)
   (let* (($actual (gc:reconcile failed submitted))
          ($hol (gc:hol-check (GC-HOL) $actual uncertain))
          ($mg (gc:mg-check (GC-MG) "gc-retry-uncertain" $actual uncertain))
          ($mm (gc:mm-check (GC-MM) gc-retry-uncertain $actual uncertain)))
     (if (and (== (seal:kind $hol) Sealed)
              (and (== (seal:kind $mg) Sealed)
                   (== (seal:kind $mm) Sealed)))
       (GodelClawCheckedTransitionV1
         failedRequestHasNoActiveNativeCall
         "6031c40a109ee667af2c27e04a1420d1406f27c4759449009f3405ed0e0bb812"
         "688da2a801f231c618f3ba77d12613df34edd3ae05dd5557e8a6511892ebc981"
         "88c6713192cb30ed95013746cb627693da467b659d7fd1e9d1599c2df942d638"
         (GcRequestStateV1 failed)
         (GcCallStateV1 submitted) (GcCallStateV1 uncertain)
         (kernels hol megalodon mm0))
       (GodelClawContractRefusedV1 $actual
         (kinds (seal:kind $hol) (seal:kind $mg) (seal:kind $mm))))))

; Positive application used by the running-language fixture.
!(gc:checked-incident)

; Each kernel independently refuses the altered expected result.
!(case (gc:hol-check (GC-HOL) uncertain completed)
   (((hol:refused $status $why) (hol-refused $status))
    ($other unexpected-hol-acceptance)))
!(case (gc:mg-check (GC-MG) "gc-wrong" uncertain completed)
   (((Error $call $why) (mg-refused $why))
    ($other unexpected-megalodon-acceptance)))
!(case (gc:mm-check (GC-MM) gc-wrong uncertain completed)
   (((mm0:refused $status $why) (mm0-refused $status))
    ($other unexpected-mm0-acceptance)))

; Kernel seals are distinct capabilities, never interchangeable evidence.
!(let $hol (gc:hol-check (GC-HOL) uncertain uncertain)
   (case (mm0:statement $hol)
     (((mm0:error $status $why) (mm0-refused-hol $status))
      ($other leaked-cross-kernel-authority))))
