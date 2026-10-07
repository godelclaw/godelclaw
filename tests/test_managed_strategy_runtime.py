"""Drive real MeTTa turns with controlled provider and activity boundaries."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagedRuntimeTest(unittest.TestCase):
    def exercise(self, fixture, marker, expected_commands):
        engines = []
        if Path(os.environ.get("CETTA_BIN", "")).is_file():
            engines.append("cetta")
        if (Path(os.environ.get("PETTA_ROOT", "")) / "run.sh").is_file():
            engines.append("petta")
        if not engines:
            self.skipTest("configure a CeTTa or PeTTa executable")
        for engine in engines:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as temporary:
                env = dict(os.environ, METTACLAW_ENGINE=engine,
                           METTACLAW_OBSERVED_STRATEGY="1",
                           METTACLAW_LLM_TRANSPORT="python",
                           METTACLAW_SKIP_INITIALIZE="1",
                           METTACLAW_COMMAND_BATCH_LIMIT="1",
                           METTACLAW_STRATEGY_PATH=str(Path(temporary) / "strategy.sqlite3"),
                           METTACLAW_EFFECT_RECEIPT_PATH=str(Path(temporary) / "effects.jsonl"),
                           OPENAI_API_KEY="unused-test-value")
                result = subprocess.run(["./run.sh", fixture],
                                        cwd=ROOT, env=env, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=45)
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-4000:])
                self.assertIn("\n" + marker + "\n", result.stdout)
                receipts = [json.loads(line) for line in (Path(temporary) / "effects.jsonl").read_text().splitlines()]
                self.assertEqual([r["command"] for r in receipts],
                                 [str(command) for command in expected_commands])
                self.assertEqual(len({str(r["turn"]) for r in receipts}), len(receipts))

    def test_stale_reply_and_recovery_on_available_engines(self):
        self.exercise("tests/observed_strategy_probe.metta", "OBSERVED_STRATEGY_OK",
                      [["help", "shell"]])

    def test_host_fault_recovery_on_available_engines(self):
        self.exercise("tests/strategy_fault_probe.metta", "STRATEGY_FAULT_RECOVERY_OK",
                      [["shell", "printf FIRST_FAULT_WITNESS"],
                       ["shell", "printf SECOND_FAULT_WITNESS"]])


if __name__ == "__main__": unittest.main()
