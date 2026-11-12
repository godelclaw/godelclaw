; Growth workload: the durable choice after n other atoms in the state file.
!(import! &self ../../src/model_selection_cetta.metta)
(: model-growth:lines (-> Number %Undefined% %Undefined%))
(= (model-growth:lines $n $acc)
   (if (== $n 0) $acc
     (model-growth:lines (- $n 1) (cons-atom "(pinned-note some-symbol \"retained text\")" $acc))))
(= (model-growth:workload $n)
   (str:byte-length (model:choice-in (str:join "\n" (model-growth:lines $n ("  (active-model last-model)"))))))
