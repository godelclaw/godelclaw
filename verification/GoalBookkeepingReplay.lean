import StiFloorDecay
import StiMass

/-!
Project 04b bounded executable replay of the existing Nat models. Build only
copies in a disposable directory. This is NOT a theorem that IEEE Float decay,
four-decimal serialization or filesystem/wake behavior refines these models.
Their floor division is intentionally different from goals.py's double decay.
-/

def replayInputs : List Nat := List.range 1001 ++ [999999, 1000000, 1000001]

def floorDecay : StiMass.Decay := ⟨decayMicro, decayMicro_le⟩

def main : IO Unit := do
  IO.println (repr (replayInputs.map decayMicro))
  IO.println (repr ((List.range 101).map fun n =>
    StiMass.V (StiMass.iterateDecay floorDecay n [900000, 800000, 10, 1000000])))
