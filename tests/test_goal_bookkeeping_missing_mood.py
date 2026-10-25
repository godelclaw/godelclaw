"""Authorized missing-dimension semantics; old KeyError receipts preserved.

Python-only checks here complement separate frozen native/caller parity tests.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import environment, receipt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import goals


GOAL = ('(goal generated Work sti:0.8 lti:0.3 vibes:(care) '
        'blocked-by:none last-verified:never note:"generated")\n')


class MissingMoodPolicyTests(unittest.TestCase):
    def test_read_only_affect_drops_invalid_and_defaults_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with mock.patch.dict(os.environ, environment(directory), clear=True):
                path, kernel = directory / "goals.metta", directory / "goals_kernel.json"
                state = json.dumps({"mood": {"Cn": 2.0, "C": -0.0}})
                kernel.write_text(state)
                actual = goals.affect_view(str(path))
                self.assertTrue(actual.startswith("gamma:0.69 Cn:0 C:-0 Ct:0"))
                self.assertTrue(actual.endswith("Sp:0"))
                self.assertEqual(kernel.read_text(), state)
                self.assertFalse(path.exists())
                receipt("missing-mood-view", {"actual": actual, "state_unchanged": True,
                    "scope": "authorized shared-loader read-only consequence; no writes"})

    def test_partial_mood_kernel_fills_missing_with_zero_without_samples(self):
        for missing in ("Cn", "Sp"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                mood = {d: -0.0 if d == "C" else 0.2 for d in goals.MOOD_DIMS if d != missing}
                with mock.patch.dict(os.environ, environment(directory), clear=True):
                    (directory / "goals.metta").write_text(GOAL)
                    kernel = directory / "goals_kernel.json"
                    kernel.write_text(json.dumps({"streaks": {}, "log_offset": 0, "mood": mood}))
                    self.assertEqual(goals._load_kernel_state(kernel)["mood"], mood)
                    result = goals.kernel_pass()
                    self.assertNotIn("error", result)
                    actual = json.loads(kernel.read_text())["mood"]
                    self.assertEqual(actual[missing], 0.0)
                    self.assertEqual(list(actual), list(goals.MOOD_DIMS))
                    self.assertEqual(json.dumps(actual["C"]), "-0.0")
                    receipt("missing-mood-kernel", {"fixture_mood": mood, "result": result,
                        "persisted_mood": actual, "scope": "authorized Python missing-dimension behavior"})

    def test_partial_mood_skills_complete_and_drop_retires_after_replacement(self):
        for operation in ("write", "drop"):
            for missing in ("Cn", "Sp"):
                with self.subTest(operation=operation, missing=missing), tempfile.TemporaryDirectory() as temp:
                    directory = Path(temp)
                    mood = {d: 0.2 for d in goals.MOOD_DIMS if d != missing}
                    state = json.dumps({"streaks": {}, "log_offset": 0, "mood": mood})
                    with mock.patch.dict(os.environ, environment(directory), clear=True), \
                         mock.patch.object(goals, "_retire_in_ledger", return_value=0) as retire, \
                         mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"):
                        path, kernel = directory / "goals.metta", directory / "goals_kernel.json"
                        path.write_text(GOAL)
                        kernel.write_text(state)
                        if operation == "write":
                            result = goals.goal_write("(goal-budget 0.5)")
                            self.assertEqual(result, "budget set to 0.5")
                            retire.assert_not_called()
                        else:
                            result = goals.goal_drop("generated", "retire")
                            self.assertEqual(result, "goal generated composted (retire) — remember a compost note")
                            retire.assert_called_once_with("generated", "retire")
                        self.assertIn(missing + ":0", path.read_text())
                        self.assertEqual(kernel.read_text(), state)
                        compost = directory / "goals_compost.log"
                        self.assertEqual(compost.exists(), operation == "drop")
                        if compost.exists():
                            self.assertTrue(compost.read_text().startswith("2026-01-02T03:04:05 retire "))
                        receipt("missing-mood-skill", {"operation": operation, "fixture_mood": mood,
                            "result": result, "goals_replaced": True,
                            "kernel_unchanged": True, "compost_appended": compost.exists(),
                            "scope": "authorized nonempty partial-mood zero completion; no native assertion here"})

    def test_empty_mood_stays_unseeded_then_first_sample_seeds(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with mock.patch.dict(os.environ, environment(directory), clear=True):
                (directory / "goals.metta").write_text(GOAL)
                kernel = directory / "goals_kernel.json"
                kernel.write_text('{"streaks": {}, "log_offset": 0, "mood": {}}')
                self.assertNotIn("error", goals.kernel_pass())
                self.assertEqual(json.loads(kernel.read_text())["mood"], {})
                trace = "⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5⟩"
                (directory / "updates.jsonl").write_text(json.dumps({"kind": "outbound", "text": trace}) + "\n")
                self.assertNotIn("error", goals.kernel_pass())
                mood = json.loads(kernel.read_text())["mood"]
                self.assertEqual(mood, {d: 0.5 if d in goals.MOOD_DIMS[:6] else 0.0 for d in goals.MOOD_DIMS})
                receipt("missing-mood-seeding", {"persisted_mood": mood,
                    "scope": "existing empty-state Python seeding, not a new load rule"})


if __name__ == "__main__":
    unittest.main()
