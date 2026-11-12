; Growth workload: n sequential native call owners, each claimed, submitted,
; described and released. Phase records must not accumulate or slow lookups.
!(import! &self ../../src/native_call_cetta.metta)
(: call-growth:loop (-> Number Number %Undefined%))
(= (call-growth:loop $n $sum)
   (if (== $n 0) $sum
     (let* (($facts (JsonObject ((JsonPair "correlation" (format-args "call-{}" ($n))) (JsonPair "epoch" "e")
                                 (JsonPair "cap" 1) (JsonPair "soft" 0))))
            ($owner (call:owner host $facts "seal"))
            ($claimed (call:begin-claim $owner))
            ($submitted (call:submitted $owner $n))
            ($metadata (call:policy-metadata $owner))
            ($owned (call:owns? $owner "e" (format-args "call-{}" ($n)) $n))
            ($released (call:release $owner)))
       (call-growth:loop (- $n 1) (+ $sum (if (and $claimed $owned) 1 0))))))
(= (call-growth:workload $n) (call-growth:loop $n 0))
