import Std

/-!
Model laws for syntax-only batch envelopes and receipt notices. These do not
verify the physical parser or network delivery. The host must faithfully decode
the command list and retain request identity when submitting a notice.
-/

namespace ManagedFeedback

inductive Envelope (Command : Type) where
  | plain : List Command → Envelope Command
  | batch : List Command → Envelope Command

def commands : Envelope Command → List Command
  | .plain xs | .batch xs => xs

def dispatch (fresh : Bool) (allowed : Command → Bool) (limit : Nat)
    (envelope : Envelope Command) : List Command :=
  if fresh && (commands envelope).all allowed then (commands envelope).take limit else []

theorem envelope_preserves_dispatch (xs : List Command) (fresh : Bool)
    (allowed : Command → Bool) (limit : Nat) :
    dispatch fresh allowed limit (.batch xs) = dispatch fresh allowed limit (.plain xs) := rfl

theorem stale_envelope_runs_nothing (allowed : Command → Bool) (limit : Nat)
    (envelope : Envelope Command) : dispatch false allowed limit envelope = [] := by
  simp [dispatch]

theorem admitted_commands_are_permitted (fresh : Bool) (allowed : Command → Bool)
    (limit : Nat) (envelope : Envelope Command) (command : Command)
    (member : command ∈ dispatch fresh allowed limit envelope) : allowed command = true := by
  unfold dispatch at member
  split at member
  next accepted =>
    have allAllowed : (commands envelope).all allowed = true := (Bool.and_eq_true_iff.mp accepted).2
    exact List.all_eq_true.mp allAllowed command (List.mem_of_mem_take member)
  next rejected => simp at member

theorem dispatch_respects_limit (fresh : Bool) (allowed : Command → Bool)
    (limit : Nat) (envelope : Envelope Command) :
    (dispatch fresh allowed limit envelope).length ≤ limit := by
  unfold dispatch
  split
  next accepted =>
    simp only [List.length_take]
    exact Nat.min_le_left _ _
  next rejected => simp

structure State where
  pending : List Nat
  completed : List Nat
  notices : List Nat

def notify (state : State) (request : Nat) : State :=
  { state with notices := request :: state.notices }

theorem notice_does_not_consume_input (state : State) (request : Nat) :
    (notify state request).pending = state.pending := rfl

theorem notice_does_not_complete_work (state : State) (request : Nat) :
    (notify state request).completed = state.completed := rfl

end ManagedFeedback
