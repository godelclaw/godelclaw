import Std

/- Generated transition function from StreamEvents.module.
   Theorems check lifecycle laws; adapter correspondence is tested separately. -/
namespace StreamingIO
inductive State where
  | Forming
  | Streaming
  | Running
  | Finished
  deriving DecidableEq, Repr
inductive Event where
  | ProviderText
  | ProviderTool
  | ExecutorStart
  | ExecutorFinish
  | TerminalFinish
  | Interrupt
  deriving DecidableEq, Repr
def step : State → Event → State
  | .Forming, .ProviderText => .Streaming
  | .Forming, .ProviderTool => .Forming
  | .Forming, .ExecutorStart => .Running
  | .Forming, .ExecutorFinish => .Finished
  | .Forming, .TerminalFinish => .Finished
  | .Forming, .Interrupt => .Finished
  | .Streaming, .ProviderText => .Streaming
  | .Streaming, .ProviderTool => .Streaming
  | .Streaming, .ExecutorStart => .Running
  | .Streaming, .ExecutorFinish => .Finished
  | .Streaming, .TerminalFinish => .Finished
  | .Streaming, .Interrupt => .Finished
  | .Running, .ProviderText => .Running
  | .Running, .ProviderTool => .Running
  | .Running, .ExecutorStart => .Running
  | .Running, .ExecutorFinish => .Finished
  | .Running, .TerminalFinish => .Finished
  | .Running, .Interrupt => .Finished
  | .Finished, .ProviderText => .Finished
  | .Finished, .ProviderTool => .Finished
  | .Finished, .ExecutorStart => .Finished
  | .Finished, .ExecutorFinish => .Finished
  | .Finished, .TerminalFinish => .Finished
  | .Finished, .Interrupt => .Finished

theorem finished_is_absorbing (e : Event) : step .Finished e = .Finished := by
  cases e <;> rfl
theorem running_requires_start (s : State) (e : Event) (h : step s e = .Running) :
    s = .Running ∨ e = .ExecutorStart := by
  cases s <;> cases e <;> simp_all [step]
structure Cursor where
  sequence : Nat
  state : State
  deriving DecidableEq
def observe (c : Cursor) (seq : Nat) (e : Event) : Cursor :=
  if seq ≤ c.sequence then c else ⟨seq, step c.state e⟩
theorem replay_idempotent (c : Cursor) (seq : Nat) (e : Event) :
    observe (observe c seq e) seq e = observe c seq e := by
  unfold observe
  split <;> simp_all
theorem cursor_never_regresses (c : Cursor) (seq : Nat) (e : Event) :
    c.sequence ≤ (observe c seq e).sequence := by
  unfold observe
  split <;> simp_all <;> omega
theorem closed_trace_stays_closed (events : List Event) :
    events.foldl step .Finished = .Finished := by
  induction events with
  | nil => rfl
  | cons e es ih => simpa [List.foldl, finished_is_absorbing] using ih
end StreamingIO
