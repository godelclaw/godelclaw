"""Check configurable admission and the real action/feedback boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from src import helper


ROOT = Path(__file__).resolve().parents[1]


class SingleActionPolicyTest(unittest.TestCase):
    def test_positive_limit_and_default(self):
        for value, expected in (("", 5), ("1", 1), ("8", 8),
                                ("0", 5), ("-1", 5), ("bad", 5)):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"METTACLAW_COMMAND_BATCH_LIMIT": value}
            ):
                self.assertEqual(helper.command_batch_limit(5), expected)

    def test_local_configuration_selects_one_action(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text("[agent]\ncommand_batch_limit = 1\n")
            result = subprocess.run(
                [sys.executable, "scripts/config_to_env.py", str(config),
                 str(ROOT)], cwd=ROOT, capture_output=True, text=True,
                check=True, timeout=10,
            )
            self.assertIn("export METTACLAW_COMMAND_BATCH_LIMIT='1'",
                          result.stdout)

    def test_composite_action_and_feedback_on_both_engines(self):
        engines = []
        petta = Path(os.environ.get(
            "PETTA_ROOT", Path.home() / "repos" / "PeTTa"))
        if (petta / "run.sh").is_file():
            engines.append("petta")
        cetta = os.environ.get("CETTA_BIN", "")
        if cetta and Path(cetta).is_file():
            engines.append("cetta")
        if not engines:
            self.skipTest("set PETTA_ROOT or CETTA_BIN to an available engine")
        for engine in engines:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as d:
                environment = dict(os.environ)
                environment.update({
                    "METTACLAW_ENGINE": engine,
                    "METTACLAW_COMMAND_BATCH_LIMIT": "1",
                    "METTACLAW_LLM_TRANSPORT": "python",
                    "METTACLAW_SKIP_INITIALIZE": "1",
                    "METTACLAW_ENGINE_STATE_PATH": str(Path(d) / "engine"),
                    "METTACLAW_EFFECT_RECEIPT_PATH": str(Path(d) / "receipts"),
                    "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw",
                    "OPENAI_API_KEY": "test-only-unused",
                })
                result = subprocess.run(
                    ["./run.sh", "tests/single_action_policy_probe.metta"],
                    cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=45,
                )
                self.assertEqual(result.returncode, 0,
                                 (result.stdout + result.stderr)[-4000:])
                self.assertIn("\nSINGLE_ACTION_POLICY_OK\n", result.stdout)
                receipts = [json.loads(line) for line in
                            (Path(d) / "receipts").read_text().splitlines()]
                returned = [r for r in receipts if r["disposition"] == "returned"]
                self.assertEqual(len(returned), 2, receipts)
                self.assertIn("FIRST_SUBSTEPSECOND_SUBSTEP", returned[0]["result"])
                self.assertFalse(any("SUFFIX_EXECUTED" in r["command"]
                                     for r in returned))
                deferred = [r for r in receipts if r["disposition"] == "deferred"]
                self.assertEqual(len(deferred), 1, receipts)
                self.assertEqual(deferred[0]["reason"], "batch_limit")


if __name__ == "__main__":
    unittest.main()
