; INACTIVE SWI interface; the oracle remains the SWI writer.
!(import! &self ./src/goals.py)
!(import! &self ./src/helper.py)
(= (gk:kernel-pass) (py-call (goals.kernel_pass)))
(= (gk:human-wake $token)
   (let $result (py-call (goals.human_wake $token))
     (GoalWake (py-call (operator.getitem $result "fresh"))
               (py-call (operator.getitem $result "maintenance_applied"))
               (py-call (operator.getitem $result "message")))))
; Same SWI decode operation without importing duplicate utility equations into
; the caller's space (which would duplicate effectful evaluations).
(= (gk:goal-write $atom) (py-call (goals.goal_write (py-call (helper.decode_safe_text $atom)))))
(= (gk:goal-drop $name $reason) (py-call (goals.goal_drop $name $reason)))
