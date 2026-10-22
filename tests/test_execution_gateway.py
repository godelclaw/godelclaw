"""Qualify the real IPC, child processes and durable queue with local tools."""

from concurrent.futures import ThreadPoolExecutor
from isolated_env import isolated_environ  # noqa: E402
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from execution_gateway import Client, GatewayError  # noqa: E402
from execution_gateway.store import TERMINAL  # noqa: E402


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gateway-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workers = {}
        self.logs = {}
        self.clients = {}
        self.addCleanup(self.stop_all)
        self.start("alpha")

    def start(self, agent, extra_tools=None):
        root = self.base / agent
        root.mkdir(exist_ok=True)
        secret = root / "test-token"
        secret.write_text("synthetic-token-" + agent)
        tools = {}
        for name in ("echo", "delay", "crash", "identity", "invalid-json", "large", "write"):
            tools[name] = {
                "command": [sys.executable, str(ROOT / "tests/fixtures/gateway_tool.py"), name],
                "effect": "write" if name == "write" else "read",
                "result_format": "json",
            }
        tools["identity"]["credential_files"] = {"TEST_AGENT_TOKEN": str(secret)}
        tools.update(extra_tools or {})
        config = root / "config.json"
        config.write_text(json.dumps({"agent": agent, "state_directory": str(root), "tools": tools}))
        log = open(root / "worker.log", "ab")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        # The worker is launched independently of every test cognition client.
        process = subprocess.Popen([sys.executable, "-m", "execution_gateway",
                                    "--config", str(config)], cwd=ROOT,
                                   env=env, stdout=log, stderr=log, start_new_session=True)
        self.workers[agent] = process
        self.logs[agent] = log
        client = Client(root / "gateway.sock", agent)
        self.clients[agent] = client
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if process.poll() is not None:
                self.fail((root / "worker.log").read_text())
            try:
                client.status("readiness")
                return client
            except (OSError, GatewayError):
                time.sleep(0.01)
        self.fail("gateway did not become ready")

    def stop_all(self):
        for process in self.workers.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for log in self.logs.values():
            log.close()

    def wait(self, request_id, agent="alpha", states=TERMINAL):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            receipt = self.clients[agent].status(request_id)
            if receipt and receipt["state"] in states:
                return receipt
            time.sleep(0.01)
        self.fail(f"request {request_id} did not reach {states}: {receipt}")

    def test_crashed_tool_and_bad_json_leave_gateway_usable(self):
        client = self.clients["alpha"]
        for request_id, tool in (("crash-1", "crash"), ("bad-json", "invalid-json")):
            client.submit(request_id, tool)
            self.assertEqual(self.wait(request_id)["state"], "failed")
        client.submit("good", "echo", ["kept as one argument"], {"ok": True})
        receipt = self.wait("good")
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(receipt["result"], {"input": {"ok": True},
                                            "arguments": ["kept as one argument"]})
        self.assertIsNone(self.workers["alpha"].poll())

    def test_busy_worker_does_not_block_cognition_or_another_agent(self):
        other = self.start("beta")
        started = time.monotonic()
        self.clients["alpha"].submit("slow", "delay", input={"seconds": 1})
        self.assertLess(time.monotonic() - started, 0.5)
        self.wait("slow", states={"running"})
        started = time.monotonic()
        self.assertEqual(self.clients["alpha"].status("slow")["state"], "running")
        other.submit("fast", "echo", input="independent")
        self.assertEqual(self.wait("fast", "beta")["state"], "succeeded")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.wait("slow")["state"], "succeeded")

    def test_agent_queues_and_configured_identities_are_separate(self):
        self.start("beta")
        for agent in ("alpha", "beta"):
            self.clients[agent].submit("same-id", "identity")
            receipt = self.wait("same-id", agent)
            expected = hashlib.sha256(("synthetic-token-" + agent).encode()).hexdigest()
            self.assertEqual(receipt["result"], {"digest": expected})
        self.assertIsNone(self.clients["beta"].status("only-alpha"))
        wrong = Client(self.base / "alpha/gateway.sock", "beta")
        with self.assertRaises(GatewayError):
            wrong.submit("impersonation", "echo")
        for agent in ("alpha", "beta"):
            db = (self.base / agent / "queue.sqlite3").read_bytes()
            self.assertNotIn(("synthetic-token-" + agent).encode(), db)

    def test_tool_discovery_does_not_expose_command_or_credentials(self):
        tools = self.clients["alpha"].tools()
        self.assertEqual(tools["identity"], {"effect": "read"})
        self.assertNotIn("credential_files", json.dumps(tools))
        self.assertNotIn("synthetic-token", json.dumps(tools))

    def test_concurrent_duplicates_execute_one_write_and_conflicts_are_rejected(self):
        client = self.clients["alpha"]
        path = self.base / "effect.txt"
        value = {"path": str(path)}
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: client.submit("one-write", "write", input=value), range(8)))
        self.assertTrue(all(r["id"] == "one-write" for r in replies))
        receipt = self.wait("one-write")
        self.assertEqual(receipt["state"], "succeeded")
        self.assertEqual(path.read_text(), "effect\n")
        with self.assertRaises(GatewayError) as context:
            client.submit("one-write", "echo")
        self.assertEqual(context.exception.code, "conflict")
        self.assertEqual(len([r for r in receipt["receipts"] if r["state"] == "running"]), 1)

    def test_deadlines_and_queued_cancellation(self):
        client = self.clients["alpha"]
        client.submit("timeout", "delay", input={"seconds": 2}, timeout_seconds=0.1)
        receipt = self.wait("timeout")
        self.assertEqual((receipt["state"], receipt["error"]), ("failed", "deadline"))
        client.submit("blocking", "delay", input={"seconds": 0.3})
        self.wait("blocking", states={"running"})
        path = self.base / "not-written.txt"
        client.submit("cancel-before-start", "write", input={"path": str(path)})
        self.assertEqual(client.cancel("cancel-before-start")["state"], "cancelled")
        client.submit("already-expired", "write", input={"path": str(path)}, timeout_seconds=-1)
        self.assertEqual(self.wait("already-expired")["state"], "expired")
        self.assertFalse(path.exists())

    def test_killed_gateway_preserves_uncertainty_and_never_replays_write(self):
        client = self.clients["alpha"]
        path = self.base / "uncertain.txt"
        value = {"path": str(path), "delay": 0.5}
        client.submit("lost-receipt", "write", input=value)
        until = time.monotonic() + 5
        while not path.exists() and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(path.exists())
        self.workers["alpha"].kill()
        self.workers["alpha"].wait()
        self.logs["alpha"].close()
        client = self.start("alpha")
        receipt = client.submit("lost-receipt", "write", input=value)
        self.assertEqual((receipt["state"], receipt["error"]), ("uncertain", "gateway-restarted"))
        time.sleep(0.6)  # let the original test-only child finish
        self.assertEqual(path.read_text(), "effect\n")
        client.submit("after-restart", "echo", input="healthy")
        self.assertEqual(self.wait("after-restart")["state"], "succeeded")

    def test_failed_and_cancelled_started_writes_are_uncertain(self):
        client = self.clients["alpha"]
        path = self.base / "partial-write.txt"
        client.submit("partial", "write", input={"path": str(path), "fail": True})
        self.assertEqual(self.wait("partial")["state"], "uncertain")
        value = {"path": str(path), "delay": 1}
        client.submit("cancel-running", "write", input=value)
        self.wait("cancel-running", states={"running"})
        client.cancel("cancel-running")
        self.assertEqual(self.wait("cancel-running")["state"], "uncertain")

    def test_queued_intent_does_not_run_under_a_changed_tool_binding(self):
        client = self.clients["alpha"]
        client.submit("hold-queue", "delay", input={"seconds": 1})
        self.wait("hold-queue", states={"running"})
        path = self.base / "changed-binding.txt"
        client.submit("queued-write", "write", input={"path": str(path)})
        self.workers["alpha"].terminate()
        self.workers["alpha"].wait(timeout=5)
        self.logs["alpha"].close()
        self.start("alpha", {"write": {
            "command": [sys.executable, str(ROOT / "tests/fixtures/gateway_tool.py"), "write"],
            "effect": "write", "result_format": "json",
            "environment": {"TOOL_BINDING_REVISION": "changed"}}})
        receipt = self.wait("queued-write")
        self.assertEqual((receipt["state"], receipt["error"]),
                         ("failed", "tool-configuration-changed"))
        self.assertFalse(path.exists())

    def test_oversized_output_and_malformed_request_are_contained(self):
        client = self.clients["alpha"]
        with self.assertRaises(GatewayError):
            client.request("submit", request={"id": "bad"})
        client.submit("large", "large", input={"bytes": 200000})
        self.assertEqual(self.wait("large")["error"], "result-too-large")
        client.submit("good-again", "echo")
        self.assertEqual(self.wait("good-again")["state"], "succeeded")

    def test_malformed_wire_envelopes_leave_service_usable(self):
        client = self.clients["alpha"]
        for message in (b"[]\n", b"{}\n", b"not-json\n"):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(3)
                peer.connect(client.socket_path)
                peer.sendall(message)
                with peer.makefile("rb") as stream:
                    receipt = json.loads(stream.readline())
            self.assertEqual(receipt["error"]["code"], "invalid-request")
        client.submit("after-wire-error", "echo")
        self.assertEqual(self.wait("after-wire-error")["state"], "succeeded")

    def test_cognition_process_exit_does_not_end_accepted_job(self):
        client = self.clients["alpha"]
        code = "from execution_gateway import Client; " \
               f"Client({client.socket_path!r}, 'alpha').submit('detached', 'delay', input={{'seconds':0.15}})"
        result = subprocess.run([sys.executable, "-c", code],
                                env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wait("detached")["state"], "succeeded")

    def test_invalid_bridge_arguments_are_data_not_runtime_exceptions(self):
        import gateway_bridge
        with mock.patch.dict(os.environ, {
                "METTACLAW_GATEWAY_SOCKET": self.clients["alpha"].socket_path,
                "METTACLAW_GATEWAY_AGENT": "alpha"}):
            reply = json.loads(gateway_bridge.submit("bad-args", "echo", '"not a list"'))
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], "invalid-request")
        self.assertIsNone(self.clients["alpha"].status("bad-args"))

    def test_unrecordable_completion_stops_dispatch_and_recovers_conservatively(self):
        path = self.base / "recording-failure.txt"
        database = self.base / "alpha/queue.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TRIGGER no_finish BEFORE UPDATE OF state ON jobs"
                       " WHEN NEW.state NOT IN ('queued','running')"
                       " BEGIN SELECT RAISE(ABORT,'injected receipt failure'); END")
        self.clients["alpha"].submit("unrecordable", "write", input={"path": str(path)})
        self.workers["alpha"].wait(timeout=5)
        self.assertNotEqual(self.workers["alpha"].returncode, 0)
        self.assertEqual(path.read_text(), "effect\n")
        self.logs["alpha"].close()
        with sqlite3.connect(database) as db:
            db.execute("DROP TRIGGER no_finish")
        client = self.start("alpha")
        receipt = client.submit("unrecordable", "write", input={"path": str(path)})
        self.assertEqual(receipt["state"], "uncertain")
        self.assertEqual(path.read_text(), "effect\n")

    def engine_environment(self, agent, engine):
        env = isolated_environ()
        env.update({
            "METTACLAW_SKIP_INITIALIZE": "1", "METTACLAW_ENGINE": engine,
            "METTACLAW_ENGINE_STATE_PATH": str(self.base / agent / "engine"),
            "METTACLAW_GATEWAY_SOCKET": self.clients[agent].socket_path,
            "METTACLAW_GATEWAY_AGENT": agent,
            "METTACLAW_MODEL_STATE_PATH": str(self.base / agent / "model-state"),
            "OPENAI_API_KEY": "unused-test-key",
        })
        return env

    def test_real_petta_and_cetta_clients_continue_while_tools_run(self):
        cetta = Path(os.environ.get("CETTA_BIN", Path.home() / "repos/CeTTa-runtime/cetta"))
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        exercised = []
        for engine in ("petta", "cetta"):
            if not ((petta / "run.sh").is_file() if engine == "petta" else cetta.is_file()):
                continue
            agent = "probe-" + engine
            self.start(agent)
            env = self.engine_environment(agent, engine)
            env["CETTA_BIN"] = str(cetta.resolve())
            result = subprocess.run(["./run.sh", "tests/fixtures/gateway_bridge_probe.metta"],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=20)
            with self.subTest(engine=engine):
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3500:])
                self.assertIn("GATEWAY_COGNITION_CONTINUES", result.stdout)
                self.assertIn("GATEWAY_BRIDGE_OK", result.stdout)
                # The printed status was observed before the slow tool ended.
                self.assertRegex(result.stdout, r'(?s)state.*(?:queued|running).*GATEWAY_COGNITION_CONTINUES')
                self.assertEqual(self.wait("engine-crash", agent)["state"], "failed")
                next_call = self.wait("engine-next", agent)
                self.assertEqual(next_call["result"]["input"], "still-alive")
            exercised.append(engine)
        if not exercised:
            self.skipTest("native MeTTa engines are unavailable")

    def test_native_evaluator_failure_is_an_ordinary_gateway_receipt(self):
        cetta = Path(os.environ.get("CETTA_BIN", Path.home() / "repos/CeTTa-runtime/cetta"))
        if not cetta.is_file():
            self.skipTest("CeTTa executable is unavailable")
        python_env = Path(os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta"))
        client = self.start("native", {"native-fault": {
            "command": [str(cetta.resolve()), "--lang", "petta",
                        "tests/fixtures/gateway_native_fault.metta"],
            "cwd": str(ROOT), "effect": "read", "environment": {
                "PYTHONHOME": str(python_env),
                "LD_LIBRARY_PATH": str(python_env / "lib"),
                "PYTHONNOUSERSITE": "1", "OPENAI_API_KEY": "unused-test-key",
            }}})
        client.submit("native-error", "native-fault")
        receipt = self.wait("native-error", "native")
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(receipt["result"]["exit_code"], 2)
        self.assertIn("uncaught PeTTa error", receipt["result"]["stderr"])
        self.assertNotIn("SHOULD_NOT_RUN", receipt["result"]["stdout"])
        client.submit("after-native-error", "echo", input="alive")
        self.assertEqual(self.wait("after-native-error", "native")["state"], "succeeded")

    def test_named_gateway_skills_on_both_native_engines(self):
        cetta = Path(os.environ.get("CETTA_BIN", Path.home() / "repos/CeTTa-runtime/cetta"))
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        exercised = []
        for engine in ("petta", "cetta"):
            if not ((petta / "run.sh").is_file() if engine == "petta" else cetta.is_file()):
                continue
            agent = "skills-" + engine
            self.start(agent, {"read-lines": {
                "command": [sys.executable, "-m", "execution_gateway.read_tools", "read-lines"],
                "cwd": str(ROOT), "effect": "read", "result_format": "json",
                "environment": {"PYTHONPATH": str(ROOT / "src"), "METTACLAW_GATEWAY_AGENT": agent}}})
            env = self.engine_environment(agent, engine)
            env["CETTA_BIN"] = str(cetta.resolve())
            result = subprocess.run(["./run.sh", "tests/fixtures/gateway_skills_probe.metta"],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=20)
            with self.subTest(engine=engine):
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3500:])
                self.assertIn("GATEWAY_SKILL_CONTINUES", result.stdout)
                self.assertNotIn('usage: (gateway-start', result.stdout)
                receipt = self.wait("skill-read", agent)
                self.assertEqual(receipt["state"], "succeeded")
                self.assertEqual(receipt["result"]["agent"], agent)
                self.assertIn("import!", receipt["result"]["value"])
            exercised.append(engine)
        if not exercised:
            self.skipTest("native MeTTa engines are unavailable")

    def test_read_only_adapter_rejects_unknown_tools_and_extra_arguments(self):
        from execution_gateway import read_tools
        with self.assertRaises(ValueError):
            read_tools.execute("shell", {"command": "echo forbidden"})
        with self.assertRaises(ValueError):
            read_tools.execute("read-lines", {"path": "run.metta", "command": "ignored"})
        result = subprocess.run(
            [sys.executable, "-m", "execution_gateway.read_tools", "vitals", "extra"],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            input="{}", capture_output=True, text=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
