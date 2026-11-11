; Branch-only goal bookkeeping selection. No live installation is implied.
; One environment-only engine switch, as in goal_views. No failure fallback.
; Both loop and skills import this. SWI must not acquire duplicate effect rules.
!(if (== (collapse (match &self (goal-bookkeeping:loaded) True)) ())
   (progn
     (add-atom &self (goal-bookkeeping:loaded))
     (import! &self ./src/goal_bookkeeping_cetta.metta)
     (add-atom &self (= (goal-bookkeeping:fresh (GoalWake $fresh $_ $_)) $fresh))
     (add-atom &self (= (goal-bookkeeping:maintenance-applied (GoalWake $_ $applied $_)) $applied))
     (add-atom &self (= (goal-bookkeeping:message (GoalWake $_ $_ $message)) $message))) True)
