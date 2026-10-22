"""Startup failures and mode selection races, without provider credentials."""
import importlib.util
from isolated_env import isolated_environ  # noqa: E402
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import engine_modes
import helper
import loop_modes

spec = importlib.util.spec_from_file_location("mode_launcher", ROOT / "modes/launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ModeLauncherTests(unittest.TestCase):
    def test_omega_runtime_loads_reasoning_and_selects_native_memory_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            entry, _paths = launcher.prepare_omega(
                runtime, ROOT, standalone=False)
            source = entry.read_text(encoding="utf-8")
            memory = (runtime / "memory.metta").read_text(encoding="utf-8")
            ledger = json.loads((runtime / "adapter-ledger.json").read_text())
            self.assertIn("(library Omega lib_nal)", source)
            self.assertIn("(library Omega lib_pln)", source)
            self.assertIn("semantic_memory_cetta.metta", source)
            self.assertIn("runtime_host.native_memory_flag", source)
            self.assertIn("semantic:remember", memory)
            self.assertIn("semantic:query", memory)
            self.assertIn("NAL and PLN", ledger["reasoning"])
            self.assertIn("native semantic memory", ledger["memory"])
            self.assertEqual(ledger["tool_protocol"]["carriers"],
                             ["ToolSpecV1", "ToolCallV1", "ToolResultV1"])
            self.assertEqual(ledger["tool_protocol"]["representation"],
                             "MeTTa constructors over named JsonObject/JsonPair fields")
            self.assertEqual(len(ledger["tool_protocol"]["sha256"]), 64)
            self.assertEqual(ledger["upstream"],
                             "94d7b9a2e1ef6b5873aee764ca1ef16de8dcd943")

    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"),
                         "set MODE_TEST_CETTA_BIN")
    def test_omega_nal_and_pln_execute_on_cetta(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "reasoning.metta"
            vendor = ROOT / "modes/omega/upstream"
            probe.write_text(
                '!(import! &self ' + json.dumps(str(vendor / "lib_nal.metta")) + ')\n' +
                '!(import! &self ' + json.dumps(str(vendor / "lib_pln.metta")) + ')\n' +
                '!(println! (NAL (collapse (|- ((--> a b) (stv 1.0 0.9)) '
                '((--> b c) (stv 1.0 0.9))))))\n' +
                '!(println! (PLN (collapse (|~ ((Inheritance a b) (stv 1.0 0.9)) '
                '((Implication (Inheritance a b) (Inheritance a c)) (stv 1.0 0.9))))))\n')
            result = subprocess.run(
                [os.environ["MODE_TEST_CETTA_BIN"], "--lang", "petta", str(probe)],
                env={**isolated_environ(), "PYTHONHOME": sys.prefix,
                     "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib")},
                text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("(--> a c)", result.stdout)
            self.assertIn("(Inheritance a c)", result.stdout)

    def test_omega_host_bindings_have_finite_answers_on_both_engines(self):
        engines = []
        if os.environ.get("MODE_TEST_CETTA_BIN"):
            engines.append([os.environ["MODE_TEST_CETTA_BIN"], "--lang", "petta"])
        if os.environ.get("MODE_TEST_PETTA_ROOT"):
            engines.append(["bash", str(Path(os.environ["MODE_TEST_PETTA_ROOT"]) / "run.sh")])
        if not engines:
            self.skipTest("set MODE_TEST_CETTA_BIN or MODE_TEST_PETTA_ROOT")
        for command in engines:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                present = root / "present.txt"
                present.write_text("fixture")
                entry = root / "bindings.metta"
                entry.write_text(
                    '!(import! &self ' + json.dumps(str(ROOT / "modes/runtime_host.py")) + ')\n' +
                    launcher.OMEGA_HOST + '\n' +
                    '!(println! (COUNT (size-atom (collapse (py-str ("a" "b"))))))\n' +
                    '!(println! (TEXT (py-str ("a" "b"))))\n' +
                    '!(println! (EXISTS (if (exists-file ' + json.dumps(str(present)) + ') yes no)))\n' +
                    '!(println! (MISSING (if (exists-file ' + json.dumps(str(root / "absent")) + ') yes no)))\n' +
                    '!(println! (DIRECTORY (if (exists-directory ' + json.dumps(directory) + ') yes no)))\n')
                env = {**isolated_environ(), "PYTHONHOME": sys.prefix,
                       "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
                       "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "modes")])}
                process = subprocess.Popen(command + [str(entry), "--silent"], cwd=root,
                    env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, start_new_session=True)
                try:
                    out, err = process.communicate(timeout=20)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                self.assertEqual(process.returncode, 0, err)
                for expected in ("(COUNT 1)", "(TEXT ab)", "(EXISTS yes)", "(MISSING no)", "(DIRECTORY yes)"):
                    self.assertIn(expected, out.replace('"', ''))

    def test_real_engines_keep_framework_startup_and_new_switch_separate(self):
        engines = []
        if os.environ.get("MODE_TEST_CETTA_BIN"):
            engines.append(("cetta", [os.environ["MODE_TEST_CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk"]))
        if os.environ.get("MODE_TEST_PETTA_ROOT"):
            engines.append(("petta", ["bash", str(Path(os.environ["MODE_TEST_PETTA_ROOT"]) / "run.sh")]))
        if not engines:
            self.skipTest("set MODE_TEST_CETTA_BIN or MODE_TEST_PETTA_ROOT")
        for engine, command in engines:
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / "mode.json").write_text('{"mode":"omega"}')
                (base / "engine").write_text(engine + "\n")
                flag = base / "recycle"
                flag.touch()
                probe = base / "probe.metta"
                probe.write_text('!(import! &self (library lib_import))\n' +
                    ''.join('!(import! &self ' + json.dumps(str(ROOT / "src" / name)) + ')\n'
                            for name in ("helper.py", "utils.metta", "open_assemblage.metta", "weak_process_core.metta", "loop_policy.metta", "loop.metta")) +
                    '!(println! (INITIAL (collapse (initLoop))))\n'
                    '!(processLoop (rooted weak-process-core-v1 coordinates-empty))\n'
                    '!(println! SHOULD_NOT_RUN_AFTER_SWITCH)\n')
                env = {**isolated_environ(), "PYTHONHOME": sys.prefix,
                       "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
                       "PYTHONPATH": str(ROOT / "src"),
                       "METTACLAW_LOOP_MODE_PATH": str(base / "mode.json"),
                       "METTACLAW_ENGINE_STATE_PATH": str(base / "engine"),
                       "METTACLAW_WORKING_SET_PATH": str(base / "working.json"),
                       "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw",
                       "METTACLAW_ACTIVE_ENGINE": engine,
                       "METTACLAW_RECYCLE_REQUEST_PATH": str(flag)}
                result = subprocess.run(command + [str(probe), "default", "--silent"],
                    cwd=base, env=env, text=True, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertIn("(rooted weak-process-core-v1", result.stdout)
                self.assertIn("coordinate active-policy godelclaw", result.stdout)
                self.assertNotIn("SHOULD_NOT_RUN_AFTER_SWITCH", result.stdout)
                self.assertTrue(flag.exists(), "boot consumed a newer switch")

    def test_upstream_shell_runner_without_shebang(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            runner = path / "run.sh"
            runner.write_text('printf "%s\\n" "$1" "$2" > argv.txt\nexit 7\n')
            runner.chmod(0o755)
            # This is how a direct exec fails, even with executable mode bits.
            with self.assertRaises(OSError) as failure:
                subprocess.Popen([str(runner)], cwd=path)
            self.assertEqual(failure.exception.errno, 8)
            entry = path / "a program.metta"
            command = launcher.engine_command("petta", entry, {"PETTA_ROOT": directory})
            self.assertEqual(launcher.run_child(command, path, isolated_environ()), 7)
            self.assertEqual((path / "argv.txt").read_text().splitlines(), [str(entry), "--silent"])

    def test_spawn_failure_keeps_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                launcher.run_child([directory + "/missing-engine"], directory, isolated_environ())

    def test_killed_engine_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            status = launcher.run_child([sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGKILL)"], directory, isolated_environ())
            self.assertEqual(status, 128 + signal.SIGKILL)

    def test_new_selection_does_not_change_running_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            mode = Path(directory) / "mode.json"
            engine = Path(directory) / "engine"
            engine.write_text("cetta\n")
            env = {"METTACLAW_LOOP_MODE_PATH": str(mode),
                   "METTACLAW_ENGINE_STATE_PATH": str(engine),
                   "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw",
                   "METTACLAW_ACTIVE_ENGINE": "cetta",
                   "METTACLAW_RECYCLE_REQUEST_PATH": str(Path(directory) / "absent")}
            with mock.patch.dict(os.environ, env):
                mode.write_text(json.dumps({"mode": "godelclaw"}))
                self.assertFalse(engine_modes.recycle_requested())
                mode.write_text(json.dumps({"mode": "iter"}))
                self.assertEqual(loop_modes.process_mode(), "godelclaw")
                self.assertEqual(helper.recycle_requested(), 1)
                mode.write_text(json.dumps({"mode": "godelclaw"}))
                engine.write_text("petta\n")
                self.assertEqual(helper.recycle_requested(), 1)


if __name__ == "__main__":
    unittest.main()
