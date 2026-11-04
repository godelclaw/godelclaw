"""Ordinary messages in a counted group let a parked agent keep waiting."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class GroupWakeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN")
    def test_counted_group_waits_and_wake_senders_do_not(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            environment = dict(os.environ,
                METTACLAW_OBSERVED_STRATEGY="0",
                METTACLAW_STRATEGY_PATH=str(state / "state.sqlite3"),
                METTACLAW_WORKING_SET_PATH=str(state / "working.json"),
                METTACLAW_TELEGRAM_SERVICE_SOCKET="",
                METTACLAW_ENGINE_STATE_PATH=str(state / "engine"),
                METTACLAW_TELEGRAM_LOG_PATH=str(state / "updates.jsonl"),
                METTACLAW_TELEGRAM_GROUP_WAKE_EVERY="-100:3, -200:x",
                METTACLAW_TELEGRAM_BOT_USERNAME="GodelTestBot",
                PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "channels")]))
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta",
                "--import-mode", "ancestor-walk", str(root / "tests/group_wake_probe.metta")],
                cwd=root, env=environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2500:])
        self.assertIn("GROUP_WAKE_OK\n", result.stdout)
        self.assertNotIn("(Error ", result.stdout)


if __name__ == "__main__":
    unittest.main()
