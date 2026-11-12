; Growth workload: the contract rewrite over a prompt of n lines whose
; authored clause comes last, the worst case for the first-occurrence search.
!(import! &self ../../src/commentary_contract_cetta.metta)
(: commentary-growth:lines (-> Number %Undefined% %Undefined%))
(= (commentary-growth:lines $n $acc)
   (if (== $n 0) $acc
     (commentary-growth:lines (- $n 1) (cons-atom "(TASK_CONTEXT evidence line with λ and quoted text)" $acc))))
(= (commentary-growth:workload $n)
   (str:byte-length (commentary:prompt (str:join "\n"
     (commentary-growth:lines $n ("Output a ((skillName args) ...) S-expression."))))))
