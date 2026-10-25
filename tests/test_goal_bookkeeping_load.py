"""Authorized shared load policy, actual callers and token-aware properties."""
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import engine, environment, receipt
from goal_bookkeeping_probe import bits
from test_goal_bookkeeping_candidates import HEADER, encoded, goal, op, snapshot, trace
import test_goal_bookkeeping_candidates as candidates
import test_goal_bookkeeping_callers as callers
import goals


def moods():
    full = {d: -0.0 if d == "C" else 0.25 for d in goals.MOOD_DIMS}
    return [full, {**full, "Cn": 2.0}, {**full, "Sp": -2.0},
            {**full, "Cn": 2.0, "C": -2.0, "Sp": 1.0000000000000002},
            *[{d: full[d] for d in goals.MOOD_DIMS[:n]} for n in (6, 7, 8)],
            {d: 2.0 for d in goals.MOOD_DIMS}, {}, {"Sp": 2.0},
            {"Sp": -1.0, "C": -0.0, "Cn": 1.0, "Ct": float("1.00000000000000001")},
            {"Sp": "0.50", "Cn": True, "C": "-0.0", "Ct": 0, "A": "2.0"},
            {"Cn": "+Inf", "C": "-Infinity", "Ct": "-NaN", "Sp": 0.5},
            {"Cn": "1e400", "C": "-1e400", "Ct": "9" * 400, "Sp": 0.5},
            {"Cn": "-0", "C": "-000", "Sp": "1."}]


def finite_outputs(files):
    """Test observation only: numeric tokens, not substring banning in notes."""
    def no_constant(token):
        raise AssertionError("nonfinite JSON numeric token: " + token)
    def walk(value):
        if isinstance(value, float):
            assert math.isfinite(value), value
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    if files["goals_kernel.json"]:
        walk(json.loads(base64.b64decode(files["goals_kernel.json"]), parse_constant=no_constant))
    for name in ("goals.metta", "goals_compost.log"):
        if not files[name]:
            continue
        text = base64.b64decode(files[name]).decode()
        for line in text.splitlines():
            if name.endswith(".log"):
                line = line.partition(" ")[2].partition(" ")[2]
            parsed, _, budget, _ = goals._parse([line])
            for g in parsed:
                assert math.isfinite(g["sti"]) and math.isfinite(g["lti"])
            if budget is not None:
                assert math.isfinite(budget)
            if line.startswith("(mood "):
                assert all(math.isfinite(float(v.split(":")[1])) for v in line[6:-1].split())


