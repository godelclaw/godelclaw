"""Bounded NEW affect observations; no authorization to sanitize old state."""
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, engine, environment, receipt, run
from goal_bookkeeping_probe import bits
from test_goal_bookkeeping_candidates import HEADER, encoded, goal, op, trace
import test_goal_bookkeeping_candidates as candidates
import goals


def log_record(text):
    return (json.dumps({"kind": "outbound", "text": text}, ensure_ascii=False) + "\n").encode()


def affect_cases():
    six = "Cn:1 C:-1 Ct:0 I:.5 J:-.5 A:-0"
    return [
        ("huge-positive-negative", "⋄⟨" + six + " S:" + "9" * 400 + ".0 Co:-" + "9" * 400 + ".0⟩"),
        ("five-valid-plus-invalid", "⋄⟨Cn:1 C:-1 Ct:0 I:.5 J:-.5 A:1.0000000000000002⟩"),
        ("six-valid-plus-invalid", "⋄⟨" + six + " S:-1.0000000000000002⟩"),
        ("last-valid-wins", "⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 A:.7 Cn:2 C:.8 C:-2⟩"),
        ("invalid-first-does-not-reserve-order", "⋄⟨Cn:" + "9" * 400 + ".0 C:.3 Ct:.4 I:.5 J:.6 A:.7 Cn:.8⟩"),
        ("distinct-not-counted-twice", "⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 Cn:.8 A:2⟩"),
        ("rounded-endpoint", "⋄⟨" + six + " S:1.00000000000000001 Co:-1.00000000000000001⟩"),
        ("long-fraction", "⋄⟨" + six + " S:0." + "9" * 400 + " Co:-0." + "9" * 400 + "⟩"),
        ("exact-endpoints", trace("1") + trace("-1")),
    ]


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set authorized CETTA_BIN")
class AffectDomainTests(unittest.TestCase):
    def test_range_before_distinct_count_last_valid_and_seed_bits(self):
        for label, text in affect_cases():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                path = directory / "updates.jsonl"
                path.write_bytes(log_record(text))
                with mock.patch.dict(os.environ, environment(directory), clear=True):
                    samples, offset = goals._mood_samples(str(path), 0)
                program = HEADER + '''!(let (GkSamples $samples $offset) (gk:mood-samples (gk:default-log) 0)
 (py-call (goal_bookkeeping_probe.text "samples" (json:stringify-python (JsonArray ((JsonArray $samples) $offset))))))
'''
                result = engine(directory, program)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actual_text = json.loads((directory / "observations.jsonl").read_text())["text"]
                expected = json.dumps([samples, offset])
                receipt("affect-domain", {"case": label, "input": text, "expected": expected, "actual": actual_text,
                    "sample_bits": [{d: bits(v) for d, v in sample.items()} for sample in samples]})
                self.assertEqual(actual_text, expected)
                self.assertTrue(all(-1 <= v <= 1 and math.isfinite(v) for s in samples for v in s.values()))
                if label in ("five-valid-plus-invalid", "distinct-not-counted-twice"):
                    self.assertEqual(samples, [])
                if label == "last-valid-wins":
                    self.assertEqual(samples[0]["Cn"], 0.2)
                    self.assertEqual(samples[0]["C"], 0.8)
                if label == "invalid-first-does-not-reserve-order":
                    self.assertEqual(list(samples[0]), ["C", "Ct", "I", "J", "A", "Cn"])
                if label == "six-valid-plus-invalid":
                    self.assertEqual(len(samples[0]), 6)
                    self.assertEqual(bits(samples[0]["A"]), "8000000000000000")

    def test_first_seed_and_100_pass_ema_reparse_bounded_from_bounded_initial(self):
        log = b""
        steps = []
        cases = affect_cases()
        for index in range(100):
            # Valid endpoints plus rejected huge observations exercise the
            # production sampler every pass, not only a prebuilt vector helper.
            log += log_record(cases[index % len(cases)][1])
            steps.append(op("kernel", files={"updates.jsonl": encoded(log)}))
        case = candidates.CandidateTests()
        actual, _, _ = case.compare({"goals.metta": encoded(goal() + "\n")}, steps)
        for row in actual:
            mood = json.loads(base64.b64decode(row["files"]["goals_kernel.json"]))["mood"]
            self.assertTrue(all(math.isfinite(v) and -1 <= v <= 1 for v in mood.values()))

    def test_parsed_goal_bounds_finite_mass_and_budget(self):
        text = "\n".join(["(goal-budget 0.5)", goal("high", sti="9" * 400, lti="9" * 400),
                          goal("negative", sti="-" + "9" * 400, lti="-1"),
                          *[goal("g%d" % i, sti="1", lti="0") for i in range(30)]]) + "\n"
        candidates.CandidateTests().compare({"goals.metta": encoded(text)}, [op("kernel"), op("kernel")])
        parsed = goals._parse(text.splitlines())[0]
        self.assertTrue(all(0 <= g["sti"] <= 1 and 0 <= g["lti"] <= 1 for g in parsed))
        self.assertLessEqual(sum(g["sti"] for g in parsed), len(parsed))

    def test_legacy_out_of_range_initial_mood_is_dropped_then_seeded(self):
        state = {"streaks": {}, "log_offset": 0, "mood": {d: 2.0 for d in goals.MOOD_DIMS}}
        actual, _, _ = candidates.CandidateTests().compare({"goals.metta": encoded(goal() + "\n"),
            "goals_kernel.json": encoded(json.dumps(state)), "updates.jsonl": encoded(log_record(trace("0")))}, [op("kernel")])
        mood = json.loads(base64.b64decode(actual[0]["files"]["goals_kernel.json"]))["mood"]
        self.assertEqual(mood["Cn"], 0.0)
        receipt("legacy-mood-precondition", {"initial": state, "actual": mood,
            "claim": "authorized load filter drops all invalid dimensions; first sample seeds, not old 2.0-to-1.38"})

    def test_actual_binary64_endpoints_signed_zero_and_100_pass_track(self):
        expected, program = [], HEADER
        for index, (old, sample) in enumerate((x, y) for x in (-1.0, -0.0, 0.0, 1.0) for y in (-1.0, -0.0, 0.0, 1.0)):
            value = 0.69 * old + (1 - 0.69) * sample
            self.assertTrue(-1 <= value <= 1)
            expected.append({"label": str(index), "bits": [bits(value)]})
            program += '!(py-call (goal_bookkeeping_probe.observe "%d" (gka:mood (%s) (%s))))\n' % (index, repr(old), repr(sample))
        value = 1.0
        program += '!(bind! &endpoint-track (new-state (1.0)))\n'
        for index in range(100):
            sample = -1.0 if index % 2 else 1.0
            value = 0.69 * value + (1 - 0.69) * sample
            expected.append({"label": "track-%d" % index, "bits": [bits(value)]})
            program += '!(change-state! &endpoint-track (gka:mood (get-state &endpoint-track) (%s)))\n' % repr(sample)
            program += '!(py-call (goal_bookkeeping_probe.observe "track-%d" (get-state &endpoint-track)))\n' % index
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, program)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
            receipt("binary64-mood-endpoints", {"expected": expected, "actual": actual,
                "scope": "actual double operations, distinct from mathematical convexity theorem"})
            self.assertEqual(actual, expected)

    @unittest.skipUnless(os.environ.get("LEAN_BIN"), "set LEAN_BIN for disposable bound model check")
    def test_lean_mathematical_convexity_model(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "Bounds.lean"
            source.write_bytes((ROOT / "verification/GoalMoodBounds.lean").read_bytes())
            result = run([os.environ["LEAN_BIN"], "--run", str(source)], directory, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = result.stdout.splitlines()
            actual = json.loads(lines[0])
            admitted = json.loads(lines[1])
            expected = [[x, y, 69 * x + 31 * y] for x in (-1, 0, 1) for y in (-1, 0, 1)]
            receipt("mood-bound-model", {"expected": expected, "actual": actual, "admitted": admitted,
                "scope": "integer-scaled mathematical numerator, NOT binary64 bit proof"})
            self.assertEqual(actual, expected)
            self.assertEqual(admitted, [-100, 0, 100])
            self.assertTrue(all(-100 <= row[2] <= 100 for row in actual))
