; Growth workload: n streamed attempts under one display id, each leaving a
; cursor and two provider sequence numbers, then closed. Nothing may remain
; per attempt, at any size; time grows linearly.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/stream_cursors_cetta.metta)
(= (stream-growth:loop $n $sum)
   (if (== $n 0) $sum
     (let* (($remembered (stream:remember "attempt" (cursor $n)))
            ($first (stream:sequence "attempt" (* 2 $n) "first"))
            ($second (stream:sequence "attempt" (+ 1 (* 2 $n)) "second"))
            ($closed (stream:close "attempt")))
       (stream-growth:loop (- $n 1) (+ $sum (+ $first $second))))))
!(growth:measure (stream-growth:loop (growth:size) 0))
!(nt:require no-stream-atoms-remain
   (nt:equal ((collapse (match &self (StreamCursor $i $c) $c)) (collapse (match &self (StreamSequence $i $n $t) $n))) (() ())))
