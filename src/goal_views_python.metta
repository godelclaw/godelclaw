; Unchanged SWI-PeTTa skill implementation.
!(import! &self ./src/goals.py)
(= (goal-view:skill) (py-call (goals.goals_view)))
