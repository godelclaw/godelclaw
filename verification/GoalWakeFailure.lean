import RetryWake

/-!
Project 04b: refine RetryWake's once-per-presented-token intent across partial
goal-file commit followed by failed kernel-state persistence. Reservation is
in-process, not a two-file transaction or proof of crash-durable deduplication.
-/
namespace GoalWakeFailure

structure State where
  runtime : Option Nat := none
  durable : Option Nat := none
  goalPasses : Nat := 0
  kernelWrites : Nat := 0
deriving Repr, DecidableEq

def present (token : Nat) (durableOK : Bool) (s : State) : State × (Nat × Nat) :=
  if s.runtime = some token then (s, (0, 0))
  else
    let reserved := { s with runtime := some token }
    if s.durable = some token then (reserved, (0, 0))
    else
      ({ reserved with goalPasses := s.goalPasses + 1
                       durable := if durableOK then some token else s.durable
                       kernelWrites := s.kernelWrites + if durableOK then 1 else 0 },
       (1, if durableOK then 1 else 0))

theorem repeatAfterFailure (token : Nat) (laterOK : Bool) (s : State) :
    present token laterOK (present token false s).1 = ((present token false s).1, (0, 0)) := by
  by_cases h : s.runtime = some token
  · simp [present, h]
  · by_cases d : s.durable = some token <;> simp [present, h, d]

-- On failed fresh attempts (no durable token), the maintenance projection is
-- exactly the already-modelled RetryWake.maintain, not cognition progress.
def forget (s : State) : RetryWake.WakeState :=
  ⟨s.runtime, s.goalPasses, 0⟩

theorem failureRefinesMaintain (token : Nat) (s : State)
    (different : s.durable ≠ some token) :
    forget (present token false s).1 = RetryWake.maintain token (forget s) := by
  by_cases h : s.runtime = some token <;>
    simp [present, h, different, forget, RetryWake.maintain]

def tokens : List Nat := [1, 1, 2, 2, 1, 1, 3, 3]

def replay : List Nat → State → List (List Nat)
  | [], _ => []
  | token :: rest, s =>
      let (next, (fresh, applied)) := present token false s
      [fresh, applied, next.goalPasses, next.kernelWrites] :: replay rest next

end GoalWakeFailure

def main : IO Unit := IO.println (repr (GoalWakeFailure.replay GoalWakeFailure.tokens {}))
