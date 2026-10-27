"""Iter's MeTTa host in a supervised agent: Telegram channel, acknowledgement,
send intents, routes and the operator gate, against the Python originals."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import durable_telegram  # noqa: E402
import engine_modes  # noqa: E402
import lifecycle  # noqa: E402
import mode_channel  # noqa: E402
import telegram  # noqa: E402

spec = importlib.util.spec_from_file_location("iter_native_launcher", ROOT / "modes/launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

CETTA = os.environ.get("MODE_TEST_CETTA_BIN", "")
_ZONE = os.environ.get("TZ")


def setUpModule():
    # Telegram send times read in the local zone; both sides use UTC here.
    os.environ["TZ"] = "UTC"
    time.tzset()


def tearDownModule():
    if _ZONE is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _ZONE
    time.tzset()


def update(update_id, chat, text=None, **fields):
    message = {"message_id": update_id + 100, "date": 1700000000 + update_id,
               "chat": chat, "from": {"id": 7, "first_name": "Op"}, **fields}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


PRIVATE = {"id": 42, "type": "private"}
GROUP = {"id": -84, "type": "supergroup", "title": "Lab </x> \"quoted\""}

SAMPLES = [
    update(11, GROUP, "verdict", message_thread_id=5, is_topic_message=True,
           reply_to_message={"message_id": 80, "from": {"id": 9, "first_name": "Other"},
                             "chat": {"id": -84, "title": "Lab"}, "text": "earlier\nline  two"}),
    update(12, {"id": 42, "type": "private", "username": "op"},
           "hello \"there\" \u00e9 \U0001F600\n\tsecond line"),
    {"update_id": 13, "edited_message": {"message_id": 4, "date": 1700000200, "edit_date": 1700000300,
                                         "chat": PRIVATE, "from": {"id": 42}, "caption": "a caption"}},
    update(14, PRIVATE, None, photo=[{"file_id": "x"}]),
    {"update_id": 15, "channel_post": {"message_id": 6, "date": 1700000400,
                                       "chat": {"id": -100123, "type": "channel", "title": "News"},
                                       "sender_chat": {"id": -100123, "title": "News"},
                                       "author_signature": "Ed", "text": "post"}},
    update(16, PRIVATE, "fw1", via_bot={"id": 5, "username": "gifbot"},
           forward_origin={"type": "user", "sender_user": {"id": 77, "first_name": "Fwd"}, "message_id": 99}),
    update(17, PRIVATE, "fw2", forward_origin={"type": "hidden_user", "sender_user_name": "Hidden One"}),
    update(18, PRIVATE, "fw3", forward_origin={"type": "channel", "chat": {"id": -1001, "title": "Chan"},
                                               "message_id": 3, "author_signature": "Sig"}),
    update(19, PRIVATE, "legacy", forward_from={"id": 55, "username": "legacy"}),
    update(20, PRIVATE, "q", forward_sender_name="Anon", quote={"text": "quoted part"},
           is_automatic_forward=True),
    update(21, {"id": -5, "type": "group", "title": "T" * 170}, "long title",
           reply_to_message={"message_id": 1, "from": {"id": 9}, "caption": "cap reply"}),
]


def python_text(value):
    """runtime_host.receive's text for one input update."""
    kind, message = telegram._extract_message(value)
    text = message.get("text") or message.get("caption") or ""
    if not text:
        text = "[attachment received; use the agent attachment tools to inspect it]"
    chat = message.get("chat", {})
    name = mode_channel.route_name(int(chat["id"]), int(message.get("message_thread_id", 0)))
    return "[reply_channel=" + name + "]\n" + telegram._format_message(value, kind, message, text)


