import Std

/-!
Input preparation refreshes the presentation, not the durable input queue.
Acknowledgement uses the fixed presentation of the successful turn. The host
must supply stable event identities, ordered pending input, and a successful
history receipt before invoking acknowledgement.
-/

namespace InputBatch

structure State where
  pending : List Nat
  prepared : List Nat
deriving DecidableEq, Repr

def arrive (state : State) (event : Nat) : State :=
  { state with pending := state.pending ++ [event] }

def prepare (state : State) : State :=
  { state with prepared := state.pending }

def Ready (state : State) : Prop :=
  ∀ event ∈ state.pending, event ∈ state.prepared

def acknowledge (state : State) (historySucceeded : Bool) : State :=
  if historySucceeded then
    { pending := state.pending.filter (fun event => !state.prepared.contains event)
      prepared := [] }
  else state

theorem preparation_preserves_pending (state : State) :
    (prepare state).pending = state.pending := rfl

theorem preparation_includes_every_pending_event (state : State) :
    (prepare state).prepared = state.pending := rfl

theorem preparation_is_ready (state : State) : Ready (prepare state) := by
  intro event member
  exact member

theorem retry_without_new_input_is_idempotent (state : State) :
    prepare (prepare state) = prepare state := rfl

theorem arrival_preserves_prepared_frontier (state : State) (event : Nat) :
    (arrive state event).prepared = state.prepared := rfl

theorem new_unprepared_input_blocks_provider (state : State) (event : Nat)
    (unread : event ∉ state.prepared) : ¬ Ready (arrive state event) := by
  intro ready
  exact unread (ready event (by simp [arrive]))

theorem failed_history_does_not_acknowledge (state : State) :
    acknowledge state false = state := rfl

theorem acknowledgement_removes_only_prepared (state : State) (event : Nat)
    (retained : event ∈ (acknowledge state true).pending) :
    event ∈ state.pending ∧ event ∉ state.prepared := by
  simpa [acknowledge, List.mem_filter] using retained

theorem unprepared_event_survives_acknowledgement (state : State) (event : Nat)
    (pending : event ∈ state.pending) (unread : event ∉ state.prepared) :
    event ∈ (acknowledge state true).pending := by
  simp [acknowledge, List.mem_filter, pending, unread]

theorem later_input_is_not_acknowledged_by_earlier_turn (state : State) (event : Nat)
    (newIdentity : event ∉ state.pending) :
    event ∈ (acknowledge (arrive (prepare state) event) true).pending := by
  apply unprepared_event_survives_acknowledgement
  · simp [arrive]
  · exact newIdentity

/-- Concrete counterexample to retaining a nonempty old presentation forever. -/
example : ¬ Ready { pending := [1, 2], prepared := [1] } := by
  intro ready
  have impossible := ready 2 (by decide)
  simp at impossible

/-- A fresh preparation recovers without dropping the earlier event. -/
example : Ready (prepare { pending := [1, 2], prepared := [1] }) :=
  preparation_is_ready _

#print axioms preparation_is_ready
#print axioms new_unprepared_input_blocks_provider
#print axioms acknowledgement_removes_only_prepared
#print axioms later_input_is_not_acknowledged_by_earlier_turn

end InputBatch
