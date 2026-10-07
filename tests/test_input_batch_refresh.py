"""Preserve and refresh input across a failed turn in the native client."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InputBatchRefreshTest(unittest.TestCase):
    def test_retry_refreshes_and_ack_keeps_later_input(self):
        if not Path(os.environ.get("CETTA_BIN", "")).is_file():
            self.skipTest("configure the native client executable")
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, XDG_STATE_HOME=temporary,
                       METTACLAW_ENGINE="cetta",
                       METTACLAW_LLM_TRANSPORT="python",
                       METTACLAW_SKIP_INITIALIZE="1",
                       METTACLAW_TELEGRAM_SERVICE_SOCKET="")
            result = subprocess.run(
                ["./run.sh", "tests/input_batch_refresh_probe.metta"],
                cwd=ROOT, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=45)
            self.assertEqual(result.returncode, 0,
                             (result.stdout + result.stderr)[-3000:])
            self.assertIn("\nINPUT_BATCH_REFRESH_OK\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
