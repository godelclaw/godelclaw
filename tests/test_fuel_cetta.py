"""The native fuel discipline answers exactly as src/fuel_modes.py does.

Every operation is evaluated by CeTTa over the same state file the Python
module reads, for every discipline, for malformed and missing state, and for
numbers, integer text and junk inputs."""
import importlib
from isolated_env import isolated_environ  # noqa: E402
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOOPS = [0, 1, 7, 30, 49, 50, 51, 150, 199, 200, 201, 1000, 123457, -5, 2.5, 7.9,
         "30", " 7 ", "+12", "-3", "x", "", "7.0"]
GRANTS = [0, 1, 50, "50", -3, "y", 2.9]
STATES = {
    "missing": None, "saturate": '{"fuel": "saturate"}', "accumulate": '{"fuel": "accumulate"}',
    "carry": '{"fuel": "carry"}', "decay": '{"fuel": "decay"}', "unlimited": '{"fuel": "unlimited"}',
    "malformed": "{bad", "array": "[1, 2]", "unknown": '{"fuel": "turbo"}',
    "spaced": '{"fuel": "  Carry "}', "number": '{"fuel": 3}', "extra": '{"x": 1, "fuel": "accumulate"}',
}


def literal(value):
    return json.dumps(value) if isinstance(value, str) else repr(value)


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to a CeTTa build")
class NativeFuelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "memory" / "fuel_mode.json"
        os.environ["METTACLAW_FUEL_MODE_PATH"] = str(self.state)
        import fuel_modes
        self.python = importlib.reload(fuel_modes)

    def tearDown(self):
        os.environ.pop("METTACLAW_FUEL_MODE_PATH", None)
        self.tmp.cleanup()

    def native(self, expressions):
        """Evaluate fuel expressions in one CeTTa process; return their values."""
        out = Path(self.tmp.name) / "results.json"
        out.unlink(missing_ok=True)
        lines = ["!(import! &self (library lib_import))", "!(import! &self (library fs))",
                 "!(import! &self (library json))", "!(import! &self ./src/fuel_cetta.metta)",
                 "(= (fuel-test:encode $value) (if (== (get-type $value) Number) "
                 "(JsonNumber (format-args \"{}\" ($value))) (JsonString $value)))"]
        names = ["$r%d" % i for i in range(len(expressions))]
        bindings = " ".join("(%s (fuel-test:encode %s))" % (n, e) for n, e in zip(names, expressions))
        lines.append("!(let* (%s ($text (json-stringify (JsonArray (%s))))) (fs:update-text %s \"replace\" $text \"\" \"\"))"
                     % (bindings, " ".join(names), json.dumps(str(out))))
        program = ROOT / (".fuel_probe_%d.metta" % os.getpid())
        program.write_text("\n".join(lines) + "\n")
        try:
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk",
                                     str(program)], cwd=ROOT, env=isolated_environ(), capture_output=True,
                                    text=True, timeout=300)
        finally:
            program.unlink()
        detail = result.stdout[-2000:] + result.stderr[-2000:]
        self.assertEqual(result.returncode, 0, detail)
        self.assertTrue(out.is_file(), "probe did not produce fresh results: " + detail)
        values = json.loads(out.read_text())
        self.assertIsInstance(values, list)
        self.assertEqual(len(values), len(expressions), "wrong number of probe results")
        return values

    def write_state(self, text):
        if text is None:
            if self.state.exists():
                self.state.unlink()
            return
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(text)

    def test_every_operation_matches_python_in_every_state(self):
        for label, text in STATES.items():
            with self.subTest(state=label):
                self.write_state(text)
                cases = [("(fuel:current)", self.python.current_fuel()),
                         ("(fuel:view)", self.python.fuel_view()),
                         ("(fuel:list-view)", self.python.fuels_view())]
                for loops in LOOPS:
                    cases.append(("(fuel:spend %s)" % literal(loops), self.python.spend(loops)))
                    cases.append(("(fuel:context-view %s)" % literal(loops), self.python.context_view(loops)))
                    for grant in GRANTS:
                        cases.append(("(fuel:refuel %s %s)" % (literal(loops), literal(grant)),
                                      self.python.refuel(loops, grant)))
                for grant in GRANTS:
                    cases.append(("(fuel:ceiling %s)" % literal(grant), self.python.ceiling(grant)))
                got = self.native([expression for expression, _ in cases])
                mismatches = [(e, want, have) for (e, want), have in zip(cases, got) if want != have]
                self.assertEqual(mismatches, [], label)
                self.assertEqual(text if text is None else self.state.read_text(), text, "reading changed state")

    def test_setting_writes_what_python_writes(self):
        python_state = Path(self.tmp.name) / "python" / "fuel_mode.json"
        for name in ["carry", " Unlimited ", "turbo", "", "DECAY", "saturate"]:
            with self.subTest(name=name):
                native_reply = self.native(["(fuel:set %s)" % json.dumps(name)])[0]
                native_bytes = self.state.read_bytes() if self.state.exists() else None
                os.environ["METTACLAW_FUEL_MODE_PATH"] = str(python_state)
                try:
                    python_reply = self.python.set_fuel(name)
                finally:
                    os.environ["METTACLAW_FUEL_MODE_PATH"] = str(self.state)
                python_bytes = python_state.read_bytes() if python_state.exists() else None
                self.assertEqual(native_reply, python_reply)
                self.assertEqual(native_bytes, python_bytes)

    def test_an_unwritable_state_fails_without_raising(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("a file, not a directory")
        self.state = blocker / "fuel_mode.json"
        os.environ["METTACLAW_FUEL_MODE_PATH"] = str(self.state)
        self.assertEqual(self.native(['(fuel:set "carry")'])[0], self.python.set_fuel("carry"))
        self.assertEqual(self.python.set_fuel("carry"), "fuel-set failed: could not persist")

    def test_a_silent_reduction_cannot_reuse_previous_results(self):
        self.native(["(fuel:current)"])
        with self.assertRaisesRegex(AssertionError, "did not produce fresh results"):
            self.native(["(superpose ())"])


if __name__ == "__main__":
    unittest.main()
