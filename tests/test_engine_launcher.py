"""The shell launcher defaults and recovers independently of real engines."""

import os
import pathlib
import signal
import shutil
import subprocess
import tempfile
import time
import unittest


SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1]


class EngineLauncherTests(unittest.TestCase):
    def test_launcher_exports_checkout_instance_to_children(self):
        launcher = (SOURCE_ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn('export METTACLAW_INSTANCE="$INSTANCE"', launcher)

    def test_service_uses_stable_cetta_selector(self):
        service = (SOURCE_ROOT / "systemd" / "pettaclaw-godel.service").read_text(
            encoding="utf-8")
        self.assertIn("Environment=CETTA_BIN=%h/repos/CeTTa-runtime/cetta",
                      service)
        self.assertNotIn("cetta-godel-command-boundary", service)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.root = self.base / "agent"
        self.petta = self.base / "petta"
        self.pyenv = self.base / "python-env"
        self.root.mkdir()
        self.petta.mkdir()
        (self.pyenv / "bin").mkdir(parents=True)
        (self.root / "src").mkdir()
        shutil.copy2(SOURCE_ROOT / "run.sh", self.root / "run.sh")
        (self.root / "run.metta").write_text("", encoding="utf-8")
        (self.root / "src" / "memory_health.py").write_text(
            "", encoding="utf-8")
        self._script(self.pyenv / "bin" / "python3",
                     'if [ "$1" = -c ]; then test -f "$METTACLAW_RECYCLE_REQUEST_PATH"; else exit 0; fi')
        self.result = self.base / "result"
        self._script(
            self.petta / "run.sh",
            'printf "petta:%s:%s:%s\\n" "$METTACLAW_ACTIVE_ENGINE" "$*" '
            '"$PYTHONPATH" '
            '>>"$ENGINE_TEST_RESULT"')
        self.cetta = self.base / "cetta"
        self._script(
            self.cetta,
            'printf "cetta:%s:%s\\n" "$METTACLAW_ACTIVE_ENGINE" "$*" '
            '>>"$ENGINE_TEST_RESULT"; '
            'if [ "${CETTA_WAIT_FOR_TERM:-0}" = 1 ]; then '
            ': >"$CETTA_READY"; trap "exit 0" TERM INT HUP; '
            'while :; do sleep 1; done; fi; '
            'if [ "${CETTA_REQUEST_RECYCLE:-0}" = 1 ]; then '
            'mkdir -p "$(dirname "$METTACLAW_RECYCLE_REQUEST_PATH")"; '
            ': >"$METTACLAW_RECYCLE_REQUEST_PATH"; fi; '
            'exit "${CETTA_EXIT_CODE:-0}"')
        self.env = os.environ.copy()
        for name in (
                "METTACLAW_ENGINE", "METTACLAW_ACTIVE_ENGINE",
                "METTACLAW_ENGINE_STATE_PATH",
                "METTACLAW_ENGINE_FAILURE_PATH",
                "METTACLAW_RECYCLE_REQUEST_PATH"):
            self.env.pop(name, None)
        self.env.update({
            "HOME": str(self.base / "home"),
            "XDG_STATE_HOME": str(self.base / "state"),
            "PETTA_ROOT": str(self.petta),
            "PETTA_PY_ENV": str(self.pyenv),
            "CETTA_BIN": str(self.cetta),
            "ENGINE_TEST_RESULT": str(self.result),
            "METTACLAW_SKIP_INITIALIZE": "1",
        })

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _script(path, body):
        path.write_text("#!/bin/sh\nset -eu\n" + body + "\n",
                        encoding="utf-8")
        path.chmod(0o755)

    @property
    def state_path(self):
        return self.base / "state" / "agent" / "engine"

    def select(self, name):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(name + "\n", encoding="utf-8")

    @property
    def failure_path(self):
        return self.base / "state" / "agent" / "engine-failure"

    def launch(self, **extra_env):
        env = dict(self.env)
        env.update({key: str(value) for key, value in extra_env.items()})
        return subprocess.run(
            [str(self.root / "run.sh")], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False)

    def result_lines(self):
        return self.result.read_text(encoding="utf-8").splitlines()

    def test_absent_selection_uses_stable_petta_default(self):
        result = self.launch(CETTA_REQUEST_RECYCLE=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.result_lines()), 1)
        self.assertTrue(self.result_lines()[0].startswith("petta:petta:"))
        self.assertIn(str(self.root / "repos" / "petta_lib_chromadb"),
                      self.result_lines()[0])

    def test_cetta_selection_launches_petta_profile(self):
        self.select("cetta")
        result = self.launch(CETTA_REQUEST_RECYCLE=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = self.result_lines()[0]
        self.assertTrue(line.startswith("cetta:cetta:"))
        self.assertIn("--lang petta", line)
        # The bootstrap imports the program; no module is also listed here,
        # which would load its equations twice.
        bootstrap = str(self.root / "cetta_bootstrap.metta")
        start = str(self.root / "cetta_run.metta")
        self.assertLess(line.index(bootstrap), line.index(start))
        self.assertNotIn(str(self.root / "src" / ""), line)
        self.assertNotIn(str(self.root / "lib_nal.metta"), line)

    def test_probe_does_not_inherit_configuration_channel_authority(self):
        (self.root / ".env").write_text(
            "export METTACLAW_TELEGRAM_SERVICE_SOCKET=/configuration/live.sock\n"
            "export METTACLAW_TELEGRAM_CLIENT=metta\n"
            "export METTACLAW_TELEGRAM_BOT_TOKEN=configuration-token\n"
            "export TELEGRAM_BOT_TOKEN=configuration-token\n")
        (self.root / "utility.metta").write_text("")
        self._script(self.cetta,
            'printf "%s|%s|%s\\n" "$METTACLAW_TELEGRAM_SERVICE_SOCKET" '
            '"$METTACLAW_TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN"; '
            'printf "client=%s\\n" "$METTACLAW_TELEGRAM_CLIENT"')
        env = dict(self.env, METTACLAW_ENGINE="cetta")
        for key in ("METTACLAW_TELEGRAM_SERVICE_SOCKET", "METTACLAW_TELEGRAM_CLIENT", "METTACLAW_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
            env.pop(key, None)
        result = subprocess.run([str(self.root / "run.sh"), "utility.metta"], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\n||\n", "\n" + result.stdout)
        self.assertIn("client=python", result.stdout)
        env.update(METTACLAW_TELEGRAM_SERVICE_SOCKET="/fixture/channel.sock",
                   METTACLAW_TELEGRAM_BOT_TOKEN="fixture-token", METTACLAW_TELEGRAM_CLIENT="metta")
        result = subprocess.run([str(self.root / "run.sh"), "utility.metta"], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/fixture/channel.sock|fixture-token|\n", result.stdout)
        self.assertIn("client=metta", result.stdout)

    def test_clean_unrequested_stop_preserves_engine(self):
        self.select("cetta")
        result = self.launch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([line.split(":", 1)[0]
                          for line in self.result_lines()],
                         ["cetta"])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"),
                         "cetta\n")
        self.assertIn("stopped without a recycle request", result.stderr)
        failure = self.failure_path.read_text(encoding="utf-8")
        self.assertIn("engine=cetta\n", failure)
        self.assertIn("status=1\n", failure)
        self.assertIn("reason=CeTTa stopped without a recycle request\n",
                      failure)
        self.assertEqual(self.failure_path.stat().st_mode & 0o777, 0o600)

    def test_cetta_failure_preserves_selection_by_default(self):
        self.select("cetta")
        result = self.launch(CETTA_EXIT_CODE=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(self.result_lines()), 1)
        self.assertEqual(self.state_path.read_text(), "cetta\n")
        self.assertIn("status=7\n", self.failure_path.read_text())

    def test_explicit_fallback_heals_selection(self):
        self.select("cetta")
        result = self.launch(CETTA_EXIT_CODE=7, METTACLAW_ENGINE_FALLBACK="petta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split(":", 1)[0]
                          for line in self.result_lines()],
                         ["cetta", "petta"])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"),
                         "petta\n")
        self.assertIn("restoring stable engine 'petta'", result.stderr)
        failure = self.failure_path.read_text(encoding="utf-8")
        self.assertIn("status=7\n", failure)
        self.assertIn("reason=CeTTa process exited\n", failure)

    def test_intentional_service_stop_preserves_cetta_selection(self):
        self.select("cetta")
        ready = self.base / "cetta-ready"
        env = dict(self.env)
        env.update({
            "CETTA_WAIT_FOR_TERM": "1",
            "CETTA_READY": str(ready),
        })
        process = subprocess.Popen(
            [str(self.root / "run.sh")], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "mock CeTTa did not start")
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual([line.split(":", 1)[0]
                          for line in self.result_lines()], ["cetta"])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"),
                         "cetta\n")
        self.assertFalse(self.failure_path.exists())
        self.assertNotIn("restoring stable engine", stderr)

    def test_unwritable_failure_receipt_does_not_block_fallback(self):
        self.select("cetta")
        failure_parent = self.base / "unwritable"
        failure_parent.mkdir()
        failure_parent.chmod(0o500)
        try:
            result = self.launch(
                CETTA_EXIT_CODE=7,
                METTACLAW_ENGINE_FALLBACK="petta",
                METTACLAW_ENGINE_FAILURE_PATH=(
                    failure_parent / "engine-failure"),
            )
        finally:
            failure_parent.chmod(0o700)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split(":", 1)[0]
                          for line in self.result_lines()],
                         ["cetta", "petta"])
        self.assertIn("could not persist engine failure receipt",
                      result.stderr)

    def test_invalid_selection_uses_petta(self):
        self.select("not-an-engine")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.result_lines()[0].startswith("petta:petta:"))
        self.assertIn("invalid persisted engine", result.stderr)

    def test_legacy_pleatta_selection_is_not_launched(self):
        self.select("pleatta")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.result_lines()[0].startswith("petta:petta:"))
        self.assertEqual(self.state_path.read_text(encoding="utf-8"),
                         "petta\n")
        self.assertIn("PLeaTTa is temporarily disabled", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
