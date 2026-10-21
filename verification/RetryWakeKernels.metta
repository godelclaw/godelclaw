; Check a proof of the encoded transition, not a precomputed result equality.
; Proof candidates are generated from the module's typed rule export by
; scripts/check_retry_wake.py. Kernels validate every inference/conversion.
; Evidence here is inert and does not grant runtime action authority.
!(import! &self holkernel)
!(import! &self megalodon)
!(import! &self seal)

(= (gc:hol-check $rows $root)
   (let $kernel (hol:kernel sealed)
     (case (hol:check-dag $kernel $rows $root)
       (((hol:refused $status $reason) (GcHolRefusedV2))
        ($theorem (GcHolEvidenceV2 (hol:sequent $theorem)
                                  (Axioms (hol:axioms $theorem))))))))

(= (gc:mg-check $claim $proof)
   (let* (($pair (mg:new-theory custom))
          ; The native API returns this typed product. Indexing preserves its
          ; owned handles in all hosts; native prove validates the theory seal.
          ($theory (index-atom $pair 1))
          ($before (mg:inventory $theory)))
     (case (mg:try-prove $theory "retry-wake-case" $claim $proof)
       (((Error $call $reason) (GcMegalodonRefusedV2))
        ($theorem (GcMegalodonEvidenceV2 (Before $before)
                                       (mg:statement $theorem)))))))

; MM0 has no audited encoding of this transition theory yet. Do not substitute
; an equality-reflexivity axiom and count that as checking the transition.
(= (gc:mm0-support) (GcBackendUnavailableV2 mm0 audited-transition-theory-required))
