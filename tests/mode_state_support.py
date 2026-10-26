"""Project06 disposable qualification; immutable bundle pins are optional env inputs.

MODE_STATE_ENGINE_SHA256 and MODE_STATE_MANIFEST_SHA256 are translated only
at the reused project05 guard boundary. No release, path or hash is selected here.
"""
from contextlib import contextmanager
import os
import subprocess

from effect_receipts_bundle import ENGINE_PIN, MANIFEST_PIN, assert_same_bundle
from effect_receipts_support import ROOT, Qualification as ReceiptQualification, limit


class Qualification(ReceiptQualification):
    def __init__(self, evidence, directory):
        super().__init__(evidence, directory)
        self.pins = {
            ENGINE_PIN: os.environ.get("MODE_STATE_ENGINE_SHA256", ""),
            MANIFEST_PIN: os.environ.get("MODE_STATE_MANIFEST_SHA256", ""),
        }
        self.unset_environment = ()

    def environment(self):
        env = super().environment()
        env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
        for name, filename in {
            "LOOP_MODE_PATH": "loop-mode.json", "FUEL_MODE_PATH": "fuel-mode.json",
            "ENGINE_FAILURE_PATH": "engine-failure", "ENERGY_PATH": "energy.json",
            "WORKLOG_PATH": "worklog.jsonl", "OBSERVER_PATH": "observer.json",
            "SETTINGS_PATH": "settings.json", "MODEL_STATE_PATH": "model",
            "COGNITIVE_HEALTH_PATH": "cognitive-health.json",
            "DEPLOYMENT_STATE_PATH": "deployment.json",
            "MEMORY_BACKEND_CONFIG": "memory-backend.json", "LLM_POLICY_PATH": "llm-policy.json",
            "TELEGRAM_OFFSET_PATH": "offset", "TELEGRAM_CONTROL_PATH": "telegram-control.json",
        }.items():
            env["METTACLAW_" + name] = str(self.directory / filename)
        env.update(GOAL_BOOKKEEPING_KERNEL_PATH=str(self.directory / "goals_kernel.json"),
                   GOAL_BOOKKEEPING_COMPOST_PATH=str(self.directory / "goals_compost.log"),
                   GOAL_BOOKKEEPING_OBSERVATIONS=str(self.directory / "observations.jsonl"))
        for key in self.unset_environment:
            env.pop(key, None)
        return env

    def run(self, name, program, settings=None, check=True):
        self.index += 1
        stem = "%03d-%s" % (self.index, name)
        for part in ("src", "tests", "verification"):
            program = program.replace("./" + part + "/", str(ROOT / part) + "/")
        source = self.directory / (stem + ".metta")
        source.write_text(program, encoding="utf-8")
        (self.evidence / source.name).write_bytes(source.read_bytes())
        command = [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk", str(source)]
        env = self.environment()
        env.update(settings or {})
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                timeout=90, preexec_fn=limit)
        self.record_process(stem, command, result, env, cwd=ROOT)
        if check:
            assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
        return result.stdout.decode("utf-8", errors="backslashreplace")

    @contextmanager
    def phase(self, name):
        start = self.identity(name, "START")
        self._phase_start = start
        try:
            self.resolution(name, "START")
            yield
        finally:
            try:
                before = self.identity(name, "PRE-END-RESOLUTION")
                assert_same_bundle(start, before)
                self.resolution(name, "PRE-END-RESOLUTION")
                # Do not launch even the final descriptor process if a bundle
                # replacement happened during the preceding resolution.
                before = self.identity(name, "PRE-FINAL-DESCRIPTOR")
                assert_same_bundle(start, before)
                self.resolution(name, "END")
            finally:
                end = self.identity(name, "END")
                self._phase_start = None
                assert_same_bundle(start, end)
