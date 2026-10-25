; Test facade for the inactive runtime arithmetic. No duplicated accumulator.
!(import! &self ./src/goal_bookkeeping_math_cetta.metta)

; Replay of the DIFFERENT Nat micro-unit model, NOT the goal kernel's decay.
(= (gka:model-decay-micro $s)
   (let $product (* $s 950000)
     (math:floor (/ (- $product (% $product 1000000)) 1000000))))
