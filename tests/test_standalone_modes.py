"""Terminal input, output and upstream requests without agent setup."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("standalone_launcher", ROOT / "modes/launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append((self.path, value))
        if self.server.mode == "iter":
            message = {"role": "assistant", "tool_calls": [
                {"id": "fixture-send", "type": "function", "function": {
                    "name": "send", "arguments": json.dumps({"channel": "terminal", "content": "terminal-fixture-reply"})}},
                {"id": "fixture-nop", "type": "function", "function": {"name": "nop", "arguments": "{}"}}]}
        else:
            message = {"role": "assistant", "content": "send terminal-fixture-reply\nnop"}
        body = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StandaloneModesTests(unittest.TestCase):
    def test_iter_seed_keeps_upstream_prompt_and_user_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = root / "agent"
            (agent / "memory").mkdir(parents=True)
            (agent / "memory/prompt.txt").write_text("must not appear by default")
            runtime = root / "runtime"
            launcher.prepare_iter(runtime, agent, standalone=True)
            self.assertEqual((runtime / "prompt.txt").read_bytes(),
                             (ROOT / "modes/iter/upstream/prompt.txt").read_bytes())
            self.assertFalse((runtime / "channels/telegram.py").exists())
            (runtime / "prompt.txt").write_text("self-authored prompt")
            launcher.prepare_iter(runtime, agent, standalone=True)
            self.assertEqual((runtime / "prompt.txt").read_text(), "self-authored prompt")

    def test_standalone_cannot_inherit_instance_latches_or_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with mock.patch.object(launcher.sys, "prefix", str(state / "venv")), \
                 mock.patch.object(launcher.sys, "base_prefix", str(state / "base")), \
                 mock.patch.object(launcher.sys, "executable", str(state / "venv/bin/python")):
                value = launcher.standalone_environment("iter", state, state / "iter", {
                    "METTACLAW_TELEGRAM_SERVICE_SOCKET": "/absent/socket",
                    "METTACLAW_LIFECYCLE_PATH": "/absent/paused.json",
                    "METTACLAW_LLM_TRANSPORT": "native",
                    "CETTA_IO_AUTH_PRIVATE_ENV": "PRIVATE_KEY",
                    "PYTHONHOME": "/absent/venv", "PATH": "/usr/bin",
                    "LLM_MODEL": "upstream-model", "AI_API_KEY": "fixture"})
            self.assertNotIn("METTACLAW_TELEGRAM_SERVICE_SOCKET", value)
            self.assertNotIn("CETTA_IO_AUTH_PRIVATE_ENV", value)
            self.assertNotIn("PYTHONHOME", value)
            self.assertEqual(value["METTACLAW_LIFECYCLE_PATH"], str(state / "lifecycle.json"))
            self.assertEqual(value["METTACLAW_LLM_TRANSPORT"], "python")
            self.assertEqual(value["LLM_MODEL"], "upstream-model")
            self.assertTrue(value["PATH"].startswith(str(state / "venv/bin")))

    def test_profiles_cannot_reuse_each_others_state(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            launcher.prepare_iter(runtime, ROOT, standalone=True)
            previous = (runtime / "prompt.txt").read_bytes()
            with self.assertRaisesRegex(RuntimeError, "different launch profile"):
                launcher.prepare_iter(runtime, ROOT, standalone=False)
            self.assertEqual((runtime / "prompt.txt").read_bytes(), previous)
            self.assertFalse((runtime / "channels/telegram.py").exists())

    def test_supervised_launch_requires_its_channel_before_seeding(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(sys, "argv", ["launch.py", "--mode", "iter", "--profile", "supervised", "--state-dir", directory]):
                with self.assertRaises(SystemExit) as error:
                    launcher.main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((Path(directory) / "iter").exists())

    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"), "set MODE_TEST_CETTA_BIN")
    def test_real_cetta_terminal_modes(self):
        for mode in ("iter", "omega"):
            with self.subTest(mode=mode):
                self.check_process(mode, "cetta")

    @unittest.skipUnless(os.environ.get("MODE_TEST_PETTA_ROOT"), "set MODE_TEST_PETTA_ROOT")
    def test_real_petta_terminal_modes(self):
        for mode in ("iter", "omega"):
            with self.subTest(mode=mode):
                self.check_process(mode, "petta")

    def check_process(self, mode, engine):
        python = os.environ.get("MODE_TEST_STANDALONE_PYTHON", sys.executable)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
            server.mode, server.received = mode, []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
            environment.update(
                METTACLAW_MODE_PYTHON=python,
                METTACLAW_TELEGRAM_SERVICE_SOCKET="/absent/service-must-not-be-used",
                CETTA_BIN=os.environ.get("MODE_TEST_CETTA_BIN", ""),
                PETTA_ROOT=os.environ.get("MODE_TEST_PETTA_ROOT", ""),
                LLM_MODEL="fixture-model", AI_API_KEY="fixture-key",
                BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
                OMEGA_provider="OpenAIAPI", OPENAIAPI_API_KEY="fixture-key",
                OMEGA_openaiapi_model="fixture-model",
                OMEGA_openaiapi_url=f"http://127.0.0.1:{server.server_port}/v1")
            stdout, stderr = root / "stdout.log", root / "stderr.log"
            with stdout.open("w") as out, stderr.open("w") as err:
                process = subprocess.Popen(["bash", str(ROOT / "run.sh"), "--mode", mode,
                    "--engine", engine, "--state-dir", str(root / "state")], cwd=root,
                    env=environment, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True)
                try:
                    process.stdin.write("terminal-fixture-input\n")
                    process.stdin.flush()
                    deadline = time.monotonic() + 40
                    while time.monotonic() < deadline and process.poll() is None:
                        if "terminal-fixture-reply" in stdout.read_text():
                            break
                        time.sleep(.05)
                    self.assertIn("terminal-fixture-reply", stdout.read_text(), stderr.read_text()[-3000:])
                    self.assertTrue(server.received, stderr.read_text()[-3000:])
                    endpoint, request = server.received[0]
                    self.assertEqual(endpoint, "/v1/chat/completions")
                    self.assertEqual(request["model"], "fixture-model")
                    self.assertEqual(sum(m["role"] == "system" for m in request["messages"]), 1)
                    self.assertIn("terminal-fixture-input", json.dumps(request["messages"]))
                    self.assertNotIn("CHANNEL ROUTES", json.dumps(request))
                    runtime = root / "state" / mode
                    if mode == "iter":
                        self.assertEqual(request["tool_choice"], "required")
                        self.assertEqual(request["max_tokens"], 2524)
                        self.assertTrue((runtime / "experience.json").exists())
                    else:
                        self.assertEqual(request["max_tokens"], 6000)
                        self.assertTrue((runtime / "repos/Omega/memory/history.metta").exists())
                    # Lightweight startup never seeds the framework identity.
                    self.assertFalse((root / "memory/prompt.txt").exists())
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.stdin.close()
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
