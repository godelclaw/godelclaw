"""An attach client: durable mail input and the shared native operator controls."""
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import uuid

from mode_channel import store


class ControlError(RuntimeError):
    pass


class Controls:
    def __init__(self, command, *, cwd, environment, log, timeout=10):
        self.timeout, self.lock, self.replies = timeout, threading.Lock(), queue.Queue()
        self.process = subprocess.Popen(command, cwd=cwd, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
            text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                # PeTTa println may render a String as its JSON quotation.
                if line.startswith('"'):
                    try: line = json.loads(line)
                    except ValueError: continue
                if line.startswith("CLAW_CONTROL "):
                    try: self.replies.put(json.loads(line[len("CLAW_CONTROL "):]))
                    except ValueError: continue
        finally:
            self.replies.put(None)

    def request(self, kind, **fields):
        with self.lock:
            identity = uuid.uuid4().hex
            try:
                self.process.stdin.write(json.dumps(dict(id=identity, kind=kind, **fields)) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                raise ControlError("control process unavailable; no automatic retry") from error
            while True:
                try: reply = self.replies.get(timeout=self.timeout)
                except queue.Empty as error:
                    raise ControlError("control reply unavailable; effects may be unknown; inspect state before retrying") from error
                if reply is None:
                    raise ControlError("control process ended; inspect its diagnostic log")
                if reply.get("id") == identity:
                    if "error" in reply: raise ControlError(reply["error"])
                    return reply["body"]

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        try: self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.terminate(); self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        if self.process.stdout: self.process.stdout.close()


class MailSession:
    """Client retry records supplement the service's authoritative receipts.

    The display stream is not an input queue. This adapter uses the existing
    authenticated mail/Space service and does not claim to grant operator rights.
    """
    def __init__(self, root, recipient, command):
        self.root, self.recipient, self.command = Path(root), recipient, list(command)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sender = "console-" + hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
        self.lock, self.processes, self.closed = threading.Lock(), set(), False

    def call(self, *args, timeout=35):
        if self.command[-1].endswith(".py"):
            # Large pasted messages must not pass through execve's argv limit.
            shim = ("import json,runpy,sys; path=sys.argv[1]; "
                    "sys.argv=[path,*json.load(sys.stdin)]; runpy.run_path(path,run_name='__main__')")
            command = [*self.command[:-1], "-c", shim, self.command[-1]]
            data = json.dumps(["--json", *args])
        else:
            command, data = [*self.command, "--json", *args], None
        with self.lock:
            if self.closed: raise RuntimeError("terminal client detached; retained input can be retried")
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True)
            self.processes.add(process)
        try:
            output, _ = process.communicate(data, timeout=timeout)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.communicate()
            raise
        finally:
            with self.lock: self.processes.discard(process)
        try: result = json.loads(output)
        except ValueError as error:
            raise RuntimeError("mail service returned no valid receipt") from error
        if process.returncode not in (0, 3):
            path = self.root / "errors" / (uuid.uuid4().hex + ".json")
            store(path, result)
            detail = str(result.get("reason") or result.get("error") or result.get("status", "unavailable"))
            raise RuntimeError("mail operation failed: " + detail[:300] + "; full receipt: " + str(path))
        return result

    def close(self):
        # Only this frontend's IPC clients are stopped. Accepted messages and
        # cognition belong to their service and survive detachment.
        with self.lock:
            self.closed = True
            for process in list(self.processes):
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass

    def prepare(self, text):
        identity = uuid.uuid4().hex
        record = dict(id=identity, recipient=self.recipient, sender=self.sender, text=str(text))
        store(self.root / "pending" / (identity + ".json"), record)
        return record

    def publish(self, record):
        result = self.call("send", "--id", record["id"], record["sender"], record["recipient"], record["text"])
        if result.get("status") not in ("accepted", "duplicate") or result.get("id") != record["id"]:
            raise RuntimeError("input acceptance is unconfirmed; retained for retry under the same id")
        store(self.root / "accepted" / (record["id"] + ".json"), dict(record=record, receipt=result))
        (self.root / "pending" / (record["id"] + ".json")).unlink(missing_ok=True)
        return result

    def retry(self):
        results = []
        for path in sorted((self.root / "pending").glob("*.json")):
            try:
                results.append(self.publish(json.loads(path.read_text())))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                results.append(dict(status="unconfirmed", id=path.stem, error=str(error)))
        return results

    def receive(self):
        result = self.call("wait", "--timeout", "2", self.sender)
        if result.get("status") == "delivery":
            if not isinstance(result.get("id"), str) or not isinstance(result.get("text"), str):
                raise RuntimeError("invalid reply observation; not acknowledged")
            path = self.root / "received" / (hashlib.sha256(result["id"].encode()).hexdigest() + ".json")
            store(path, result)
            receipt = self.call("ack", self.sender, result["id"])
            if receipt.get("status") not in ("settled", "duplicate") or receipt.get("id") != result["id"]:
                raise RuntimeError("reply is retained; acknowledgement is unconfirmed")
            return result
        if result.get("status") != "idle":
            raise RuntimeError("reply observation unavailable")
        return None
