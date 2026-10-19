"""Compare native helpers with Python, then use a separate tmux server."""

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper  # noqa: E402
import tmux_bridge  # noqa: E402

STUB = '''#!PYTHON -E
import json,os,sys,time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['TMUX_FIXTURE_LOG'],'a') as log:
    log.write(json.dumps({'argv':args,'at':time.monotonic()})+'\\n')
config=json.loads(Path(os.environ['TMUX_FIXTURE_CONFIG']).read_text())
operation='submit' if args==['send-keys','-t',config.get('target','agent:5'),'Enter'] else args[0]
value=config.get(operation, {})
sys.stdout.write(value.get('stdout',''))
sys.stderr.write(value.get('stderr',''))
sys.exit(value.get('code',0))
'''


class NativeTmux(unittest.TestCase):
    def setUp(self):
        binary = os.environ.get("CETTA_BIN", "")
        if not binary or not Path(binary).is_file():
            self.skipTest("set CETTA_BIN")
        self.binary = str(Path(binary).resolve())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.log = self.work / "calls.jsonl"
        self.config = self.work / "fixture.json"
        self.config.write_text("{}")
        stub = self.bin / "tmux"
        stub.write_text(STUB.replace("PYTHON", sys.executable, 1))
        stub.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        TMUX_FIXTURE_LOG=str(self.log), TMUX_FIXTURE_CONFIG=str(self.config))

    def run_native(self, calls, expected, *, env=None, binary=None, skills=False):
        def value(x):
            if isinstance(x, bool): return "True" if x else "False"
            return json.dumps(x, ensure_ascii=False)
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
            source.write('!(import! &self (library lib_import))\n')
            if skills:
                source.write('!(import! &self ./src/skills)\n'
                             '!(import! &self ./tests/helper_port_probe.py)\n'
                             '!(py-call (helper_port_probe.forbid_python_helpers))\n')
            else:
                source.write('!(import! &self ./src/tmux_cetta)\n')
            for index, ((name, args), answer) in enumerate(zip(calls, expected)):
                expression = "(" + name + (" " if args else "") + " ".join(value(x) for x in args) + ")"
                source.write(f'!(let $answers (collapse {expression}) '
                             '(if (== (length $answers) 1) '
                             '(let $hash (str:sha256 (car-atom $answers)) '
                             f'(println! (TmuxCheck {index} $hash))) '
                             '(println! TMUX_WRONG_ANSWER_COUNT)))\n')
            source.write('!(println! TMUX_NATIVE_OK)\n')
            source.flush()
            result = subprocess.run([binary or self.binary, "--lang", "petta", "--import-mode",
                                     "ancestor-walk", source.name], cwd=ROOT, env=env or self.env,
                                    capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TMUX_NATIVE_OK", result.stdout)
        self.assertNotIn("Error", result.stdout)
        self.assertNotIn("Assert", result.stdout + result.stderr)
        self.assertNotIn("Assertion failed", result.stderr)
        self.assertNotIn("TMUX_WRONG_ANSWER_COUNT", result.stdout)
        for index, answer in enumerate(expected):
            digest = hashlib.sha256(answer.encode()).hexdigest()
            self.assertIn(f'(TmuxCheck {index} "{digest}")', result.stdout)
        return result

    def reference(self, calls):
        with mock.patch.dict(os.environ, self.env), mock.patch.object(tmux_bridge.time, "sleep"):
            result = []
            for name, args in calls:
                if name == "tmux-send":
                    result.append(tmux_bridge.send(args[0], helper.decode_safe_text(args[1])))
                else:
                    result.append(getattr(tmux_bridge, {"tmux-windows": "windows", "tmux-peek": "peek"}[name])(*args))
        self.log.unlink(missing_ok=True)
        return result

    def test_windows_and_peek_match_the_python_oracle(self):
        pane = "  first\r\n\r\n   \nλ 🌸\rlast  \n"
        self.config.write_text(json.dumps({"list-windows": {"stdout": "  agent:5 λ 🌸\n"},
                                           "capture-pane": {"stdout": pane}}))
        calls = [("tmux-windows", [])] + [("tmux-peek", ["agent:5", n])
                  for n in (1, 2, 0, -3, 2.8, "3", "+2", " 2 ", "bad", "2.0")]
        self.run_native(calls, self.reference(calls))
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(records), len(calls))
        self.assertEqual(records[0]["argv"][:3], ["list-windows", "-a", "-F"])

    def test_empty_and_nonzero_exit_results_match(self):
        for config in ({}, {"list-windows": {"code": 1, "stderr": "can't find server\n"},
                            "capture-pane": {"code": 1, "stderr": "can't find window\n"}}):
            self.config.write_text(json.dumps(config))
            calls = [("tmux-windows", []), ("tmux-peek", ["agent:5", 3])]
            self.run_native(calls, self.reference(calls))

    def test_literal_paste_and_enter_are_separate_ordered_effects(self):
        marker = self.work / "must-not-exist"
        text = f"λ _quote_x_quote__newline_$(touch {marker}); _apostrophe_y_apostrophe_"
        calls = [("tmux-send", ["agent:5", text])]
        self.run_native(calls, self.reference(calls))
        paste, enter = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(paste["argv"], ["send-keys", "-l", "-t", "agent:5", "--",
                                          helper.decode_safe_text(text)])
        self.assertEqual(enter["argv"], ["send-keys", "-t", "agent:5", "Enter"])
        self.assertGreaterEqual(enter["at"] - paste["at"], 1.9)
        self.assertFalse(marker.exists())

    def test_no_submit_after_failed_or_empty_paste(self):
        self.config.write_text(json.dumps({"send-keys": {"code": 1, "stderr": "no target"}}))
        calls = [("tmux-send", ["agent:5", "   "]), ("tmux-send", ["agent:5", "hello"])]
        self.run_native(calls, self.reference(calls))
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_submit_failure_is_distinct(self):
        self.config.write_text(json.dumps({"submit": {"code": 1, "stderr": "no Enter"}}))
        calls = [("tmux-send", ["agent:5", "hello"])]
        self.run_native(calls, self.reference(calls))

    def test_skill_import_selects_native_backend_without_python_helpers(self):
        from test_command_dispatch_engine import CommandDispatchEngineTest
        env = dict(CommandDispatchEngineTest()._cetta_env(),
                   TMUX_FIXTURE_CONFIG=str(self.config), TMUX_FIXTURE_LOG=str(self.log),
                   XDG_STATE_HOME=str(self.work / "state"),
                   METTACLAW_ENGINE_STATE_PATH=str(self.work / "engine"))
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        self.config.write_text(json.dumps({"list-windows": {"stdout": "agent:5 λ\n"}}))
        calls = [("tmux-windows", []), ("tmux-peek", ["agent:5", 2]),
                 ("tmux-send", ["agent:5", "literal _quote_λ_quote_"])]
        self.run_native(calls, self.reference(calls), env=env, skills=True)

    def test_swi_selects_the_python_adapter(self):
        from test_command_dispatch_engine import CommandDispatchEngineTest
        harness = CommandDispatchEngineTest()
        petta = harness._petta_root()
        if not (petta / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        env = dict(harness._cetta_env(), METTACLAW_ACTIVE_ENGINE="petta",
                   PETTA_PATH=str(petta), TMUX_FIXTURE_CONFIG=str(self.config),
                   TMUX_FIXTURE_LOG=str(self.log))
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        self.config.write_text(json.dumps({"list-windows": {"stdout": "agent:5 λ\n"}}))
        calls = [("tmux-windows", []), ("tmux-peek", ["agent:5", 2]),
                 ("tmux-send", ["agent:5", "literal _quote_λ_quote_"])]
        expected = self.reference(calls)
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
            source.write('!(import! &self (library lib_import))\n'
                         '!(import! &self ./src/helper.py)\n'
                         '!(import! &self ./src/tmux_io)\n'
                         '!(import! &self ./tests/file_tools_probe.py)\n')
            for (name, args), answer in zip(calls, expected):
                expression = "(" + name + " " + " ".join(json.dumps(x) for x in args) + ")"
                source.write(f'!(let $answers (collapse {expression}) '
                             '(if (== (length $answers) 1) '
                             '(let $answer (car-atom $answers) '
                             f'(py-call (file_tools_probe.check $answer {json.dumps(answer, ensure_ascii=False)}))) '
                             '(py-call (file_tools_probe.check "wrong answer count" "one"))))\n')
            source.write('!(println! TMUX_SWI_OK)\n')
            source.flush()
            result = subprocess.run(["bash", str(petta / "run.sh"), source.name, "default"],
                                    cwd=ROOT, env=env, text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TMUX_SWI_OK", result.stdout)
        self.assertNotIn("Error", result.stdout + result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 4)

    def test_real_separate_server(self):
        tmux = shutil.which("tmux")
        if not tmux:
            self.skipTest("tmux is unavailable")
        socket = self.work / "separate.sock"
        subprocess.run([tmux, "-f", os.devnull, "-S", str(socket), "new-session",
                        "-d", "-s", "fixture", "cat"],
                       check=True, capture_output=True)
        try:
            # An explicit socket in the executable wrapper also protects the
            # default server if an environment variable is lost in the port.
            (self.bin / "tmux").write_text("#!" + sys.executable + "\nimport os,sys\n"
                + f"os.execv({tmux!r}, [{tmux!r}, '-S', {str(socket)!r}, *sys.argv[1:]])\n")
            env = dict(self.env, TMUX=str(socket) + ",0,0")
            self.run_native([("tmux-send", ["fixture:0", "native literal λ 🌸"])],
                            ['sent to fixture:0 — the session will see it as user input; '
                             '(tmux-peek "fixture:0" 30) in a later turn to read the reply'], env=env)
            captured = subprocess.check_output([tmux, "-S", str(socket), "capture-pane", "-p"], text=True)
            self.assertIn("native literal λ 🌸", captured)
        finally:
            subprocess.run([tmux, "-S", str(socket), "kill-server"], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
