; Growth workload: the durable model choice after n other atoms in the state file.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/model_selection_cetta.metta)
(= (model-growth:lines $n $acc)
   (if (== $n 0) $acc
     (model-growth:lines (- $n 1) (cons-atom "(pinned-note some-symbol \"retained text\")" $acc))))
!(let $text (str:join "\n" (model-growth:lines (growth:size) ("  (active-model last-model)")))
   (growth:measure (model:choice-in $text)))
