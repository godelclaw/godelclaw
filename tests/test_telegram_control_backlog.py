"""Real native channel + MeTTa controls survive an unconsumed durable backlog.

Set CETTA_BIN, CETTA_SERVICE_BIN and CETTA_CHANNEL_ROOT. All traffic, provider
state and settings belong to this fixture; no production credentials are used.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(all(os.environ.get(k) for k in
    ("CETTA_BIN", "CETTA_SERVICE_BIN", "CETTA_CHANNEL_ROOT", "CETTA_WORKER_FIXTURE")), "native integration binaries required")
class ControlBacklog(unittest.TestCase):
    def test_controls_and_restart_preserve_all_pending_input(self):
        channel_root = Path(os.environ["CETTA_CHANNEL_ROOT"])
        sys.path.insert(0, str(channel_root / "tests"))
        from test_telegram_transport import Peer, TOKEN, peer
        import test_command_dispatch_engine as fixture

        class API(Peer):
            def __init__(self, *args):
                self.updates, self.calls, self.serial, self.menu_id = [], [], 10000, 0
                super().__init__(*args)

            def add(self, update, text):
                with self.lock:
                    self.updates.append({"update_id": update, "message": {
                        "message_id": update, "date": 1790000000,
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": 7}, "text": text}})

            def tap(self, update, data):
                with self.lock:
                    self.updates.append({"update_id": update, "callback_query": {
                        "id": "q%d" % update, "from": {"id": 7}, "data": data,
                        "message": {"message_id": self.menu_id, "chat": {"id": 42, "type": "private"}}}})

            def receipt(self, connection, method, path, body):
                data = json.loads(body)
                name = path.rsplit("/", 1)[-1]
                with self.lock:
                    if name == "getUpdates":
                        result = [u for u in self.updates if u["update_id"] >= data["offset"]][:data.get("limit", 100)]
                    elif name == "getMe":
                        result = {"id": int(TOKEN.split(":")[0]), "is_bot": True, "username": "ControlFixtureBot"}
                    else:
                        self.serial += 1
                        self.calls.append((name, data))
                        result = True if name == "answerCallbackQuery" else {"message_id": data.get("message_id", self.serial), "chat": {"id": 42}}
                        if "reply_markup" in data: self.menu_id = result["message_id"]
                return 200, [], json.dumps({"ok": True, "result": result}).encode()

            def menu_count(self):
                with self.lock:
                    return sum("reply_markup" in data for _, data in self.calls)

        def pending(db):
            if not db.exists():
                return {}
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                return dict(conn.execute("SELECT key,value FROM records WHERE space='host.worker-tasks'"))

        with tempfile.TemporaryDirectory(prefix="control-backlog-") as directory:
            temp = Path(directory)
            env = fixture.CommandDispatchEngineTest()._cetta_env()
            env.update(METTACLAW_TELEGRAM_CLIENT="metta", METTACLAW_TELEGRAM_OPERATOR_IDS="7",
                METTACLAW_TELEGRAM_PRIMARY_CHAT_ID="42", METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS="42",
                METTACLAW_TELEGRAM_SERVICE_SOCKET=str(temp / "client.sock"),
                METTACLAW_ENGINE_STATE_PATH=str(temp / "engine"),
                METTACLAW_MODEL_STATE_PATH=str(temp / "model.json"),
                METTACLAW_STRATEGY_PATH=str(temp / "strategy.db"),
                METTACLAW_COGNITIVE_HEALTH_PATH=str(temp / "cognitive.json"),
                METTACLAW_TELEGRAM_HEALTH_PATH=str(temp / "health.json"),
                METTACLAW_TELEGRAM_LOG_PATH=str(temp / "events.jsonl"),
                METTACLAW_TELEGRAM_SERVICE_STATE_DIR=str(temp / "state"),
                METTACLAW_LIFECYCLE_PATH=str(temp / "lifecycle.json"),
                XDG_STATE_HOME=str(temp / "xdg"))
            (temp / "cognitive.json").write_text(json.dumps({
                "last_outcome": "failed", "failure_episode": {
                    "id": "fixture-outage", "first_at": 100, "count": 10,
                    "provider": "fixture", "model": "offline",
                    "kind": "provider-rejected-429"}}))
            fakes = temp / "fakes"; fakes.mkdir()
            (fakes / "synthetic_llm.py").write_text('''
def chat(*args, **kwargs):
    raise AssertionError("control called cognition")
def quota():
    raise RuntimeError("provider credits exhausted")
def current_model(): return "offline"
def model_ids(): return ["provider/" + "long-model-name-" * 8 + str(n) for n in range(500)]
def model_catalog_status(): return "fixture provider unavailable"
def model_in_observed_catalog(name): return None
''')
            env["PYTHONPATH"] = str(fakes) + os.pathsep + env["PYTHONPATH"]
            (temp / "state").mkdir(mode=0o700)
            token = temp / "token"; token.write_text(TOKEN); token.chmod(0o600)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(temp / "client.sock")); listener.listen(16)
            (temp / "client.sock").chmod(0o600)
            processes = []; stopped = set()
            source = temp / "control.metta"
            source.write_text('!(import! &self ' + json.dumps(str(ROOT / "channels/telegram_control.metta")) + ')\n!(tc:serve)\n')
            (temp / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
            with peer("http/1.1", None, None, API) as (api, origin):
                argv = [os.environ["CETTA_SERVICE_BIN"], "--run", "--root", str(channel_root),
                    "--state-dir", str(temp / "state"), "--worker", "lila", "--chat", "42",
                    "--operator", "7", "--program", "channel", "--credential-file", str(token),
                    "--mock-origin", origin, "--listener-fd", str(listener.fileno()),
                    "--commands", str(ROOT / "channels/telegram_commands.metta")]

                def start_service():
                    proc = subprocess.Popen(argv, pass_fds=(listener.fileno(),), env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    processes.append(proc)
                    return proc

                def wait(predicate, label, timeout=30):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        for proc in processes:
                            if proc.poll() is not None and proc not in stopped:
                                self.fail((label, proc.returncode, proc.communicate()))
                        if predicate(): return
                        time.sleep(.025)
                    self.fail(label)

                def ready():
                    try:
                        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                            client.settimeout(.5); client.connect(str(temp / "client.sock"))
                            client.sendall(b"CWP1" + bytes([6, 0]))
                            reply = client.recv(64)
                            return reply[:5] == b"CWP1" + bytes([64])
                    except (OSError, TimeoutError):
                        return False

                service = start_service()
                db = temp / "state/journal.db"
                try:
                    wait(ready, "service initialized")
                    # Seed through the same durable owner; neither SQL insertion
                    # nor consuming conversational messages substitutes for admission.
                    stopped.add(service); service.terminate(); service.communicate(timeout=10)
                    subprocess.run([os.environ["CETTA_WORKER_FIXTURE"], str(db), "lila", "1024"],
                        env=env, check=True, timeout=30)
                    before = pending(db)
                    self.assertEqual(len(before), 1024)
                    service = start_service()
                    wait(ready, "service reopened")
                    self.assertTrue(before.items() <= pending(db).items())
                    stopped.add(service); service.send_signal(signal.SIGKILL); service.communicate(timeout=10)
                    service = start_service()
                    wait(ready, "service reopened after crash")
                    self.assertTrue(before.items() <= pending(db).items())
                    responder = subprocess.Popen([env["CETTA_BIN"], "--lang", "petta", "--import-mode",
                        "ancestor-walk", str(source)], cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    processes.append(responder)
                    api.add(1, "/models")
                    wait(lambda: api.menu_count() >= 1, "model menu despite provider outage and backlog", 10)
                    wait(lambda: any("HTTP 429" in d.get("text", "") for _, d in api.calls),
                         "actionable outage notice without a model call", 10)
                    api.add(2, "/quota")
                    wait(lambda: any("provider credits exhausted" in d.get("text", "") for _, d in api.calls), "explicit provider failure", 10)
                    api.add(3, "/models")
                    wait(lambda: api.menu_count() >= 2, "later control survives failure", 10)
                    count = api.menu_count()
                    api.tap(4, "menu:model:24")
                    wait(lambda: api.menu_count() > count, "next catalogue page through the real callback path", 10)
                    # Restart only the responder: its durable notice identity
                    # must survive independently of in-memory episode caches.
                    stopped.add(responder); responder.terminate(); responder.communicate(timeout=10)
                    count = api.menu_count()
                    responder = subprocess.Popen([env["CETTA_BIN"], "--lang", "petta", "--import-mode",
                        "ancestor-walk", str(source)], cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    processes.append(responder)
                    api.add(5, "/models")
                    wait(lambda: api.menu_count() > count, "controls after responder restart", 10)
                    self.assertEqual(sum("HTTP 429" in d.get("text", "") for _, d in api.calls), 1)
                    self.assertTrue(before.items() <= pending(db).items())
                    self.assertFalse((temp / "model.json").exists(), "reading menus changed selected model")
                finally:
                    for proc in processes:
                        if proc.poll() is None: proc.terminate()
                    for proc in processes:
                        try: proc.communicate(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill(); proc.communicate(timeout=10)
                    listener.close()


if __name__ == "__main__":
    unittest.main()
