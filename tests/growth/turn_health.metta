; Growth workload: a health label from n characters, half of them dropped, and
; a record update over a record holding n other members.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/turn_health_cetta.metta)
(= (health-growth:pieces $n $acc)
   (if (== $n 0) $acc (health-growth:pieces (- $n 1) (cons-atom "a!" $acc))))
(= (health-growth:members $n $acc)
   (if (== $n 0) $acc
     (health-growth:members (- $n 1) (cons-atom (JsonPair (str:concat "k" (repr $n)) $n) $acc))))
!(let* (($label (health:label (str:join "" (health-growth:pieces (growth:size) ())) "unknown"))
        ($record (health:failed (JsonObject (health-growth:members (growth:size) ())) "transport-error" 100 15 "e1")))
   (growth:measure (+ (str:byte-length $label) (str:byte-length (sf:python-json $record)))))
