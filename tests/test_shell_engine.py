"""Process contracts run on both engines; CeTTa's backend is native MeTTa/C."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShellEngineTests(unittest.TestCase):
    def engine(self, engine, program, work, **overrides):
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        cetta = Path(os.environ.get("CETTA_BIN", ""))
        if not (petta / "run.sh").is_file() or (engine == "cetta" and not cetta.is_file()):
            self.skipTest("engine checkout/executable unavailable")
        python = Path(os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta"))
        env = dict(os.environ, METTACLAW_ACTIVE_ENGINE=engine,
                   METTACLAW_ENGINE_STATE_PATH=str(work / "engine.json"),
                   METTACLAW_PROGRESS_PATH=str(work / "progress.json"),
                   METTACLAW_SHELL_TIMEOUT_SECONDS="120", SHELL_TEST_VALUE="λ 🌸",
                   PETTA_PATH=str(petta), PYTHONHOME=str(python),
                   PYTHONPATH=str(ROOT / "src"),
                   PATH=str(python / "bin") + os.pathsep + os.environ.get("PATH", ""),
                   LD_LIBRARY_PATH=str(python / "lib") + os.pathsep + os.environ.get("LD_LIBRARY_PATH", ""))
        env.pop("NOTIFY_SOCKET", None)
        env.update(overrides)
        local = ROOT / (".shell-probe-" + work.name + ".metta")
        local.write_text("!(import! &self (library lib_import))\n"
                         "!(import! &self ./src/shell_io)\n" + program, encoding="utf-8")
        command = ([str(cetta), "--lang", "petta", "--import-mode", "ancestor-walk",
                    str(petta / "lib/lib_import.metta"), str(local)] if engine == "cetta"
                   else ["bash", str(petta / "run.sh"), str(local), "default"])
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    text=True, timeout=45)
        finally:
            local.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        self.assertNotIn("AssertionError", result.stderr)
        return result

    def contract(self, engine):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            checks = []
            def check(command, expected, seconds=None):
                call = f"(shell {json.dumps(command, ensure_ascii=False)}"
                if seconds is not None: call += " " + json.dumps(seconds)
                checks.append(f"!(assert (== {call}) {json.dumps(expected, ensure_ascii=False)}))")
            check("printf '%s' \"$SHELL_TEST_VALUE\"", "λ 🌸")
            check("printf 'one'; printf 'two' >&2; printf 'three'", "onetwothree")
            check("printf '%s' 'quoted \"$value\"'", 'quoted "$value"')
            check("pwd", str(ROOT) + "\n")
            check("true", "SHELL_OK_NO_OUTPUT (exit 0)")
            check("printf failure >&2; exit 7", "SHELL_ERROR exit 7\nfailure")
            check("printf partial; sleep 2; printf late", "SHELL_TIMEOUT after 0.1 seconds\npartial", 0.1)
            check("printf no-deadline", "no-deadline", 0)
            check("printf string-timeout", "string-timeout", "1")
            check("sleep 6; printf survived", "survived")
            # No hidden capture cap: compare a 2 MiB file against its output.
            large = work / "large"; large.write_text("x" * (2 * 1024 * 1024))
            checks.append(f'!(assert (== (shell "cat {large}") (read-file {json.dumps(str(large))} 0)))')
            # A background descendant must not hold the shell's result pipe open.
            command = f"sleep 5 </dev/null & printf '%s' $! > '{work / 'child'}'; printf background"
            check(command, "background")
            checks.append("!(println! SHELL_CONTRACT_OK)\n")
            program = "!(import! &self ./src/file_io)\n" + "\n".join(checks)
            try:
                result = self.engine(engine, program, work)
                self.assertIn("SHELL_CONTRACT_OK", result.stdout)
                self.assertNotIn("Error", result.stdout)
                self.assertNotIn("Assertion failed", result.stderr)
            finally:
                if (work / "child").exists():
                    try: os.kill(int((work / "child").read_text()), 9)
                    except ProcessLookupError: pass

    def test_native_cetta_contract(self): self.contract("cetta")
    def test_swi_petta_contract(self): self.contract("petta")

    def test_native_wait_observation_and_watchdog(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.addCleanup(notify.close)
            notify.bind(str(work / "notify.sock")); notify.settimeout(1)
            program = '!(assert (== (shell "sleep 31; printf observed" 0) "observed"))\n'
            result = self.engine("cetta", program, work, NOTIFY_SOCKET=str(work / "notify.sock"))
            self.assertNotIn("Error", result.stdout)
            self.assertEqual(notify.recv(128), b"WATCHDOG=1")
            self.assertEqual(notify.recv(128), b"WATCHDOG=1")
            progress = json.loads((work / "progress.json").read_text())
            self.assertEqual(progress["phase"], "process-wait")
            self.assertGreaterEqual(progress["sequence"], 2)
            self.assertTrue(progress["owner"].startswith(str(progress["pid"]) + ":"))
            self.assertGreater(progress["wait_until"], time.monotonic())

    def test_native_timeout_kills_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            command = f"(sleep 0.5; printf leaked > '{work / 'leaked'}') & printf partial; sleep 2"
            program = f'!(println! (shell {json.dumps(command)} 0.1))\n'
            result = self.engine("cetta", program, work)
            self.assertIn("SHELL_TIMEOUT after 0.1 seconds", result.stdout)
            self.assertIn("partial", result.stdout)
            time.sleep(0.6)
            self.assertFalse((work / "leaked").exists())

    def test_native_progress_path_derived_from_engine_state(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            result = self.engine("cetta", '!(println! (shell "printf derived"))\n', work,
                                 METTACLAW_PROGRESS_PATH="")
            self.assertIn("derived", result.stdout)
            self.assertNotIn("Error", result.stdout)
            self.assertTrue((work / "progress.json").is_file())

    def test_native_exit_124_and_signals_are_not_timeouts(self):
        with tempfile.TemporaryDirectory() as folder:
            program = ('!(assert (== (shell "exit 124" 1) "SHELL_ERROR exit 124\\n"))\n'
                       '!(assert (== (shell "kill -TERM $$" 1) "SHELL_ERROR signal 15\\n"))\n'
                       '!(assert (== (shell "printf should-not-run" -0.000001) '
                       '"SHELL_ERROR timeout seconds must be nonnegative"))\n')
            result = self.engine("cetta", program, Path(folder))
            self.assertNotIn("Assertion failed", result.stderr)

    def test_native_background_child_does_not_delay_return(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            command = f"sleep 10 </dev/null & printf '%s' $! > '{work / 'child'}'; printf ready"
            started = time.monotonic()
            try:
                result = self.engine("cetta", f'!(println! (shell {json.dumps(command)} 0))\n', work)
                self.assertIn("ready", result.stdout)
                self.assertLess(time.monotonic() - started, 5)
            finally:
                if (work / "child").exists():
                    try: os.kill(int((work / "child").read_text()), 9)
                    except ProcessLookupError: pass


if __name__ == "__main__": unittest.main()
