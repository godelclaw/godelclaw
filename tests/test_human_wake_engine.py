"""The native foreign bridge carries the once-per-wake result into MeTTa."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HumanWakeEngineTest(unittest.TestCase):
    def test_same_batch_is_applied_once(self):
        executable = Path(os.environ.get("CETTA_BIN", ""))
        if not executable.is_file():
            self.skipTest("configure CETTA_BIN")
        with tempfile.TemporaryDirectory() as temporary:
            goal = Path(temporary) / "goals.metta"
            goal.write_text(
                '(goal-budget 3)\n'
                '(goal task Work sti:0.8 lti:0.3 vibes:(care) '
                'blocked-by:none last-verified:never note:"fixture")\n',
                encoding="utf-8",
            )
            env = dict(os.environ, METTACLAW_GOALS_PATH=os.fspath(goal))
            result = subprocess.run(
                [os.fspath(executable), "--lang", "petta", "--import-mode",
                 "ancestor-walk", "tests/human_wake_probe.metta"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HUMAN_WAKE_IDENTITY_OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
