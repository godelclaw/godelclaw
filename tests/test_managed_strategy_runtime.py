"""Drive real MeTTa turns with controlled provider and activity boundaries."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagedRuntimeTest(unittest.TestCase):
    def exercise(self, fixture, marker, expected_commands, expected_deferred=None, *, environment=None):
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
                metadata=Path(temporary)/"provider-observations.json"
                metadata.write_text(json.dumps({"schema":1,"models":{"value":[{"id":"syn:large:text","context":524288}]}}))
                env["METTACLAW_PROVIDER_OBSERVATION_PATH"]=str(metadata)
                env.update(environment(engine) if callable(environment) else environment or {})
                result = subprocess.run(["./run.sh", fixture],
                                        cwd=ROOT, env=env, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=45)
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-4000:])
                self.assertIn("\n" + marker + "\n", result.stdout)
                receipts = [json.loads(line) for line in (Path(temporary) / "effects.jsonl").read_text().splitlines()]
                executed = [r for r in receipts if "command" in r]
                deferred = [r for r in receipts if "commands" in r]
                if expected_commands is None:
                    self.assertEqual(len(executed),18)
                    continue
                self.assertEqual([r["command"] for r in executed],
                                 [str(command) for command in expected_commands])
                self.assertEqual([r["commands"] for r in deferred],
                                 [str(commands) for commands in expected_deferred or []])
                self.assertTrue(all(r["disposition"] == "deferred" for r in deferred))

    def test_task_history_and_failure_boundary_on_available_engines(self):
        self.exercise("tests/task_context_probe.metta", "TASK_CONTEXT_OK", None)

    def test_batch_envelope_and_precise_feedback_on_available_engines(self):
        self.exercise("tests/command_feedback_probe.metta", "COMMAND_FEEDBACK_OK",
                      [["pin", "verified note"], ["help", "shell"], ["not-a-tool"], ["help", "pin"]],
                      [])

    def test_stale_reply_and_recovery_on_available_engines(self):
        self.exercise("tests/observed_strategy_probe.metta", "OBSERVED_STRATEGY_OK",
                      [["shell", "printf EMITTED_COMMAND"], ["help", "shell"]])

    def test_host_fault_recovery_on_available_engines(self):
        self.exercise("tests/strategy_fault_probe.metta", "STRATEGY_FAULT_RECOVERY_OK",
                      [["shell", "printf FIRST_FAULT_WITNESS"],
                       ["shell", "printf SECOND_FAULT_WITNESS"]])

    def test_runtime_observation_and_omission_on_available_engines(self):
        self.exercise("tests/runtime_context_probe.metta", "RUNTIME_CONTEXT_OK",
                      [["help", "vitals"], ["help", "vitals"]])

    def test_peer_input_resumes_an_idle_loop_without_operator_authority(self):
        self.exercise("tests/peer_activity_probe.metta", "PEER_ACTIVITY_OK",
                      [["help", "send"]],
                      environment=lambda engine: {"METTACLAW_INSTANCE":"lila",
                          "METTACLAW_TELEGRAM_CLIENT":"metta" if engine=="cetta" else "python"})


if __name__ == "__main__": unittest.main()
