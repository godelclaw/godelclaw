; Growth workload: the refresh child keeps each row of a provider catalog of
; n distinct models (a catalog that repeats an id takes the walk the unit
; tests cover).
!(import! &self ../growth_support.metta)
!(import! &self ../../src/model_menu_cetta.metta)
(= (catalog-growth:rows $n $acc)
   (if (== $n 0) $acc
     (catalog-growth:rows (- $n 1)
       (cons-atom (str:join "" ("{\"id\": \"model-" (repr $n) "\", \"context_length\": 128000}")) $acc))))
!(let $payload (json:parse-python (str:join "" ("{\"data\": [" (str:join ", " (catalog-growth:rows (growth:size) ())) "]}")))
   (growth:measure (model:catalog $payload "kimi:")))
