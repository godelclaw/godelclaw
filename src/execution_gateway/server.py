"""One independently launched gateway per agent, with isolated tool children."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socketserver
import stat
import subprocess
import tempfile
import threading
import time

from tool_output import ProcessOutput, StorageExhausted

from .client import MAX_FRAME
from .store import Conflict, Store

NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


class Gateway:
    def __init__(self, config):
        self.agent = config["agent"]
        if not isinstance(self.agent, str) or not NAME.fullmatch(self.agent):
            raise ValueError("invalid agent name")
        self.root = Path(config["state_directory"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = open(self.root / "worker.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.tools = config["tools"]
        for name, tool in self.tools.items():
            if not NAME.fullmatch(name) or tool["effect"] not in ("read", "write"):
                raise ValueError("invalid configured tool")
            argv = tool["command"]
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
                raise ValueError("tool command must be an argv list")
            if tool.get("result_format", "text") not in ("text", "json"):
                raise ValueError("invalid tool result format")
        self.store = Store(self.root / "queue.sqlite3", self.agent)
        self.store.recover()
        self.socket_path = self.root / "gateway.sock"
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise ValueError("gateway endpoint is not a socket")
            self.socket_path.unlink()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.failure = None

    def dispatch(self, message):
        if not isinstance(message, dict):
            raise ValueError("gateway envelope must be an object")
        if message.get("version") != 1 or message.get("agent") != self.agent:
            raise ValueError("wrong protocol version or agent")
        operation = message["operation"]
        if operation == "tools":
            return {name: {key: tool[key] for key in
                    ("effect", "description", "input_schema") if key in tool}
                    for name, tool in self.tools.items()}
        if operation in ("status", "cancel"):
            request_id = message["id"]
            if not isinstance(request_id, str) or not NAME.fullmatch(request_id):
                raise ValueError("invalid request ID")
            return (self.store.get(request_id) if operation == "status"
                    else self.store.cancel(request_id))
        if operation != "submit":
            raise ValueError("unknown gateway operation")
        request = message["request"]
        if set(request) != {"id", "tool", "arguments", "input", "deadline"}:
            raise ValueError("invalid request fields")
        if not isinstance(request["id"], str) or not NAME.fullmatch(request["id"]):
            raise ValueError("invalid request ID")
        arguments = request["arguments"]
        if not isinstance(arguments, list) or not all(isinstance(a, str) and "\0" not in a for a in arguments):
            raise ValueError("arguments must be strings without NUL bytes")
        deadline = request["deadline"]
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("invalid deadline")
        tool = self.tools[request["tool"]]
        # Effect classification and credential binding come from trusted
        # configuration, never from fields in a model-authored request.
        intent = {k: request[k] for k in ("id", "tool", "arguments", "input")}
        fingerprint = digest({"intent": intent, "tool": tool})
        receipt = self.store.submit(request, fingerprint, tool["effect"])
        self.ready.set()
        return receipt

    @staticmethod
    def terminate(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            process.wait()

    def execute(self, job):
        request = job["request"]
        tool = self.tools.get(request["tool"])
        intent = {k: request[k] for k in ("id", "tool", "arguments", "input")}
        if tool is None or digest({"intent": intent, "tool": tool}) != job["fingerprint"]:
            self.store.finish(request["id"], "failed", error="tool-configuration-changed")
            return
        process = None
        try:
            # Only explicitly configured environment values enter the tool.
            # Secret-file contents do not enter requests or transition receipts.
            environment = {"PATH": os.defpath}
            environment.update(tool.get("environment", {}))
            for key, path in tool.get("credential_files", {}).items():
                environment[key] = Path(path).read_text().strip()
            with tempfile.TemporaryFile() as input_file, \
                 ProcessOutput(self.root / "captures" / request["id"]) as output:
                input_file.write(json.dumps(request["input"]).encode())
                input_file.seek(0)
                process = subprocess.Popen(
                    tool["command"] + request["arguments"],
                    stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=tool.get("cwd"), env=environment, start_new_session=True)
                until = time.monotonic() + max(0, request["deadline"] - time.time())
                reason = None
                output.attach(process)
                while process.poll() is None or not output.complete:
                    try:
                        output.pump()
                    except StorageExhausted:
                        reason = "storage-exhausted"
                    if self.stop.is_set():
                        reason = "gateway-stopped"
                    elif self.store.cancellation_requested(request["id"]):
                        reason = "cancelled"
                    elif time.monotonic() >= until:
                        reason = "deadline"
                    if reason:
                        self.terminate(process)
                        break
                    self.stop.wait(0.01)
                artifacts = output.artifacts(self.root / "tool-results", partial=bool(reason))
                stdout, stdout_artifact = artifacts["stdout"]
                stderr, stderr_artifact = artifacts["stderr"]
                result = {"stdout": stdout, "stderr": stderr, "exit_code": process.returncode}
                outputs = {"stdout": stdout_artifact, "stderr": stderr_artifact}
                large = stdout_artifact["preview_truncated"] or stderr_artifact["preview_truncated"]
                if large:
                    result["artifacts"] = {"stdout": stdout_artifact, "stderr": stderr_artifact}
                    result["preview_truncated"] = True
                if not reason and process.returncode == 0:
                    if tool.get("result_format") == "json":
                        stream = output.records["stdout"]["file"]
                        stream.seek(0)
                        try:
                            parsed = json.load(stream)
                        except (ValueError, UnicodeError) as error:
                            state = "uncertain" if job["effect"] == "write" else "failed"
                            self.store.finish(request["id"], state, result=result, error=type(error).__name__, outputs=outputs)
                            return
                        if stdout_artifact["preview_truncated"]:
                            result["result_format"] = "json"
                            result["stored_json"] = stdout_artifact
                        else:
                            result = parsed
                    self.store.finish(request["id"], "succeeded", result=result, outputs=outputs)
                    return
                reason = reason or "tool-exit"
                state = ("uncertain" if job["effect"] == "write" else
                         "cancelled" if reason == "cancelled" else "failed")
                self.store.finish(request["id"], state, result=result, error=reason, outputs=outputs)
        except Exception as exc:
            if process is not None and process.poll() is None:
                self.terminate(process)
            state = "uncertain" if process is not None and job["effect"] == "write" else "failed"
            self.store.finish(request["id"], state, error=type(exc).__name__)

    def consume(self):
        try:
            while not self.stop.is_set():
                job = self.store.claim()
                if job:
                    self.execute(job)
                else:
                    self.ready.wait(0.05)
                    self.ready.clear()
        except Exception as exc:
            # A broken journal must stop dispatch, not leave an apparently
            # healthy service whose execution thread has silently died.
            self.failure = type(exc).__name__
            self.stop.set()

    def run(self):
        gateway = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                try:
                    raw = self.rfile.readline(MAX_FRAME + 1)
                    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
                        raise ValueError("invalid gateway frame")
                    value = gateway.dispatch(json.loads(raw))
                    reply = {"agent": gateway.agent, "value": value}
                except (ValueError, KeyError, TypeError) as exc:
                    reply = {"agent": gateway.agent, "error": {
                        "code": "conflict" if isinstance(exc, Conflict) else "invalid-request",
                        "message": str(exc) if not isinstance(exc, KeyError) else "unknown tool or field"}}
                self.wfile.write((json.dumps(reply) + "\n").encode())

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        consumer = threading.Thread(target=self.consume, daemon=True)
        with Server(str(self.socket_path), Handler) as server:
            os.chmod(self.socket_path, 0o600)
            listener = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            listener.start()
            consumer.start()
            try:
                while not self.stop.wait(0.05):
                    pass
            except KeyboardInterrupt:
                pass
            finally:
                self.stop.set()
                self.ready.set()
                server.shutdown()
                listener.join(timeout=5)
                consumer.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
        self.lock.close()
        if self.failure:
            raise RuntimeError("gateway dispatch stopped: " + self.failure)
