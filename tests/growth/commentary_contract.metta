; Growth workload: the enabled commentary rewrite over a prompt of n lines whose
; authored clauses come last, the worst case for the first-occurrence search.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/commentary_contract_cetta.metta)
(= (commentary-growth:lines $n $acc)
   (if (== $n 0) $acc
     (commentary-growth:lines (- $n 1) (cons-atom "(TASK_CONTEXT evidence line with λ and quoted text)" $acc))))
!(let $text (str:join "\n" (commentary-growth:lines (growth:size)
               ((commentary:legacy-output-clause) (commentary:legacy-batch-clause))))
   (growth:measure (commentary:replace-first
                     (commentary:replace-first $text (commentary:legacy-output-clause)
                       "Use the advertised metta tool for the command S-expression.")
                     (commentary:legacy-batch-clause) (commentary:batch-clause))))