class LoadPolicyTests(unittest.TestCase):
    def test_load_and_read_only_affect_preserve_order_values_and_write_nothing(self):
        for mood in moods():
            with self.subTest(mood=mood), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                path, kernel = directory / "goals.metta", directory / "goals_kernel.json"
                state = {"extra": "NaN Infinity are legitimate words", "mood": mood,
                         "streaks": {"untouched": {"streak": 2}}, "log_offset": 0}
                kernel.write_text(json.dumps(state))
                with mock.patch.dict(os.environ, environment(directory), clear=True):
                    expected = goals._load_kernel_state(kernel)
                    affect = goals.affect_view(str(path))
                before = snapshot(directory)
                program = HEADER.replace("goal_bookkeeping_probe.candidate_setup", "goal_bookkeeping_probe.disable_affect_view")
                program += '''!(py-call (goal_bookkeeping_probe.text "load" (json:stringify-python (gv:kernel (gv:path)))))
!(py-call (goal_bookkeeping_probe.text "affect" (gv:affect (gv:path))))
'''
                result = engine(directory, program)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                rows = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
                receipt("load-view-policy", {"input": state, "expected_load": json.dumps(expected),
                    "actual": rows, "expected_affect": affect, "before": before, "after": snapshot(directory)})
                self.assertEqual(rows[0]["text"], json.dumps(expected))
                self.assertEqual(rows[1]["text"], affect)
                self.assertEqual(snapshot(directory), before)
                self.assertFalse((directory / "goals.metta.tmp").exists())
                self.assertFalse((directory / "goals_kernel.json.tmp").exists())

    def test_immediate_real_skills_after_filter_native_and_actual_swi(self):
        for native in (True, False):
            for mood in moods():
                with self.subTest(native=native, mood=mood):
                    rows, _ = callers.RealCallerTests().compare({"goals.metta": encoded(goal() + "\n"),
                        "goals_kernel.json": encoded(json.dumps({"mood": mood}))}, [
                        {"kind": "write", "text": "(goal-budget 0.5)"},
                        {"kind": "drop", "name": "alpha", "reason": "retire"}], native=native)
                    self.assertEqual(rows[0]["value"], "budget set to 0.5")
                    self.assertIn("composted", rows[1]["value"])
                    self.assertEqual(rows[0]["files"]["goals_kernel.json"], rows[1]["files"]["goals_kernel.json"])

    def test_no_samples_then_seeding_and_subsequent_samples(self):
        for mood in moods():
            with self.subTest(mood=mood):
                log = (json.dumps({"kind": "outbound", "text": trace(".5", 6)}) + "\n").encode()
                rows, _, _ = candidates.CandidateTests().compare({"goals.metta": encoded(goal() + "\n"),
                    "goals_kernel.json": encoded(json.dumps({"mood": mood}))}, [op("kernel"),
                    op("kernel", files={"updates.jsonl": encoded(log)}),
                    op("kernel", files={"updates.jsonl": encoded(log + log)})])
                for row in rows:
                    finite_outputs(row["files"])

    def test_finite_overflow_sized_json_numerals_and_huge_integers(self):
        for token in ("1e400", "-1e400", "9" * 400, "-" + "9" * 400):
            with self.subTest(token=token):
                state = '{"streaks": {}, "log_offset": 0, "mood": {"Cn": ' + token + ', "C": 0.5}}'
                candidates.CandidateTests().compare({"goals.metta": encoded(goal() + "\n"),
                    "goals_kernel.json": encoded(state)}, [op("write", "(goal-budget 0.5)"), op("kernel")])

    def test_stored_duplicates_last_wins_before_filter_and_parsed_endpoints(self):
        raw = ('{"extra": "unchanged", "mood": {"Cn": 2, "C": -0.0, "Cn": 0.5, '
               '"Sp": 0.5, "Sp": 2, "Ct": 1.00000000000000001, '
               '"I": -1.00000000000000001, "A": 1.0000000000000002}}')
        rows, _, _ = candidates.CandidateTests().compare({"goals.metta": encoded(goal() + "\n"),
            "goals_kernel.json": encoded(raw)}, [op("write", "(goal-budget 0.5)"), op("kernel")])
        mood = json.loads(base64.b64decode(rows[-1]["files"]["goals_kernel.json"]))["mood"]
        self.assertEqual(mood["Cn"], 0.5)
        self.assertEqual(mood["Sp"], 0.0)
        self.assertEqual(mood["Ct"], 1.0)
        self.assertEqual(mood["I"], -1.0)
        self.assertEqual(mood["A"], 0.0)

    def test_malformed_non_numeric_values_are_not_silently_sanitized(self):
        for value in (None, "bad", [1], {"bad": 1}, "\x1f2.0", "\x1c+Inf"):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                kernel = directory / "goals_kernel.json"
                state = {"mood": {"Cn": value}}
                kernel.write_text(json.dumps(state))
                with mock.patch.dict(os.environ, environment(directory), clear=True):
                    self.assertEqual(goals._load_kernel_state(kernel), state)
                program = HEADER + '!(py-call (goal_bookkeeping_probe.text "malformed" (json:stringify-python (gv:kernel (gv:path)))))\n'
                result = engine(directory, program)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                row = json.loads((directory / "observations.jsonl").read_text())
                self.assertEqual(row["text"], json.dumps(state))

    def test_100_real_caller_tracks_and_numeric_finiteness_not_note_words(self):
        from test_goal_bookkeeping_domain import affect_cases, log_record
        initial = {"goals.metta": encoded(goal().replace("generated 日本 🙂 é", "NaN Infinity -Infinity 日本") + "\n"),
                   "goals_kernel.json": encoded(json.dumps({"mood": {"Cn": 2.0, "C": -0.0, "Sp": -2.0}}))}
        log, steps = b"", []
        cases = affect_cases()
        for i in range(100):
            if i == 50:
                log = b""
            log += log_record(cases[i % len(cases)][1])
            steps.append({"kind": "heartbeat", "files": {"updates.jsonl": encoded(log)}})
        steps.append({"kind": "drop", "name": "alpha", "reason": "retire"})
        for native in (True, False):
            rows, _ = callers.RealCallerTests().compare(initial, steps, native=native)
            track = []
            for row in rows:
                finite_outputs(row["files"])
                state = json.loads(base64.b64decode(row["files"]["goals_kernel.json"]))
                track.append({dim: bits(value) for dim, value in state["mood"].items()})
            receipt("loaded-mood-reparse-bits", {"native": native, "track": track,
                "scope": "101 saved/reparsed state observations; exact oracle/native bytes checked separately; not in-memory track"})
            self.assertIn(b"NaN Infinity -Infinity", base64.b64decode(rows[-1]["files"]["goals_compost.log"]))


if __name__ == "__main__":
    unittest.main()
