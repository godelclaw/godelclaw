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

def endpoints : List Int := [-1, 0, 1]

def replay : List (List Int) := endpoints.flatMap fun x =>
  endpoints.map fun y => [x, y, numerator x y]

end GoalMoodBounds

def main : IO Unit := IO.println (repr GoalMoodBounds.replay)
