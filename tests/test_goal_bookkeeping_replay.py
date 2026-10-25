"""Replay existing Lean Nat models, without modifying their checkout or cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from goal_bookkeeping_support import ROOT, engine, receipt, run


@unittest.skipUnless(all(os.environ.get(key) for key in ("CETTA_BIN", "LEAN_BIN", "LEAN_MODEL_ROOT")),
                     "set CETTA_BIN, LEAN_BIN and LEAN_MODEL_ROOT (pettaclaw models)")
class ReplayTests(unittest.TestCase):
    def test_corrected_partial_write_wake_replays_model_oracle_and_native(self):
        from test_goal_bookkeeping_candidates import CandidateTests, encoded, op
        from test_goal_bookkeeping_oracle import goal
        models = Path(os.environ["LEAN_MODEL_ROOT"])
        names = ("FuelPolicy.lean", "RetryWake.lean")
        before = {name: hashlib.sha256((models / name).read_bytes()).hexdigest() for name in names}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in names:
                shutil.copyfile(models / name, directory / name)
                result = run([os.environ["LEAN_BIN"], "-o", name.replace(".lean", ".olean"), name],
                             directory, cwd=directory, extra_env={"LEAN_PATH": str(directory)})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            shutil.copyfile(ROOT / "verification/GoalWakeFailure.lean", directory / "Wake.lean")
            result = run([os.environ["LEAN_BIN"], "--run", "Wake.lean"], directory,
                         cwd=directory, extra_env={"LEAN_PATH": str(directory)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = json.loads(result.stdout)
        # CandidateTests.compare independently checks corrected Python messages
        # and all file bytes first. No serializer or maintenance writer is stubbed.
        case = CandidateTests()
        _, _, rows = case.compare({"goals.metta": encoded(goal() + "\n")},
            [op("wake", str(token)) for token in (1, 1, 2, 2, 1, 1, 3, 3)],
            faults={"write": "goals_kernel.json.tmp"})
        actual = [[row["value"][1], row["value"][2], row["calls"].get("goal_renames", 0),
                   row["calls"].get("kernel_renames", 0)] for row in rows]
        receipt("wake-lean-replay", {"model_hashes": before, "expected": expected, "actual": actual,
            "scope": "bounded partial-write/repeat replay plus local Lean theorems; no crash-durability claim"})
        self.assertEqual(actual, expected)
        self.assertEqual(before, {name: hashlib.sha256((models / name).read_bytes()).hexdigest() for name in names})

    def test_bounded_nat_models_not_a_float_refinement(self):
        models = Path(os.environ["LEAN_MODEL_ROOT"])
        names = ("StiFloorDecay.lean", "StiMass.lean")
        before = {name: hashlib.sha256((models / name).read_bytes()).hexdigest() for name in names}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            settings = {"LEAN_PATH": str(directory)}
            version = run([os.environ["LEAN_BIN"], "--version"], directory)
            self.assertEqual(version.returncode, 0, version.stderr)
            for name in names:
                shutil.copyfile(models / name, directory / name)
                result = run([os.environ["LEAN_BIN"], "-o", name.replace(".lean", ".olean"), name],
                             directory, cwd=directory, extra_env=settings)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            shutil.copyfile(ROOT / "verification/GoalBookkeepingReplay.lean", directory / "Replay.lean")
            result = run([os.environ["LEAN_BIN"], "--run", "Replay.lean"], directory,
                         cwd=directory, extra_env=settings)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            decoder = json.JSONDecoder()
            text = result.stdout.strip()
            first, end = decoder.raw_decode(text)
            expected = [first, json.loads(text[end:].strip())]
            inputs = list(range(1001)) + [999999, 1000000, 1000001]
            pairs = [900000, 800000, 10, 1000000]
            masses = []
            for _ in range(101):
                masses.append(sum(pairs))
                pairs = [s * 950000 // 1000000 for s in pairs]
            self.assertEqual(expected, [[s * 950000 // 1000000 for s in inputs], masses])
            source = """!(import! &self (library json))
!(import! &self (library lib_import))
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(import! &self ./verification/GoalBookkeepingArithmetic.metta)
"""
            source += '(= (mass $values) (if (== $values ()) 0 (+ (car-atom $values) (mass (cdr-atom $values)))))\n'
            source += '!(py-call (goal_bookkeeping_probe.text "micro" (json:stringify (JsonArray (map-atom (%s) $s (gka:model-decay-micro $s))))))\n' % " ".join(map(str, inputs))
            source += '!(bind! &micro (new-state (900000 800000 10 1000000)))\n'
            for _ in range(101):
                source += '!(py-call (goal_bookkeeping_probe.text "mass" (json:stringify (mass (get-state &micro)))))\n'
                source += '!(change-state! &micro (map-atom (get-state &micro) $s (gka:model-decay-micro $s)))\n'
            result = engine(directory, source)
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [json.loads(json.loads(line)["text"]) for line in
                      (directory / "observations.jsonl").read_text().splitlines()]
            receipt("lean-replay", {"models_sha256": before, "lean_version": version.stdout,
                                    "expected": expected, "actual": [actual[0], actual[1:]],
                                    "scope": "1004 Nat inputs and 101 Nat mass states, NOT Float or writer proof"})
            self.assertEqual([actual[0], actual[1:]], expected)
            self.assertTrue(all(a >= b for a, b in zip(masses, masses[1:])))
        self.assertEqual(before, {name: hashlib.sha256((models / name).read_bytes()).hexdigest() for name in names})


if __name__ == "__main__":
    unittest.main()
