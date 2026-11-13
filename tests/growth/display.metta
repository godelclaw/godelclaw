; Growth workload: n output records of one tool, then its end, appended to a
; display under the growth directory. A long command writes a record per
; chunk of output, so each record must cost the same however many came before.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/display_cetta.metta)
(= (dg:root) (path:join (growth:directory) (str:concat "display-" (repr (growth:size)))))
(= (dg:output $root)
   (display:write $root ((JsonPair "kind" (JsonString "tool.output")) (JsonPair "item" (JsonString "x"))
                         (JsonPair "text" (JsonString "a line of output\n")) (JsonPair "source" (JsonString "executor")))))
(= (dg:records $root $n $written)
   (if (== $n 0) $written (dg:records $root (- $n 1) (+ $written (dg:output $root)))))
!(growth:measure (dg:records (dg:root) (growth:size) 0))
