#!/usr/bin/env python3
"""Real service/responder notices and cleanup after durable input processing."""
import json
import contextlib
import http.server
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
CHANNEL_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT))
SERVICE = os.environ.get("CETTA_SERVICE_BIN", "")
CETTA = os.environ.get("CETTA_BIN", "")
sys.path[:0] = [str(ROOT / "src")]
TOKEN = "123456789:TEST_ONLY_abcdefghijklmnopqrstuvwxyz0123456789"
import durable_telegram
import input_ack
import runtime_context


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        status, _, body = self.server.receipt(None, "POST", self.path,
            self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(status); self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class API(http.server.ThreadingHTTPServer):
    def __init__(self, *args):
        self.updates, self.sent, self.deleted = [], [], []
        super().__init__(("127.0.0.1", 0), Handler)
    def receipt(self, connection, method, path, body):
        value = json.loads(body)
        command = path.rsplit("/", 1)[-1]
        if command == "getUpdates":
            return 200, [], json.dumps({"ok": True, "result": [u for u in self.updates if u["update_id"] >= value["offset"]]}).encode()
        if command == "deleteMessage":
            self.deleted.append((value["chat_id"], value["message_id"]))
            return 200, [], b'{"ok":true,"result":true}'
        if command == "sendMessage":
            self.sent.append(value)
            return 200, [], json.dumps({"ok": True, "result": {"message_id": 501, "chat": {"id": value["chat_id"]}}}).encode()
        return 200, [], b'{"ok":true,"result":true}'


@contextlib.contextmanager
def peer():
    api = API()
    threading.Thread(target=api.serve_forever, daemon=True).start()
    try:
        yield api, "http://127.0.0.1:%d" % api.server_port
    finally:
        api.shutdown(); api.server_close()


def wait(check, label):
    end = time.monotonic() + 20
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.05)
    raise AssertionError(label)


def main():
    with tempfile.TemporaryDirectory(prefix="owned-notice-") as temporary:
        folder = Path(temporary)
        token = folder / "token"; token.write_text(TOKEN); token.chmod(0o600)
        state = folder / "service"; state.mkdir(mode=0o700)
        path = folder / "channel.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(path)); listener.listen(16); path.chmod(0o600)
        environment = dict(os.environ, METTACLAW_TELEGRAM_CLIENT="metta",
            METTACLAW_TELEGRAM_SERVICE_SOCKET=str(path), METTACLAW_STRATEGY_PATH=str(folder / "strategy.db"),
            METTACLAW_OBSERVED_STRATEGY="1", METTACLAW_INPUT_ACK_SECONDS="0.05",
            METTACLAW_INSTANCE="fixture", METTACLAW_ENGINE_STATE_PATH=str(folder / "engine"),
            METTACLAW_TELEGRAM_LOG_PATH=str(folder / "updates.jsonl"),
            METTACLAW_TELEGRAM_HEALTH_PATH=str(folder / "health.json"),
            METTACLAW_TELEGRAM_OPERATOR_IDS="7", METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS="42",
            METTACLAW_TELEGRAM_PRIMARY_CHAT_ID="42", METTACLAW_LIFECYCLE_PATH=str(folder / "lifecycle"),
            METTACLAW_WAKE_REQUEST_PATH=str(folder / "wake"),
            PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")]))
        source = folder / "responder.metta"
        source.write_text('!(import! &self "' + str(ROOT / "channels/telegram_control.metta") + '")\n!(tc:serve)\n')
        with peer() as (api, origin):
            service = subprocess.Popen([SERVICE, "--run", "--root", str(CHANNEL_ROOT), "--state-dir", str(state),
                "--worker", "notice-fixture", "--chat", "42", "--operator", "7", "--program", "channel",
                "--credential-file", str(token), "--mock-origin", origin, "--listener-fd", str(listener.fileno())],
                pass_fds=(listener.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def responder():
                return subprocess.Popen([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", str(source)],
                    cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            worker = responder()
            try:
                api.updates.append({"update_id": 1, "message": {"message_id": 100, "date": 1790000000,
                    "chat": {"id": 42, "type": "private", "first_name": "fixture"},
                    "from": {"id": 7, "is_bot": False, "first_name": "fixture"}, "text": "ordinary question"}})
                wait(lambda: len(api.sent) == 1, "owned receipt notice was not delivered")
                assert api.sent[0]["text"] == input_ack.TEXT
                assert api.deleted == [], "notice deleted before successful processing"
                def inbox_count(count):
                    try:
                        value = json.loads((folder / "modes/control-status.json").read_text())
                        return value.get("operator_inbox", {}).get("pending") == count
                    except (OSError, ValueError, AttributeError):
                        return False
                wait(lambda: inbox_count(1), "complete service input scan did not reach health context")
                # A responder restart cannot send another notice under a new ID.
                worker.kill(); worker.wait(); worker = responder()
                time.sleep(.2)
                assert len(api.sent) == 1
                os.environ.update({key: value for key, value in environment.items() if key.startswith("METTACLAW_")})
                channel = durable_telegram.Channel(str(path), os.devnull)
                input_ack.processed_update_ids(["1"])
                wait(lambda: input_ack.pump(channel) or bool(api.deleted), "processed owned notice was not removed")
                assert api.deleted == [(42, 501)], api.deleted
                assert len(api.sent) == 1
                assert any(json.loads(body)[0] == "input" for _, body in channel.pending(32)), "notice cleanup consumed user input"
                wait(lambda: inbox_count(1), "responder restart lost the still-pending input observation")
                facts = runtime_context.read()
                assert facts["humanInbox"]["pending"] == 1
                assert facts["humanInbox"]["newestAgeSeconds"] > 0
                assert "ordinary question" not in runtime_context.render(facts)
                # The observer leaves the input in the service. Only the actual
                # processing acknowledgement removes its pending occurrence.
                pending = [(task, body) for task, body in channel.pending(32) if json.loads(body)[0] == "input"]
                assert len(pending) == 1
                channel.acknowledge(pending[0][0])
                wait(lambda: inbox_count(0), "processed service input remained in the context gauge")
                assert runtime_context.read()["humanInbox"]["pending"] == 0
                service.kill(); service.wait()
                wait(lambda: json.loads((folder / "modes/control-status.json").read_text()).get("channel_ready") is False,
                     "failed channel did not publish loss of readiness")
                failed = runtime_context.read()
                assert failed["localChannelReady"] is False and failed["humanInbox"] is None
                print("PASS: exact owned notice deleted after processing; responder restart did not resend; input remains independently owned")
                print("PASS: complete inbox scan reaches context, acknowledgement clears it, channel failure removes assurance")
            finally:
                worker.kill(); worker.wait(); service.kill(); service.wait(); listener.close()


if __name__ == "__main__":
    main()
