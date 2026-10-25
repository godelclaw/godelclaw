"""Project 04b explicit JSON API gate and bounded arithmetic qualification.

This suite is NOT writer/caller parity; separate suites exercise branch wiring.
The historical default-emitter failure remains in the preserved report/receipts.
The new explicit API gate keeps the same expected bytes. Strict gap tests remain
ordinary failures, not expectedFailure or skips disguising incomplete parity.
"""
import base64
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, engine, engine_info, environment, receipt
from goal_bookkeeping_probe import bits

sys.path.insert(0, str(ROOT / "src"))
import goals


def atom(value):
    if isinstance(value, (list, tuple)):
        return "(" + " ".join(atom(item) for item in value) + ")"
    return "GkaAbsent" if value is None else repr(value)


def advance(pairs, budget):
    result = []
    for sti, lti in pairs:
        sti *= goals.DECAY
        lti = lti + goals.ALPHA * max(0.0, sti - lti)
        result.append([sti, lti])
    total = sum(pair[0] for pair in result)
    if total > budget > 0:
        scale = budget / total
        for pair in result:
            pair[0] *= scale
    return result


HEADER = """!(import! &self (library lib_import))
!(import! &self ./verification/GoalBookkeepingArithmetic.metta)
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(py-call (goal_bookkeeping_probe.disable_writers))
"""


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to reviewer TEST engine")
class ArithmeticTests(unittest.TestCase):
    def test_neumaier_signed_zero_boundaries_and_randomized_python312(self):
        rng = random.Random(404204)
        cases = [([], -0.0), ([-0.0], -0.0), ([0.0], -0.0),
                 ([1.0, -1.0], -0.0), ([1e100, 1.0, -1e100], 0),
                 ([1e308, -1e308, 5e-324], 0),
                 ([5e-324, 5e-324, -5e-324], 0),
                 ([1.7976931348623157e308, -1.7976931348623157e308], 0)]
        for _ in range(1000):
            values = [math.ldexp(rng.uniform(-1, 1), rng.randint(-1070, 950))
                      for _ in range(rng.randrange(61))]
            cases.append((values, rng.choice((0, 0.0, -0.0, 1.0, -1.0))))
        expected = []
        lines = [HEADER]
        for index, (values, start) in enumerate(cases):
            expected.append({"label": str(index), "bits": bits(sum(values, start))})
            lines.append('!(py-call (goal_bookkeeping_probe.observe "%d" (gka:sum-values %s %s)))' %
                         (index, atom(values), atom(start)))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, "\n".join(lines))
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
            receipt("neumaier-bits", {"cases": cases, "expected": expected, "actual": actual,
                "scope": "1008 locally executed finite lists; generic overflow retained separately as out-of-domain diagnostic"})
            self.assertEqual(actual, expected)

    def test_compensated_sum_counterexample(self):
        rng = random.Random(4042)
        initial = [[0.9, 0.5], [0.8, 0.3], [0.00001, 0.0], [1.0, 0.0]]
        initial += [[rng.random(), rng.random()] for _ in range(5)]
        decayed = [pair[0] * goals.DECAY for pair in initial]
        naive = 0
        for value in decayed:
            naive += value
        expected = sum(decayed)
        self.assertEqual(bits(expected), "4013dc2f5304f3aa")
        self.assertEqual(bits(naive), "4013dc2f5304f3a9")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, HEADER +
                '!(py-call (goal_bookkeeping_probe.observe "compensated" (gka:total %s 0)))\n' %
                atom([[value, 0.0] for value in decayed]))
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = json.loads((directory / "observations.jsonl").read_text())["bits"]
            receipt("compensated-sum", {"input_bits": bits(decayed), "naive_bits": bits(naive),
                                        "python_sum_bits": bits(expected), "native_bits": actual})
            self.assertEqual(actual, bits(expected))

    def test_100_actual_oracle_passes_reparse_track(self):
        rows = []
        lines = [HEADER]
        original_emit = goals._emit
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "goals.metta"
            path.write_text('(goal-budget 0.5)\n' + '\n'.join(
                '(goal g%d Work sti:%s lti:0.1 vibes:() blocked-by:none '
                'last-verified:never note:"generated")' % (i, value)
                for i, value in enumerate(("0.9", "0.8", "0.00001", "1.0"))) + '\n')
            def observe(parsed, flags, budget, other, mood):
                rows.append([[g["sti"], g["lti"]] for g in parsed])
                return original_emit(parsed, flags, budget, other, mood)
            oracle_env = environment(directory)
            oracle_env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
            with mock.patch.dict(os.environ, oracle_env, clear=True), \
                 mock.patch.object(goals, "_emit", side_effect=observe):
                for index in range(100):
                    inputs = [[g["sti"], g["lti"]] for g in goals._parse(path.read_text().splitlines())[0]]
                    result = goals.kernel_pass()
                    self.assertNotIn("error", result)
                    lines += ['!(py-call (goal_bookkeeping_probe.observe "oracle-%d" (gka:advance %s 0.5)))' %
                              (index, atom(inputs))]
            result = engine(directory, "\n".join(lines))
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
            expected = [{"label": "oracle-%d" % index, "bits": bits(row)} for index, row in enumerate(rows)]
            receipt("actual-kernel-arithmetic", {"expected": expected, "actual": actual,
                "goal_bytes_base64": base64.b64encode(path.read_bytes()).decode(),
                "scope": "native arithmetic against actual oracle pre-emit values; NO native writer parity"})
            self.assertEqual(actual, expected)

    def test_100_pass_bits_in_memory_and_goal_reparse(self):
        rng = random.Random(4042)
        initial = [[0.9, 0.5], [0.8, 0.3], [0.00001, 0.0], [1.0, 0.0]]
        initial += [[rng.random(), rng.random()] for _ in range(5)]
        expected = []
        lines = [HEADER, "!(bind! &pairs (new-state %s))" % atom(initial),
                 "!(bind! &mood (new-state ()))"]
        previous = list(initial)
        reparsed = list(initial)
        mood = None
        differences = []
        for index in range(100):
            budget = (0.5, 1.0, 3.0, 0.0, -1.0, 10.0)[index % 6]
            previous = advance(previous, budget)
            one_pass = advance(reparsed, budget)
            if bits(previous) != bits(one_pass):
                differences.append(index + 1)
            # Real goal serialization has FOUR decimal places; do not claim
            # in-memory iteration equals arithmetic after each file reparse.
            reparsed = [[float(goals._r(x)) for x in pair] for pair in one_pass]
            sample = [None if (index + d) % 7 == 0 else
                      (0.00001 if d == 0 else rng.uniform(-1, 1)) for d in range(9)]
            if mood is None:
                mood = [0.0 if x is None else x for x in sample]
            else:
                mood = [goals.GAMMA * old + (1 - goals.GAMMA) * (old if s is None else s)
                        for old, s in zip(mood, sample)]
            lines += ["!(change-state! &pairs (gka:advance (get-state &pairs) %s))" % budget,
                      '!(py-call (goal_bookkeeping_probe.observe "memory-%d" (get-state &pairs)))' % index,
                      '!(py-call (goal_bookkeeping_probe.observe "reparse-%d" (gka:advance %s %s)))' %
                      (index, atom(initial if index == 0 else before_reparse), budget),
                      "!(change-state! &mood %s)" % (
                          "(gka:seed %s)" % atom(sample) if index == 0 else
                          "(gka:mood (get-state &mood) %s)" % atom(sample)),
                      '!(py-call (goal_bookkeeping_probe.observe "mood-%d" (get-state &mood)))' % index]
            expected += [{"label": "memory-%d" % index, "bits": bits(previous)},
                         {"label": "reparse-%d" % index, "bits": bits(one_pass)},
                         {"label": "mood-%d" % index, "bits": bits(mood)}]
            before_reparse = reparsed
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, "\n".join(lines))
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
            receipt("arithmetic", {"expected": expected, "actual": actual,
                                   "passes_with_memory_reparse_difference": differences,
                                   "scope": "finite generated fixtures; bridge observes bits only"})
            self.assertEqual(actual, expected)
            self.assertTrue(differences)


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to reviewer TEST engine")
class PrimitiveTests(unittest.TestCase):
    def test_native_local_time_separate_from_frozen_compost(self):
        original = os.environ.get("TZ")
        try:
            for zone in ("UTC0", "JST-9"):
                with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"TZ": zone}):
                    time.tzset()
                    directory = Path(temp)
                    start = time.time()
                    result = engine(directory, '''!(import! &self (library lib_import))
!(import! &self (library str))
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(py-call (goal_bookkeeping_probe.text "local" (str:symbol->string (format-time "%Y-%m-%dT%H:%M:%S"))))
''')
                    end = time.time()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    actual = json.loads((directory / "observations.jsonl").read_text())["text"]
                    allowed = [time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
                               for t in range(int(start), int(end) + 1)]
                    receipt("local-time", {"TZ": zone, "start": start, "end": end,
                                           "actual": actual, "allowed": allowed})
                    self.assertIn(actual, allowed)
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to reviewer TEST engine")
class CapabilityTests(unittest.TestCase):
    def test_nonfinite_ascii_controls_and_order_explicit_api(self):
        expressions = ['(json:stringify-python (py-call (goal_bookkeeping_probe.nonfinite_fixture "%s")))' % name
                       for name in ("inf", "-inf", "nan")]
        value = {"z": "日🙂\u001c\n\"\\", "a": -0.0, "middle": [0.1, 2.5e-7]}
        expressions += ["(json:stringify-python (json:parse %s))" % json.dumps(json.dumps(value))]
        expected = [json.dumps(float("inf")), json.dumps(float("-inf")), json.dumps(float("nan")), json.dumps(value)]
        program = '''!(import! &self (library json))
!(import! &self (library lib_import))
!(import! &self ./tests/goal_bookkeeping_probe.py)
'''
        program += "\n".join('!(py-call (goal_bookkeeping_probe.text "%d" %s))' % (i, expr)
                             for i, expr in enumerate(expressions))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, program)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = [json.loads(line)["text"] for line in (directory / "observations.jsonl").read_text().splitlines()]
            receipt("json-python-extensions", {"expressions": expressions, "expected": expected, "actual": actual,
                "input_source": "explicit test-only Python DOUBLE fixture; emitter is native, no Python serializer; NOT arithmetic or writer input qualification"})
            self.assertEqual(actual, expected)

    def test_python_json_bytes_required_before_writers(self):
        values = [0.1, 1e-05, 2.5e-07, 1.0, -0.0, 1e16, 1e-4, 1.2345678901234567]
        # Python records native STRINGS, never converts/serializes their numbers.
        program = """!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library lib_import))
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(println! (json:capabilities))
"""
        expressions = ["(json:stringify-python (JsonArray %s))" % atom(values),
                       "(json:stringify-reference (JsonArray %s))" % atom(values)]
        expressions += ["(json:stringify-python %s)" % atom(x) for x in values]
        expressions += ["(repr %s)" % atom(x) for x in values]
        state = {"streaks": {"日🙂": {"blocked": "待", "streak": 2}},
                 "log_offset": 0, "mood": {"Cn": 1e-05}, "last_human_wake": "abc"}
        expressions += ["(json:stringify-python (json:parse %s))" % json.dumps(json.dumps(state)),
                        "(json:pretty (json:parse %s))" % json.dumps(json.dumps(state))]
        program += "\n".join('!(py-call (goal_bookkeeping_probe.text "%d" %s))' % (i, expression)
                             for i, expression in enumerate(expressions))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            info = engine_info(directory)
            self.assertEqual(info["version_returncode"], 0)
            result = engine(directory, program)
            after = engine_info(directory)
            self.assertEqual(info["sha256"], after["sha256"])
            self.assertEqual(info["version_stdout"], after["version_stdout"])
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [json.loads(line)["text"] for line in
                      (directory / "observations.jsonl").read_text().splitlines()]
            expected = [json.dumps(values), json.dumps(values)] + [json.dumps(x) for x in values]
            expected += [repr(x) for x in values] + [json.dumps(state), json.dumps(state)]
            # repr/reference/pretty are diagnostics, not mandatory APIs. The
            # production candidate uses the checkpoint's explicit Python API.
            # Old default/reference/pretty/repr outputs remain diagnostics.
            required = [0, *range(2, 2 + len(values)), len(expressions) - 2]
            qualified = len(actual) == len(expected) and all(actual[i] == expected[i] for i in required)
            receipt("capability", {"expressions": expressions, "expected": expected, "actual": actual,
                                    "required_indices": required,
                                    "api": "json:stringify-python", "engine_before": info, "engine_after": after,
                                    "required": "Unchanged Python json.dump bytes; explicit native API, no serializer workaround",
                                    "qualified": qualified})
            self.assertEqual(len(actual), len(expected))
            self.assertEqual([actual[i] for i in required], [expected[i] for i in required],
                "BLOCKED: native JSON bytes are not Python-compatible; do not activate writers")


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set checkpoint CETTA_BIN")
class OutOfDomainArithmeticDiagnostics(unittest.TestCase):
    def test_preserved_generic_overflow_is_outside_bounded_kernel_domain(self):
        values = [1e308, 1e308, -1e308]
        expected = bits(sum(values, 0))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result = engine(directory, HEADER +
                '!(py-call (goal_bookkeeping_probe.observe "overflow" (gka:sum-values %s 0)))' % atom(values))
            receipt("arithmetic-overflow-gap", {"values": values, "expected_bits": expected,
                "actual_exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            self.assertEqual(expected, "7ff0000000000000")
            if result.returncode == 0:
                self.assertEqual(json.loads((directory / "observations.jsonl").read_text())["bits"], expected)
            else:
                self.assertEqual(result.returncode, 2)
                self.assertIn("float_overflow", result.stderr)


if __name__ == "__main__":
    unittest.main()
