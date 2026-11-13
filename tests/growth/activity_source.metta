; Growth workload: settling one acknowledged batch of n runtime occurrences
; and n update ids, every update id twice, through validation, repeats
; dropped and settlement with no ledger or handover folder. A long rest can
; leave a large batch, so each step must stay linear in the ids.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/activity_source_cetta.metta)
(= (ag:ids $n $out)
   (if (== $n 0) $out
     (let* (($update (repr (+ 1000000 $n))) ($runtime (str:concat "runtime:job:" (repr $n))))
       (ag:ids (- $n 1) (cons-atom $runtime (cons-atom $update (cons-atom $update $out)))))))
(= (ag:run $n)
   (let $text (str:join "," (ag:ids $n ()))
     (activity-source:ack-with (ActivitySourceConfigV1 "" False "") $text)))
!(growth:measure (ag:run (growth:size)))
