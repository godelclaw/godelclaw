"""One table for every constant that lives in more than one artifact.

The rest/heartbeat regression survived for months because two Lean models
described a loop that had changed underneath them. The guard that was
supposed to catch that class of drift — test_constants_regression.sh —
pointed at a different agent's files and grepped for a pattern that no
longer existed anywhere, so it found nothing, printed a warning, and
passed. A guard that can silently match nothing is decoration.

Hence the one rule here: a source that is PRESENT but whose pattern does
not match is a failure, not a skip. Only a genuinely absent file (the Lean
models are in a separate repo) is skipped, and a group needs two readable
sources before it can claim to have cross-checked anything.

To pin a new shared constant, add a row. That is the whole mechanism.
"""

import os
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fuel_modes  # noqa: E402


def _lean_dir():
    configured = os.environ.get("METTACLAW_LEAN_MODEL_DIR")
    if configured:
        return pathlib.Path(configured)
    return pathlib.Path.home() / "repos" / "MeTTapedia" / "lean" / "pettaclaw"


LEAN = _lean_dir()

# name -> [(path, pattern capturing one integer), ...]
CONSTANTS = {
    "one full burst": [
        (ROOT / "src" / "loop_policy.metta",
         r"\(=\s*\(policy-registry\)\s*"
         r"\(coordinate\s+base\s*"
         r"\(coordinate\s+burst-budget\s+(\d+)"),
        (LEAN / "PolicyPipelineRuntime.lean",
         r"def fullBurst : Nat := (\d+)"),
        (LEAN / "PresentMoment.lean", r"def full : Nat := (\d+)"),
        (LEAN / "ClawArchitectures.lean", r"def full : Nat := (\d+)"),
        (LEAN / "RestEnergy.lean", r"def full : Nat := (\d+)"),
        (LEAN / "FuelPolicy.lean", r"def full : Nat := (\d+)"),
    ],
    "iter idle boundary": [
        (ROOT / "src" / "loop_policy.metta",
         r"\(coordinate\s+iter-renewal\s*"
         r"\(coordinate\s+idle-wait\s+(\d+)"),
        (LEAN / "PolicyPipelineRuntime.lean",
         r"def iterIdleWait : Nat := (\d+)"),
    ],
    "feedback window": [
        (ROOT / "src" / "helper.py", r"_WORKING_CAP = (\d+)"),
        (ROOT / "src" / "memory.metta", r"configure maxFeedback (\d+)"),
        (LEAN / "PresentMoment.lean", r"def cap : Nat := (\d+)"),
    ],
    "carry ceiling": [
        (ROOT / "src" / "fuel_modes.py", r"^CARRY = (\d+)"),
        (LEAN / "FuelPolicy.lean", r"def carryMultiple : Nat := (\d+)"),
    ],
}


class ConstantCorrespondenceTest(unittest.TestCase):
    def read(self, path, pattern):
        """None when the artifact is absent; a hard failure when it is
        present and the pattern has gone blind."""
        if not path.is_file():
            return None
        match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(
            match, "%s no longer matches %r — the guard has gone blind, "
                   "which is exactly how the last one failed" % (path, pattern))
        return int(match.group(1))

    def test_shared_constants_agree(self):
        for name, sources in CONSTANTS.items():
            with self.subTest(constant=name):
                found = {}
                for path, pattern in sources:
                    value = self.read(path, pattern)
                    if value is not None:
                        found[path.name] = value
                if len(found) < 2:
                    self.skipTest("%s: only %d readable source" % (name, len(found)))
                self.assertEqual(len(set(found.values())), 1,
                                 "%s disagrees across artifacts: %s" % (name, found))

    def test_runtime_carry_matches_its_own_constant(self):
        self.assertEqual(
            self.read(ROOT / "src" / "fuel_modes.py", r"^CARRY = (\d+)"),
            fuel_modes.CARRY)

    def test_decay_rate_matches_the_model(self):
        """The rate is what makes decay and carry share a ceiling, so a
        change on one side alone silently breaks the bound."""
        model = LEAN / "FuelPolicy.lean"
        if not model.is_file():
            self.skipTest("FuelPolicy.lean is not checked out")
        lean = re.search(r"\|\s*\.decay,\s*held, grant =>\s*"
                         r"held \* (\d+) / (\d+) \+ grant",
                         model.read_text(encoding="utf-8"))
        self.assertIsNotNone(lean, "decay rule not found in the model")
        code = re.search(r"return held \* (\d+) // (\d+) \+ offered",
                         (ROOT / "src" / "fuel_modes.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(code, "decay rule not found in the runtime")
        self.assertEqual(lean.groups(), code.groups())

    def test_every_runtime_discipline_is_modelled(self):
        model = LEAN / "FuelPolicy.lean"
        if not model.is_file():
            self.skipTest("FuelPolicy.lean is not checked out")
        cases = set(re.findall(r"^\s*\|\s*(saturate|accumulate|carry|decay|unlimited)$",
                               model.read_text(encoding="utf-8"), re.M))
        self.assertEqual(cases, set(fuel_modes.FUELS))

    def test_the_models_carry_no_sorry(self):
        for model in sorted(LEAN.glob("*.lean")) if LEAN.is_dir() else []:
            with self.subTest(model=model.name):
                self.assertNotIn("sorry", model.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
