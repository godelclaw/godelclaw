; INACTIVE selector. Do NOT import into loop/skills until explicitly approved.
; One import-time engine_modes boundary, as in goal_views. No failure fallback.
!(import! &self ./src/engine_modes.py)
!(case (py-call (engine_modes.active_engine))
   (("cetta" (import! &self ./src/goal_bookkeeping_cetta.metta))
    (cetta (import! &self ./src/goal_bookkeeping_cetta.metta))
    ($other (import! &self ./src/goal_bookkeeping_python.metta))))
(= (goal-bookkeeping:fresh (GoalWake $fresh $_ $_)) $fresh)
(= (goal-bookkeeping:maintenance-applied (GoalWake $_ $applied $_)) $applied)
(= (goal-bookkeeping:message (GoalWake $_ $_ $message)) $message)