class Service:
    """The durable channel service's CWP side: tasks, answers, submissions."""

    def __init__(self, path, tasks):
        self.path = path
        self.pending = dict(tasks)
        self.results = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.server.bind(str(path))
        self.server.listen()
        self.server.settimeout(.1)
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while self.running:
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            with connection:
                packet = connection.recv(70000)
                code, size = packet[4], packet[5]
                name, body = packet[6:6 + size].decode(), packet[6 + size:].decode()
                reply = (durable_telegram.IDLE, "", "")
                if code == durable_telegram.NEXT:
                    later = sorted(task for task in self.pending if not name or task > name)
                    if later:
                        reply = (durable_telegram.TASK, later[0], self.pending[later[0]])
                elif code == durable_telegram.RESULT:
                    known = name in self.pending
                    self.results.append((name, body))
                    self.pending.pop(name, None)
                    reply = (durable_telegram.STORED if known else durable_telegram.UNKNOWN, "", "")
                connection.sendall(b"CWP1" + bytes([reply[0], len(reply[1])]) + reply[1].encode() + reply[2].encode())

    def acknowledged(self):
        return [name for name, body in self.results if body == "[]"]

    def close(self):
        self.running = False
        self.thread.join(1)
        self.server.close()


@unittest.skipUnless(CETTA, "set MODE_TEST_CETTA_BIN")
class IterHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "modes"
        self.runtime = self.state / "iter"
        self.socket = self.base / "channel.sock"
        (self.base / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
        (self.base / "engine").write_text("cetta\n")
        (self.base / "loop_mode.json").write_text('{"mode": "iter"}')
        self.environment = {
            "METTACLAW_MODE_STATE_ROOT": str(self.state),
            "METTACLAW_MODE_RUNTIME": str(self.runtime),
            "METTACLAW_ENGINE_STATE_PATH": str(self.base / "engine"),
            "METTACLAW_LIFECYCLE_PATH": str(self.base / "lifecycle.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.base / "recycle"),
            "METTACLAW_LOOP_MODE_PATH": str(self.base / "loop_mode.json"),
            "METTACLAW_ACTIVE_LOOP_MODE": "iter",
            "METTACLAW_ACTIVE_ENGINE": "cetta",
            "METTACLAW_ENGINE": "cetta",
            "METTACLAW_TELEGRAM_SERVICE_SOCKET": str(self.socket),
            "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42",
            "TZ": "UTC",
        }
        with mock.patch.dict(os.environ, self.environment):
            launcher.prepare_iter(self.runtime, ROOT, standalone=False)

    def service(self, tasks):
        service = Service(self.socket, tasks)
        self.addCleanup(service.close)
        return service

    def metta(self, *forms, environment=None):
        """Evaluate forms in the loop's directory after utils.metta, as a
        worker does; each value comes back as one JSON line."""
        driver = self.runtime / "driver.metta"
        output = self.runtime / "driver.jsonl"
        output.unlink(missing_ok=True)
        driver.write_text("!(import! &self utils)\n"
                          '(= (driver:out $value) (fs:append-text "driver.jsonl" (str:concat (json-text $value) "\\n")))\n' +
                          "".join("!(driver:out %s)\n" % form for form in forms))
        env = isolated_environ(**{**self.environment, **(environment or {})})
        result = subprocess.run([CETTA, "--lang", "petta", "--no-results", str(driver)], cwd=self.runtime,
                                env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        lines = output.read_text().splitlines() if output.exists() else []
        self.assertEqual(len(lines), len(forms), result.stdout[-3000:] + result.stderr[-3000:])
        return [json.loads(line) for line in lines]

    def save(self, *texts):
        history = [{"role": "user", "content": "Step 2026-01-01 00:00:00: [telegram] " + text} for text in texts]
        (self.runtime / "experience.json").write_text(json.dumps(history))

    def test_rendering_matches_the_python_client(self):
        (self.runtime / "updates.json").write_text(json.dumps(SAMPLES))
        [texts] = self.metta('(map-atom (json-parse (read-text "updates.json")) $u '
                             '(host:item-text (host:input-item "t" $u (json-text $u))))')
        self.assertEqual(texts, [python_text(value) for value in SAMPLES])

    def test_receive_offers_until_the_saved_history_holds_the_input(self):
        private, group = update(31, PRIVATE, "first"), update(32, GROUP, "second", message_thread_id=5)
        receipt = json.dumps(["delivery", "42.0.00000000000000000001", 0, 1, ["delivered", 5]])
        service = self.service({
            "t1": json.dumps(["input", "42.0", "message", "operator", private]),
            "t2": json.dumps(["command", "42.0", "/mode", "omega", "operator"]),
            "t3": json.dumps(["command", "42.0", "/delete", "", "operator"]),
            "t4": receipt,
            "t5": json.dumps(["input", "-84.5", "message", "bot", group]),
            "t6": json.dumps(["callback", "42.0", "mode:iter", "operator", {}]),
        })
        handover = update(19, PRIVATE, "pending during handover")
        mode_channel.store(self.state / "channel-handover/19.json", handover)

        first, again, before = self.metta("(host:telegram-receive)", "(host:telegram-receive)", "(host:telegram-commit)")
        self.assertEqual(first, again)
        parts = first.split("\n[reply_channel=")
        self.assertIn("pending during handover", parts[0])
        self.assertIn(python_text(private), first)
        self.assertIn(python_text(group), first)
        self.assertIn("[channel receipt; this is delivery status, not a user message]\n" + receipt, first)
        self.assertNotIn("/mode", first)
        self.assertNotIn("mode:iter", first)
        self.assertEqual(before, 0)
        # Offering acknowledges nothing; /delete is answered for the loop.
        self.assertEqual(service.acknowledged(), [])
        self.assertEqual([name for name, body in service.results], ["t3"])
        self.assertIn("unavailable in iter mode", service.results[0][1])
        self.assertEqual(sorted(service.pending), ["t1", "t2", "t4", "t5", "t6"])
        # Routes keep mode_channel's record and spelling.
        route = self.state / "channel-routes/telegram_m84_5.json"
        self.assertEqual(route.read_text(), json.dumps({"chat": "-84", "thread": 5, "type": "supergroup",
                                                       "title": GROUP["title"]}, ensure_ascii=False))
        with mock.patch.dict(os.environ, self.environment):
            self.assertEqual(mode_channel.resolve("telegram_m84_5"), {"chat": "-84", "thread": 5, "type": "supergroup",
                                                                      "title": GROUP["title"]})
        logged = [json.loads(line) for line in (self.runtime / "channel-receipts.jsonl").read_text().splitlines()]
        self.assertEqual(logged, [{"task": "t4", "receipt": json.loads(receipt)}] * 2)

        # A save that holds one input acknowledges that input only.
        self.save(python_text(private))
        acknowledged, offered = self.metta("(host:telegram-commit)", "(host:telegram-receive)")
        self.assertEqual(acknowledged, 1)
        self.assertEqual(service.acknowledged(), ["t1"])
        self.assertNotIn(python_text(private), offered)
        self.assertIn(python_text(group), offered)

        # Everything saved: all acknowledged, the handover file retired.
        self.save(first)
        acknowledged, offered = self.metta("(host:telegram-commit)", "(host:telegram-receive)")
        self.assertEqual(acknowledged, 3)
        self.assertEqual(sorted(service.acknowledged()), ["t1", "t4", "t5"])
        self.assertEqual(offered, "")
        self.assertTrue((self.state / "channel-handover/19.done").exists())
        self.assertFalse((self.state / "channel-handover/19.json").exists())
        records = [json.loads(line) for line in (self.runtime / "runtime.jsonl").read_text().splitlines()]
        self.assertEqual([r["count"] for r in records if r["kind"] == "input-acknowledged"], [1, 3])

    def test_a_stopped_or_recycling_loop_takes_no_input(self):
        service = self.service({"t1": json.dumps(["input", "42.0", "message", "operator", update(41, PRIVATE, "wait")])})
        (self.base / "lifecycle.json").write_text('{"schema":1,"state":"stopped"}')
        [stopped] = self.metta("(host:telegram-receive)")
        (self.base / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
        (self.base / "recycle").touch()
        [recycling] = self.metta("(host:telegram-receive)")
        (self.base / "recycle").unlink()
        [running] = self.metta("(host:telegram-receive)")
        self.assertEqual((stopped, recycling), ("", ""))
        self.assertIn("wait", running)
        self.assertEqual(service.results, [])

    def test_sends_are_the_python_queue_intents(self):
        long_text = "\n".join(("line %d " % i) * 30 for i in range(120))
        self.service({"t1": json.dumps(["input", "-84.5", "message", "bot", update(51, GROUP, "x", message_thread_id=5)])})
        (self.runtime / "long.txt").write_text(long_text)
        with mock.patch.dict(os.environ, self.environment):
            outbox = mode_channel.state() / "channel-outbox"
            first, offered, long_key, group_key, unknown, traversal, null_lane = self.metta(
                '(host:telegram-send "telegram" "private status")',
                "(host:telegram-receive)",
                '(host:telegram-send "telegram" (read-text "long.txt"))',
                '(host:telegram-send "telegram_m84_5" "sibling reply")',
                '(repr (host:telegram-send "telegram_m84_6" "x"))',
                '(repr (host:send-tool "../operator" "x"))',
                '(repr (host:telegram-send "telegram_0_0" "x"))')
            python_key = mode_channel.enqueue("python status").split(": ")[1]
            [last] = self.metta('(host:telegram-send "telegram" "after python")')
            intents = {path.stem: json.loads(path.read_text()) for path in outbox.glob("*.json")}
            context = mode_channel.context()
        keys = [k.split(": ")[1] for k in (first, long_key, group_key)]
        last_key = last.split(": ")[1]
        self.assertTrue(first.startswith("durably queued: 42.0."))
        self.assertEqual(intents[keys[0]], {"key": keys[0], "commands": [["send", "private status", "plain"]]})
        self.assertEqual([c[1] for c in intents[keys[1]]["commands"]], durable_telegram.chunks(long_text))
        self.assertEqual(durable_telegram.lane_of(keys[2]), "-84.5")
        self.assertLess(keys[0], keys[1])
        self.assertLess(int(keys[1].rsplit(".", 1)[1]), int(python_key.rsplit(".", 1)[1]))
        self.assertLess(int(python_key.rsplit(".", 1)[1]), int(last_key.rsplit(".", 1)[1]))
        for refused in (unknown, null_lane):
            self.assertIn("Error", refused)
        self.assertEqual(traversal, '"Unknown channel: ../operator"')
        self.assertEqual(len(intents), 5)
        [route_context] = self.metta("(host:route-context)")
        self.assertEqual(route_context, context)

    def test_default_state_paths_are_the_python_ones(self):
        import loop_modes
        defaults = {"XDG_STATE_HOME": str(self.base / "xdg"), "METTACLAW_INSTANCE": "fixture-instance",
                    "METTACLAW_LIFECYCLE_PATH": "", "METTACLAW_ENGINE_STATE_PATH": ""}
        paths = self.metta("(host:lifecycle-path)", "(host:engine-path)", "(host:active-loop-path)", environment=defaults)
        with mock.patch.dict(os.environ, {**self.environment, **defaults}):
            os.environ.pop("METTACLAW_LIFECYCLE_PATH")
            os.environ.pop("METTACLAW_ENGINE_STATE_PATH")
            self.assertEqual(paths, [str(lifecycle._path()), engine_modes._state_path(), loop_modes._active_path()])

    def test_the_gate_reads_the_latch_and_recycle_witnesses_as_python_does(self):
        cases = [
            ("lifecycle.json", '{"schema":1,"state":"running"}'),
            ("lifecycle.json", '{"schema":1,"state":"stopped"}'),
            ("lifecycle.json", '{"schema":1,"state":"running","extra":0}'),
            ("lifecycle.json", '{"schema":1.0,"state":"running"}'),
            ("lifecycle.json", 'not json'),
            ("lifecycle.json", None),
            ("engine", "petta\n"),
            ("engine", " SWI \n"),
            ("engine", "pleatta\n"),
            ("engine", "cetta\n"),
            ("loop_mode.json", '{"mode": "omega"}'),
            ("loop_mode.json", '{"mode": " Iter "}'),
            ("loop_mode.json", '{"mode": "agent"}'),
            ("loop_mode.json", '[1]'),
            ("loop_mode.json", '{"mode": "iter"}'),
            ("recycle", ""),
        ]
        forms, expected = [], []
        for name, content in cases:
            path = self.base / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content)
            with mock.patch.dict(os.environ, self.environment):
                expected.append([bool(lifecycle.cognition_enabled()), engine_modes.recycle_requested()])
            [observed] = self.metta("(cons-atom (host:cognition-enabled?) (cons-atom (host:recycle-requested?) ()))")
            forms.append(observed)
            if name == "recycle":
                path.unlink()
            if name == "lifecycle.json":
                (self.base / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
        self.assertEqual(forms, expected)


class Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        calls = [{"id": "nop-%d" % len(self.server.requests), "type": "function",
                  "function": {"name": "nop", "arguments": "{}"}}]
        if len(self.server.requests) == 1:
            calls.insert(0, {"id": "send-1", "type": "function", "function": {
                "name": "send", "arguments": json.dumps({"channel": "telegram", "content": "fixture reply"})}})
        reply = json.dumps({"choices": [{"message": {"role": "assistant", "tool_calls": calls},
                                         "finish_reason": "tool_calls"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


@unittest.skipUnless(CETTA, "set MODE_TEST_CETTA_BIN")
class SupervisedLoopTests(unittest.TestCase):
    def test_input_send_stop_resume_and_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
            (base / "engine").write_text("cetta\n")
            (base / "loop_mode.json").write_text('{"mode": "iter"}')
            service = Service(base / "channel.sock", {
                "t1": json.dumps(["input", "42.0", "message", "operator", update(61, PRIVATE, "please answer")])})
            self.addCleanup(service.close)
            provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            provider.requests = []
            threading.Thread(target=provider.serve_forever, daemon=True).start()
            self.addCleanup(provider.server_close)
            self.addCleanup(provider.shutdown)
            environment = isolated_environ(
                PYTHONPATH=str(ROOT / "src"), PYTHONHOME=sys.prefix, LD_LIBRARY_PATH=str(Path(sys.prefix) / "lib"),
                PYTHONDONTWRITEBYTECODE="1", METTACLAW_MODE_CETTA_BIN=CETTA,
                METTACLAW_MODE_STATE_ROOT=str(base / "modes"), METTACLAW_ENGINE_STATE_PATH=str(base / "engine"),
                METTACLAW_LIFECYCLE_PATH=str(base / "lifecycle.json"), METTACLAW_LOOP_MODE_PATH=str(base / "loop_mode.json"),
                METTACLAW_RECYCLE_REQUEST_PATH=str(base / "recycle"), METTACLAW_COGNITIVE_HEALTH_PATH=str(base / "health.json"),
                METTACLAW_MODEL_STATE_PATH=str(base / "model.metta"), METTACLAW_LLM_LEDGER_PATH=str(base / "ledger.db"),
                METTACLAW_LLM_POLICY="upstream", METTACLAW_TELEGRAM_SERVICE_SOCKET=str(base / "channel.sock"),
                METTACLAW_TELEGRAM_PRIMARY_CHAT_ID="42", SYNTHETIC_MODEL="fixture-model",
                SYNTHETIC_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1", SYNTHETIC_API_KEY="fixture-only",
                AI_API_KEY="must-not-reach-the-loop")
            log = (base / "loop.log").open("w")
            self.addCleanup(log.close)
            process = subprocess.Popen([sys.executable, str(ROOT / "modes/launch.py"), "--repo", str(ROOT), "--mode", "iter",
                                        "--engine", "cetta", "--profile", "supervised"],
                                       env=environment, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            def stop():
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
            self.addCleanup(stop)
            def until(condition, seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), (base / "loop.log").read_text()[-3000:])
                    if condition():
                        return
                    time.sleep(.2)
                self.fail((base / "loop.log").read_text()[-3000:])
            outbox = base / "modes/channel-outbox"
            until(lambda: len(provider.requests) >= 2 and list(outbox.glob("*.json")), 60)
            first = provider.requests[0]
            self.assertTrue(first["messages"][0]["content"].startswith("CHANNEL ROUTES"))
            self.assertIn("please answer", json.dumps(first["messages"]))
            self.assertEqual(service.acknowledged(), ["t1"])
            [intent] = [json.loads(path.read_text()) for path in outbox.glob("*.json")]
            self.assertEqual(intent["commands"], [["send", "fixture reply", "plain"]])
            self.assertTrue(intent["key"].startswith("42.0."))

            (base / "lifecycle.json").write_text('{"schema":1,"state":"stopped"}')
            service.pending["t2"] = json.dumps(["input", "42.0", "message", "operator", update(62, PRIVATE, "while stopped")])
            time.sleep(15)
            stopped = len(provider.requests)
            time.sleep(12)
            self.assertEqual(len(provider.requests), stopped)
            self.assertIn("t2", service.pending)

            (base / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
            until(lambda: any("while stopped" in json.dumps(r["messages"]) for r in provider.requests), 30)
            self.assertIn("t2", service.acknowledged())

            (base / "lifecycle.json").write_text('{"schema":1,"state":"stopped"}')
            time.sleep(14)
            (base / "loop_mode.json").write_text('{"mode": "godelclaw"}')
            (base / "recycle").touch()
            self.assertEqual(process.wait(timeout=30), 0)
            records = [json.loads(line) for line in (base / "modes/iter/runtime.jsonl").read_text().splitlines()]
            self.assertEqual(records[-1]["kind"], "recycle")


class IterLaunchTests(unittest.TestCase):
    def test_vendored_files_match_the_manifest(self):
        import hashlib
        manifest = json.loads((ROOT / "modes/iter/upstream-manifest.json").read_text())
        self.assertEqual(manifest["repository"], "https://github.com/godelclaw/iter")
        vendored = [item for item in manifest["files"] if item["vendored"]]
        for item in vendored:
            data = (ROOT / "modes/iter/upstream" / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"], item["path"])
        on_disk = {str(path.relative_to(ROOT / "modes/iter/upstream"))
                   for path in (ROOT / "modes/iter/upstream").rglob("*") if path.is_file()}
        self.assertEqual(on_disk, {item["path"] for item in vendored})

    def test_the_loop_and_its_modules_reach_no_python(self):
        # Only the provider process loads the native provider's Python host.
        modules = sorted((ROOT / "modes/iter/upstream").rglob("*.metta")) + sorted(
            path for path in (ROOT / "modes/iter/supervised").glob("*.metta") if path.name != "provider.metta")
        for path in modules:
            text = path.read_text()
            self.assertNotIn("py-call", text, path)
            self.assertNotRegex(text, r"import! &self [^)]*\.py\b", path)
        self.assertIn("native_llm.metta", (ROOT / "modes/iter/supervised/provider.metta").read_text())

    def test_supervised_code_is_replaced_and_agent_files_are_kept(self):
        vendor, host = ROOT / "modes/iter/upstream", ROOT / "modes/iter/supervised"
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_SERVICE_SOCKET": "/absent.sock"}):
            agent, runtime = Path(directory) / "agent", Path(directory) / "iter"
            (agent / "memory").mkdir(parents=True)
            (agent / "memory/soul.md").write_text("Fixture identity")
            entry, paths = launcher.prepare_iter(runtime, agent, standalone=False)
            self.assertEqual(entry, runtime / "iter.metta")
            for name in ("iter.metta", "utils.metta", "iter_worker.metta", "lib_nal.metta", "tools/shell.metta"):
                self.assertEqual((runtime / name).read_bytes(), (vendor / name).read_bytes(), name)
            for name, source in (("iter_llm.metta", "iter_llm.metta"), ("channels/telegram.metta", "telegram.metta"),
                                 ("tools/send.metta", "send.metta"), ("godelclaw_host.metta", "godelclaw_host.metta")):
                self.assertEqual((runtime / name).read_bytes(), (host / source).read_bytes(), name)
            self.assertFalse((runtime / "channels/terminal.metta").exists())
            prompt = (runtime / "prompt.txt").read_text()
            self.assertIn("Fixture identity", prompt)
            self.assertIn("Telegram channel is named telegram", prompt)
            ledger = json.loads((runtime / "adapter-ledger.json").read_text())
            self.assertEqual(ledger["profile"], "supervised")
            self.assertEqual(ledger["upstream"], "a58e2ebbba8db237e4126e13bdfe9f7cb90a3aaa")
            for name in ("tools/send.metta", "prompt.txt", "iter.metta"):
                (runtime / name).write_text("changed by the agent")
            launcher.prepare_iter(runtime, agent, standalone=False)
            self.assertEqual((runtime / "tools/send.metta").read_text(), "changed by the agent")
            self.assertEqual((runtime / "prompt.txt").read_text(), "changed by the agent")
            self.assertEqual((runtime / "iter.metta").read_bytes(), (vendor / "iter.metta").read_bytes())

    def test_supervised_loop_gets_no_standalone_key_and_runs_iter_metta(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = base / "bundle"
            (bundle / "lib/petta").mkdir(parents=True)
            (bundle / "lib/petta/lib_import.metta").write_text("")
            (bundle / "cetta").write_text("")
            seen = {}
            def run_child(command, cwd, environment):
                seen.update(command=command, environment=environment)
                return 0
            environment = {"METTACLAW_TELEGRAM_SERVICE_SOCKET": str(base / "channel.sock"),
                           "METTACLAW_MODE_STATE_ROOT": str(base / "modes"),
                           "METTACLAW_MODE_CETTA_BIN": str(bundle / "cetta"),
                           "AI_API_KEY": "standalone-key", "BASE_URL": "http://standalone.invalid",
                           "LLM_MODEL": "standalone-model"}
            with mock.patch.dict(os.environ, environment), mock.patch.object(launcher, "run_child", run_child), \
                 mock.patch.object(sys, "argv", ["launch.py", "--repo", str(ROOT), "--mode", "iter", "--profile", "supervised"]):
                self.assertEqual(launcher.main(), 0)
        self.assertEqual(Path(seen["command"][-1]).name, "iter.metta")
        for name in ("AI_API_KEY", "BASE_URL", "LLM_MODEL"):
            self.assertNotIn(name, seen["environment"])
        self.assertEqual(seen["environment"]["ITER_PROVIDER_ENTRY"], str(ROOT / "modes/iter/supervised/provider.metta"))


@unittest.skipUnless(CETTA, "set MODE_TEST_CETTA_BIN")
class VendoredWorkerTests(unittest.TestCase):
    def test_the_forks_worker_tests_pass(self):
        result = subprocess.run([CETTA, "--lang", "petta", "tests/worker.metta"], cwd=ROOT / "modes/iter/upstream",
                                env=isolated_environ(), capture_output=True, text=True, timeout=300,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.count("\u2705"), 20, result.stdout[-3000:])
        self.assertNotIn("\u274c", result.stdout)


if __name__ == "__main__":
    unittest.main()
