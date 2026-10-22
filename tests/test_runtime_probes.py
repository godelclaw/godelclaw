"""Require semantic witnesses from the real PeTTa runtime probes."""

import os
from isolated_env import isolated_environ  # noqa: E402
import json
import pathlib
import re
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ANSI_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class RuntimeProbeTest(unittest.TestCase):
    PROBES = (
        ("tests/instruction_scope_probe.metta", "INSTRUCTION_SCOPE_OK"),
        ("tests/weak_process_core_probe.metta", "WEAK_PROCESS_CORE_OK"),
        ("tests/weak_process_episode_probe.metta", "WEAK_PROCESS_EPISODE_OK"),
        ("tests/shared_policy_turn_probe.metta", "SHARED_POLICY_TURN_OK"),
        ("tests/loop_policy_probe.metta", "LOOP_POLICY_OK"),
        ("tests/protocol_recovery_probe.metta", "PROTOCOL_RECOVERY_OK"),
        ("tests/fuel_policy_probe.metta", "FUEL_POLICY_OK"),
        ("tests/rest_banking_probe.metta", "REST_BANKING_OK"),
        ("tests/rest_continuation_probe.metta", "REST_CONTINUATION_OK"),
        ("tests/context_sources_probe.metta", "CONTEXT_SOURCES_OK"),
        ("tests/native_iter_process_probe.metta", "NATIVE_ITER_PROCESS_OK"),
        ("tests/metta_coding_policy_probe.metta", "METTA_CODING_POLICY_OK"),
        ("tests/structured_request_probe.metta", "STRUCTURED_REQUEST_OK"),
        ("tests/integrated_coding_turn_probe.metta",
         "INTEGRATED_CODING_TURN_OK"),
        ("tests/native_iter_multiturn_probe.metta",
         "NATIVE_ITER_MULTITURN_OK"),
        ("tests/interactive_batch_policy_probe.metta",
         "INTERACTIVE_BATCH_POLICY_OK"),
        ("tests/interrupted_cognitive_turn_probe.metta",
         "INTERRUPTED_COGNITIVE_TURN_OK"),
        ("tests/activity_delivery_probe.metta",
         "ACTIVITY_DELIVERY_OK"),
        ("tests/activity_delivery_full_turn_probe.metta",
         "ACTIVITY_DELIVERY_FULL_TURN_OK"),
        ("tests/prompt_cache_layout_probe.metta",
         "PROMPT_CACHE_LAYOUT_OK"),
        ("tests/background_jobs_probe.metta", "BACKGROUND_JOBS_OK"),
        ("tests/command_error_labels_probe.metta",
         "COMMAND_ERROR_LABELS_OK"),
        ("tests/burst_end_and_prose_probe.metta",
         "BURST_END_AND_PROSE_OK"),
    )

    def assert_witness(self, result, marker, engine):
        diagnostic = (result.stdout + "\n" + result.stderr)[-5000:]
        self.assertEqual(result.returncode, 0, diagnostic)
        witnessed = ANSI_CONTROL.sub("", result.stdout)
        self.assertRegex(
            witnessed,
            re.compile(r"^%s$" % re.escape(marker), re.MULTILINE),
            "%s exited successfully without reaching the semantic witness"
            % engine,
        )

    def test_each_probe_reaches_its_success_witness(self):
        petta_root = pathlib.Path(os.environ.get(
            "PETTA_ROOT", pathlib.Path.home() / "repos" / "PeTTa"))
        if not (petta_root / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        self.run_probes("petta", {}, "PeTTa")

    def test_each_probe_reaches_its_success_witness_on_cetta(self):
        """The same probes on CeTTa, the engine Lila runs on."""
        cetta = os.environ.get("CETTA_BIN", "")
        if not cetta or not pathlib.Path(cetta).is_file():
            self.skipTest("set CETTA_BIN to a cetta with Python")
        cetta = pathlib.Path(cetta).resolve()
        self.run_probes("cetta", {"CETTA_BIN": os.fspath(cetta),
                                  "CETTA_ROOT": os.fspath(cetta.parent)}, "CeTTa")

    def run_probes(self, engine, engine_env, label):
        with tempfile.TemporaryDirectory() as directory:
            test_bin = pathlib.Path(directory) / "bin"
            test_bin.mkdir()
            systemctl = test_bin / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --user ] && [ \"$2\" = is-active ]; then\n"
                "  echo active\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)
            lifecycle = pathlib.Path(directory) / "lifecycle.json"
            lifecycle.write_text(
                json.dumps({"schema": 1, "state": "running"}) + "\n",
                encoding="utf-8",
            )
            deployment = pathlib.Path(directory) / "deployment.json"
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            deployment.write_text(json.dumps({
                "candidate": head,
                "last_observation": {
                    "observed_at": time.time(),
                    "head": head,
                    "active": True,
                    "problems": [],
                },
            }) + "\n", encoding="utf-8")
            for probe, marker in self.PROBES:
                with self.subTest(probe=probe):
                    probe_state = pathlib.Path(directory) / pathlib.Path(probe).stem
                    probe_state.mkdir(exist_ok=True)
                    probe_tmp = probe_state / "tmp"
                    probe_tmp.mkdir(exist_ok=True)
                    iter_directory = (
                        pathlib.Path(directory) / "native-iter-multiturn"
                        if probe == "tests/native_iter_multiturn_probe.metta"
                        else ROOT / "tests" / "fixtures" /
                        "native_iter_transformations"
                    )
                    prompt_path = pathlib.Path(directory) / "prompt.txt"
                    prompt_path.write_text(
                        "GODEL_NATIVE_ITER_PERSONA\n", encoding="utf-8")
                    pins_path = pathlib.Path(directory) / "pins.txt"
                    pins_path.write_text(
                        "GODEL_NATIVE_ITER_PIN\n", encoding="utf-8")
                    env = isolated_environ()
                    env.update({
                        "METTACLAW_SKIP_INITIALIZE": "1",
                        "METTACLAW_SKIP_CONFIG": "1",
                        "METTACLAW_ENGINE": engine,
                        "PETTA_ROOT": os.fspath(pathlib.Path.home() /
                                                "repos" / "PeTTa"),
                        "PETTA_PY_ENV": os.fspath(pathlib.Path.home() /
                                                  "miniforge3" / "envs" /
                                                  "petta"),
                        "METTACLAW_ENGINE_STATE_PATH": os.path.join(
                            probe_state, "engine"),
                        "METTACLAW_MODEL_STATE_PATH": os.path.join(
                            probe_state, "model.metta"),
                        "METTACLAW_LLM_LEDGER_PATH": os.path.join(
                            probe_state, "llm-budget.sqlite3"),
                        "METTACLAW_LLM_COMPLETION_PATH": os.path.join(
                            probe_state, "completion.json"),
                        "METTACLAW_PROVIDER_OBSERVATION_PATH": os.path.join(
                            probe_state, "providers.json"),
                        "METTACLAW_COGNITIVE_HEALTH_PATH": os.path.join(
                            probe_state, "health.json"),
                        "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(
                            probe_state, "recycle.requested"),
                        "METTACLAW_WAKE_REQUEST_PATH": os.path.join(
                            probe_state, "wake.requested"),
                        "METTACLAW_CHATGPT_POLICY_PATH": os.path.join(
                            probe_state, "chatgpt.json"),
                        "METTACLAW_LOOP_MODE_PATH": os.path.join(
                            probe_state, "loop-mode.json"),
                        "METTACLAW_CHROMA_DIR": os.path.join(
                            probe_state, "chroma"),
                        "METTACLAW_ITER_PROCESS_DIR": os.fspath(
                            iter_directory),
                        "METTACLAW_ITER_PROCESS_TIMEOUT_SECONDS": "0.2",
                        "METTACLAW_PROMPT_PATH": os.fspath(prompt_path),
                        "METTACLAW_HISTORY_PATH": os.path.join(
                            probe_state, "history.metta"),
                        "METTACLAW_WORKING_SET_PATH": os.path.join(
                            probe_state, "working-set.json"),
                        "METTACLAW_PINS_PATH": os.fspath(pins_path),
                        "METTACLAW_LIFECYCLE_PATH": os.fspath(lifecycle),
                        "METTACLAW_DEPLOYMENT_STATE_PATH": os.fspath(
                            deployment),
                        "TMPDIR": os.fspath(probe_tmp),
                        "PATH": os.pathsep.join((
                            os.fspath(test_bin), env.get("PATH", ""),
                        )),
                    })
                    env.update(engine_env)
                    result = subprocess.run(
                        ["./run.sh", probe], cwd=ROOT, env=env,
                        stdin=subprocess.DEVNULL, capture_output=True,
                        text=True, timeout=30,
                    )
                    self.assert_witness(result, marker, label)
                    if probe == "tests/interactive_batch_policy_probe.metta":
                        mode_path = probe_state / "loop-mode.json"
                        history_path = probe_state / "history.metta"
                        self.assertTrue(mode_path.is_file())
                        self.assertIn("coding", mode_path.read_text(
                            encoding="utf-8"))
                        self.assertTrue(history_path.is_file())
                        self.assertIn("probe-stimulus", history_path.read_text(
                            encoding="utf-8"))
                    if probe == "tests/interrupted_cognitive_turn_probe.metta":
                        pins_path = pathlib.Path(directory) / "pins.txt"
                        pins = (pins_path.read_text(encoding="utf-8")
                                if pins_path.is_file() else "")
                        # New input queues while every emitted call runs.
                        self.assertIn("pin-after-new-input-lands", pins)
                        self.assertIn(
                            "pin-after-withheld-send-must-not-land", pins)
                        self.assertNotIn("COMMAND_BATCH_INTERRUPTED",
                                      result.stdout)

    def test_context_projection_crosses_cetta_provider_boundary(self):
        cetta = pathlib.Path(os.environ.get(
            "CETTA_BIN",
            pathlib.Path.home() / "repos" / "CeTTa-runtime" / "cetta"))
        if not cetta.is_file():
            self.skipTest("CeTTa executable is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            tmp = state / "tmp"
            tmp.mkdir()
            env = isolated_environ()
            env.update({
                "CETTA_BIN": os.fspath(cetta),
                "METTACLAW_SKIP_INITIALIZE": "1",
                "METTACLAW_SKIP_CONFIG": "1",
                "METTACLAW_ENGINE": "cetta",
                "PETTA_ROOT": os.fspath(pathlib.Path.home() /
                                        "repos" / "PeTTa"),
                "PETTA_PY_ENV": os.fspath(pathlib.Path.home() /
                                          "miniforge3" / "envs" / "petta"),
                "METTACLAW_ENGINE_STATE_PATH": os.path.join(
                    directory, "engine"),
                "METTACLAW_MODEL_STATE_PATH": os.path.join(
                    directory, "model.metta"),
                "METTACLAW_LLM_LEDGER_PATH": os.path.join(
                    directory, "llm-budget.sqlite3"),
                "METTACLAW_LLM_COMPLETION_PATH": os.path.join(
                    directory, "completion.json"),
                "METTACLAW_PROVIDER_OBSERVATION_PATH": os.path.join(
                    directory, "providers.json"),
                "METTACLAW_COGNITIVE_HEALTH_PATH": os.path.join(
                    directory, "health.json"),
                "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(
                    directory, "recycle.requested"),
                "METTACLAW_WAKE_REQUEST_PATH": os.path.join(
                    directory, "wake.requested"),
                "METTACLAW_CHATGPT_POLICY_PATH": os.path.join(
                    directory, "chatgpt.json"),
                "METTACLAW_CHROMA_DIR": os.path.join(directory, "chroma"),
                "TMPDIR": os.fspath(tmp),
            })
            result = subprocess.run(
                ["./run.sh", "tests/context_sources_probe.metta"],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assert_witness(result, "CONTEXT_SOURCES_OK", "CeTTa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
