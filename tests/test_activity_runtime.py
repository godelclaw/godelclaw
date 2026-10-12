"""Actual C coordinator, process workers and native HTTP model decoding.

Set CETTA_ACTIVITY_SERVICE and CETTA_ACTIVITY_ENGINE to qualified binaries.
The HTTP fixture delays real Chat Completions responses; no paid calls occur.
An optional CETTA_ACTIVITY_PYTHON_PREFIX selects the embedded engine Python.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = os.environ.get("CETTA_ACTIVITY_SERVICE")
ENGINE = os.environ.get("CETTA_ACTIVITY_ENGINE")
PREFIX = os.environ.get("CETTA_ACTIVITY_PYTHON_PREFIX")
DELAY = float(os.environ.get("CETTA_ACTIVITY_REPLAY_SECONDS", "16"))
RESULT = re.compile(r'^result (\S+) \(activity:result 1 "[A-Za-z0-9_.-]+" [0-9]+ (current|late) (".*")\)$')


class Fixture:
    def __init__(self):
        self.calls = []; self.lock = threading.Lock()
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt = request["messages"][-1]["content"]
                with fixture.lock: fixture.calls.append((prompt, time.monotonic()))
                if prompt == "dropped-response":
                    self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
                seconds = DELAY if prompt.startswith("slow") else .1
                time.sleep(seconds)
                result = {"id": "fixture", "model": request["model"], "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "answer:" + prompt},
                     "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}
                body = json.dumps(result).encode()
                try:
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def count(self, prompt):
        with self.lock: return sum(p == prompt for p, _ in self.calls)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


class Pilot:
    def __init__(self, directory, fixture):
        self.root = Path(directory); self.fixture = fixture
        for name in ("journal", "worker", "budget"): (self.root / name).mkdir(mode=0o700)
        self.socket = self.root / "worker.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.listener.bind(str(self.socket)); self.listener.listen(16)
        self.tool = self.root / "fixture-tool"
        self.tool.write_text("#!" + sys.executable + "\nimport sys,time\ntime.sleep(float(sys.argv[1]))\nprint('tool-finished', flush=True)\n")
        self.tool.chmod(0o700)
        self.lines = []; self.condition = threading.Condition()
        self.service = self.worker = None; self.readers = []
        self.started = time.monotonic(); self.start_service(); self.start_worker()

    def start_service(self):
        process = subprocess.Popen([SERVICE, str(self.root / "journal/store.sqlite3"),
            str(self.listener.fileno()), "activities", "8"], pass_fds=[self.listener.fileno()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.service = process
        def read():
            for line in process.stdout:
                with self.condition:
                    self.lines.append((time.monotonic(), line.rstrip())); self.condition.notify_all()
        reader = threading.Thread(target=read, daemon=True); reader.start(); self.readers.append(reader)
        start = time.monotonic()
        self.wait(lambda l: l == "ready", after=start - .1)

    def start_worker(self):
        env = dict(os.environ)
        # Fixture-only credentials and private configuration; never use an
        # inherited production ledger, key, URL, notification or lifecycle.
        for key in list(env):
            if key.startswith(("METTACLAW_", "SYNTHETIC_", "OPENAI_", "ANTHROPIC_", "CETTA_IO_AUTH_")):
                env.pop(key)
        env.update(SYNTHETIC_API_KEY="fixture-only", SYNTHETIC_BASE_URL=
            "http://127.0.0.1:%d/v1" % self.fixture.server.server_port,
            METTACLAW_LLM_POLICY="managed", METTACLAW_LLM_REQUESTS_PER_HOUR="100",
            METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY="100000", METTACLAW_LLM_STREAM="0")
        python = sys.executable
        if PREFIX:
            env["PYTHONHOME"] = PREFIX
            env["LD_LIBRARY_PATH"] = str(Path(PREFIX) / "lib")
            env["PATH"] = str(Path(PREFIX) / "bin") + os.pathsep + env.get("PATH", "")
            python = str(Path(PREFIX) / "bin/python3")
        self.worker = subprocess.Popen([python, str(ROOT / "src/activity_worker.py"),
            "--engine", ENGINE, "--socket", str(self.socket), "--state", str(self.root / "worker"),
            "--journal-dir", str(self.root / "journal"), "--budget", str(self.root / "budget/model.sqlite3"),
            "--model", "hf:fixture", "--parallel", "3", "--reserve-interactive", "1",
            "--max-tokens", "64", "--tool", "fixture=" + str(self.tool)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def wait(self, predicate, seconds=5, after=0):
        end = time.monotonic() + seconds
        with self.condition:
            while True:
                for when, line in self.lines:
                    if when >= after and predicate(line): return when, line
                if self.service.poll() is not None:
                    raise AssertionError("coordinator stopped: " + self.service.stderr.read())
                if self.worker and self.worker.poll() is not None:
                    raise AssertionError("worker stopped: " + self.worker.stderr.read())
                remaining = end - time.monotonic()
                if remaining <= 0: raise AssertionError("missing activity; seen " + repr(self.lines[-8:]))
                self.condition.wait(min(.1, remaining))

    def record(self, kind, identity, body, owner=None, due=0, period=0):
        start = time.monotonic()
        fields = [kind, identity, owner or identity, "1"]
        if kind == "timer": fields += [str(due), str(period)]
        self.service.stdin.write("\t".join(fields + [body]) + "\n"); self.service.stdin.flush()
        self.wait(lambda l: l == "intake " + identity + " ok", after=start)
        return start

    def model(self, identity, prompt, lane="background", owner=None):
        body = json.dumps(dict(kind="model", lane=lane, model="hf:fixture", tokens=32,
                               effort="low", prompt=prompt), separators=(",", ":"))
        return self.record("work", identity, body, owner)

    def result(self, identity, seconds=5, after=0):
        when, line = self.wait(lambda l: l.startswith("result " + identity + " "), seconds, after)
        match = RESULT.fullmatch(line)
        if not match: raise AssertionError("invalid result envelope: " + line)
        return when, match[2], json.loads(json.loads(match[3]))

    def until(self, condition, seconds=5):
        end = time.monotonic() + seconds
        while not condition():
            if self.worker and self.worker.poll() is not None:
                raise AssertionError("worker stopped: " + self.worker.stderr.read())
            if time.monotonic() >= end: raise AssertionError("condition not met")
            time.sleep(.01)

    def kill_service(self):
        self.service.kill(); self.service.wait(timeout=5)
        for reader in self.readers: reader.join(timeout=1)
        self.service.stdin.close(); self.service.stdout.close(); self.service.stderr.close(); self.service = None

    def kill_worker(self):
        self.worker.kill(); self.worker.wait(timeout=5); self.worker.stderr.close(); self.worker = None

    def close(self):
        if self.worker:
            self.worker.terminate()
            try: self.worker.wait(timeout=6)
            except subprocess.TimeoutExpired: self.worker.kill(); self.worker.wait(timeout=5)
            self.worker.stderr.close()
        if self.service:
            if self.service.poll() is None:
                self.service.stdin.write("quit\n"); self.service.stdin.flush()
            self.service.wait(timeout=5)
            for reader in self.readers: reader.join(timeout=1)
            stderr = self.service.stderr.read()
            self.service.stdin.close(); self.service.stdout.close(); self.service.stderr.close()
            if self.service.returncode != 0: raise AssertionError(stderr)
            for _, line in self.lines:
                if line.startswith("stats "): print("SERVICE_" + line.upper().split(" ", 1)[0] + " " + line[6:])
        self.listener.close()


@unittest.skipUnless(SERVICE and ENGINE, "configure native activity binaries")
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        fixture = Fixture(); self.addCleanup(fixture.close)
        self.p = Pilot(temporary.name, fixture); self.addCleanup(self.p.close)

    def test_delayed_model_and_tool_leave_input_interactive_call_and_periodic_work_responsive(self):
        p = self.p
        start = p.model("slow-model", "slow-model")
        p.record("work", "slow-tool", json.dumps(dict(kind="tool", lane="background", tool="fixture", arguments=[str(DELAY)])))
        p.until(lambda: p.fixture.count("slow-model") == 1)
        period = max(50, int(DELAY * 1000 / 3.2))
        due = int(time.time() * 1000) + period
        p.record("timer", "alphabet", "abcdefghijklmnopqrstuvwxyz", due=due, period=period)
        incoming = p.record("notice", "incoming", "operator arrived during model and tool")
        notice, _ = p.wait(lambda l: l.startswith("result incoming "))
        interactive = p.model("interactive", "hear-you", "interactive")
        short, authority, result = p.result("interactive", 8)
        self.assertEqual((authority, result["status"], result["value"]), ("current", "completed", "answer:hear-you"))
        ticks = [p.wait(lambda l, n=n: l.startswith(f"result alphabet/{n} "), DELAY + 8)[0] for n in range(3)]
        model, _, model_result = p.result("slow-model", DELAY + 8)
        tool, _, tool_result = p.result("slow-tool", DELAY + 8)
        self.assertEqual(model_result["value"], "answer:slow-model")
        self.assertEqual((tool_result["status"], tool_result["value"]), ("completed", "tool-finished\n"))
        self.assertLess(notice - incoming, 1)
        self.assertLess(short - interactive, 5)
        self.assertLess(short, min(model, tool)); self.assertLess(ticks[-1], min(model, tool))
        self.assertEqual([p.fixture.count(v) for v in ("slow-model", "hear-you")], [1, 1])
        self.assertNotEqual(model_result["call_id"], result["call_id"])
        print("REPLAY " + json.dumps(dict(notice_ms=round((notice-incoming)*1000, 3),
            interactive_ms=round((short-interactive)*1000, 3), model_s=round(model-start, 3),
            tool_s=round(tool-start, 3), alphabet_s=[round(t-start, 3) for t in ticks])))

    def test_coordinator_restart_does_not_replay_an_inflight_provider_call(self):
        p = self.p; p.model("slow-restart", "slow-restart")
        p.until(lambda: p.fixture.count("slow-restart") == 1)
        p.kill_service(); p.start_service()
        p.record("notice", "during-restart", "continued input")
        p.wait(lambda l: l.startswith("result during-restart "))
        _, _, result = p.result("slow-restart", DELAY + 8)
        self.assertEqual(result["value"], "answer:slow-restart")
        self.assertEqual(p.fixture.count("slow-restart"), 1)

    def test_worker_restart_retains_unknown_and_never_replays_started_provider_work(self):
        p = self.p; p.model("slow-kill", "slow-kill")
        p.until(lambda: p.fixture.count("slow-kill") == 1)
        p.kill_worker(); p.start_worker()
        _, _, result = p.result("slow-kill", 8)
        self.assertEqual((result["status"], result["reason"]), ("unknown", "worker-interrupted-after-start"))
        p.model("after-kill", "still-here", "interactive")
        _, _, result = p.result("after-kill", 8)
        self.assertEqual(result["value"], "answer:still-here")
        self.assertEqual(p.fixture.count("slow-kill"), 1)

    def test_revoked_real_model_response_is_retained_without_current_authority(self):
        p = self.p; p.model("slow-cancel", "slow-cancel", owner="owner")
        p.until(lambda: p.fixture.count("slow-cancel") == 1)
        p.record("cancel", "revoke", "stop awaiting", owner="owner")
        p.wait(lambda l: l.startswith("result revoke "))
        _, authority, result = p.result("slow-cancel", DELAY + 8)
        self.assertEqual((authority, result["status"], result["value"]), ("late", "completed", "answer:slow-cancel"))
        self.assertEqual(p.fixture.count("slow-cancel"), 1)

    def test_lost_transport_is_unknown_and_an_identical_prompt_is_not_reissued(self):
        p = self.p; p.model("lost-transport", "dropped-response")
        _, _, result = p.result("lost-transport", 8)
        self.assertEqual((result["status"], result["reason"]), ("unknown", "transport-error"))
        p.model("attempt-replay", "dropped-response")
        _, _, retry = p.result("attempt-replay", 8)
        self.assertEqual((retry["status"], retry["reason"]), ("failed", "provider-outcome-unknown"))
        self.assertEqual(p.fixture.count("dropped-response"), 1)


if __name__ == "__main__": unittest.main(verbosity=2)
