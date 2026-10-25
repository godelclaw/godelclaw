/-!
Mathematical convexity only: numerator of (69*x + 31*y)/100 on
integer-scaled coordinates. This is NOT a theorem about binary64 rounding,
legacy NaN/Infinity, native parsing, JSON ingestion or file persistence.
The initial coordinate and every admitted new coordinate must be bounded.
-/
namespace GoalMoodBounds

def numerator (x y : Int) : Int := 69 * x + 31 * y

theorem convexNumerator (n x y : Int)
    (hx : -n ≤ x ∧ x ≤ n) (hy : -n ≤ y ∧ y ≤ n) :
    -(100 * n) ≤ numerator x y ∧ numerator x y ≤ 100 * n := by
  dsimp [numerator]
  omega

-- Mathematical counterpart of dropping out-of-range stored coordinates.
-- Does not model binary64 coercion, JSON parsing, NaN or persistence.
def admitted (n : Int) (xs : List Int) : List Int :=
  xs.filter fun x => decide (-n ≤ x ∧ x ≤ n)

theorem admittedBound (n x : Int) (xs : List Int) (h : x ∈ admitted n xs) :
    -n ≤ x ∧ x ≤ n := by
  simp only [admitted, List.mem_filter, decide_eq_true_eq] at h
  exact h.2

theorem missingZeroBound (n : Int) (nonneg : 0 ≤ n) : -n ≤ 0 ∧ 0 ≤ n := by
  omega

def endpoints : List Int := [-1, 0, 1]

def replay : List (List Int) := endpoints.flatMap fun x =>
  endpoints.map fun y => [x, y, numerator x y]

end GoalMoodBounds

def main : IO Unit := do
  IO.println (repr GoalMoodBounds.replay)
  IO.println (repr (GoalMoodBounds.admitted 100 [-200, -100, 0, 100, 200]))
