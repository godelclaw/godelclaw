"""Process workers for the native durable activity service.

Only the coordinator accepts continuations. This controller schedules bounded
captured jobs and records each start/result before the next boundary. A saved
start without a result becomes unknown on recovery, never a blind provider or
tool rerun. CWP1 results are inert data and retain their original identities.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import time

from durable_telegram import Changes

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
BODY_MAX = 65536


class WorkerError(RuntimeError):
    pass


class Client:
    def __init__(self, path):
        self.path = str(path)

    def rpc(self, code, identity="", body=""):
        if code not in (1, 2, 3) or (identity and not IDENTITY.fullmatch(identity)):
            raise WorkerError("invalid worker request")
        name, data = identity.encode("ascii"), body.encode("utf-8")
        if len(data) > BODY_MAX or b"\0" in data:
            raise WorkerError("worker result exceeds channel bound")
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as peer:
            peer.settimeout(5); peer.connect(self.path)
            _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise WorkerError("unexpected service owner")
            peer.sendall(b"CWP1" + bytes([code, len(name)]) + name + data)
            packet, _, flags, _ = peer.recvmsg(BODY_MAX + 70)
        if flags & socket.MSG_TRUNC or len(packet) < 6 or packet[:4] != b"CWP1" or packet[5] > 64:
            raise WorkerError("invalid worker response")
        length = packet[5]
        if not 6 + length <= len(packet) <= 6 + length + BODY_MAX:
            raise WorkerError("invalid worker response size")
        status = packet[4]
        name = packet[6:6 + length].decode("ascii")
        body = packet[6 + length:].decode("utf-8")
        if status not in range(64, 73) or (name and not IDENTITY.fullmatch(name)) or "\0" in body:
            raise WorkerError("invalid worker response identity")
        if code != 1 and name != identity:
            raise WorkerError("foreign worker receipt")
        if status == 65:
            if code != 1 or not name or not body:
                raise WorkerError("invalid worker task")
        elif body or (code == 1 and name and not (status == 68 and name == identity)):
            raise WorkerError("unexpected worker response data")
        return status, name, body

    def pending(self):
        after = ""
        for _ in range(128):
            status, identity, body = self.rpc(1, after)
            if status == 64 or (status == 68 and after): return
            if status != 65: raise WorkerError("worker task unavailable")
            yield identity, body
            after = identity


def private(path, directory=False):
    info = Path(path).lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.geteuid() or info.st_mode & 0o077 or (not directory and info.st_nlink != 1):
        raise WorkerError("private worker storage required")


def packed(value):
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode()) > BODY_MAX or "\0" in body:
        raise WorkerError("worker result exceeds channel bound")
    return body


class Cache:
    def __init__(self, directory, version, retained=64):
        self.directory = Path(directory); private(self.directory, directory=True)
        self.version = version
        self.retained = retained
        path = self.directory / "worker.sqlite3"
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        self.db = None
        try:
            private(path); fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.db = sqlite3.connect(path, isolation_level=None, timeout=1)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=8192")
            self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, version TEXT NOT NULL,
                state TEXT NOT NULL, lane TEXT NOT NULL, result TEXT, updated REAL NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, body_digest TEXT NOT NULL, result_digest TEXT NOT NULL,
                updated REAL NOT NULL)""")
            for row in self.rows("started"):
                result = self.saved_result(row["id"])
                if result is None:
                    result = packed({"status": "unknown", "reason": "worker-interrupted-after-start"})
                self.result(row["id"], result)
            for row in self.rows("reported"):
                self.retire_files(row["id"])
        except BaseException:
            self.close(); raise

    def write(self, sql, args):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(sql, args); self.db.execute("COMMIT")
        except BaseException:
            # An ambiguous COMMIT spends the cache owner too. Reopening reads
            # the durable phase before any provider/tool may be started.
            self.close(); raise

    def rows(self, state):
        return self.db.execute("SELECT * FROM jobs WHERE state=? ORDER BY rowid", (state,)).fetchall()

    def add(self, identity, body, lane):
        if not IDENTITY.fullmatch(identity) or len(body.encode()) > BODY_MAX:
            raise WorkerError("invalid captured job")
        old = self.db.execute("SELECT body FROM jobs WHERE id=?", (identity,)).fetchone()
        if old:
            if old[0] != body: raise WorkerError("conflicting captured job")
            return
        if self.db.execute("SELECT 1 FROM receipts WHERE id=?", (identity,)).fetchone():
            raise WorkerError("settled task was unexpectedly reintroduced")
        self.write("INSERT INTO jobs VALUES(?,?,?,'queued',?,NULL,?)",
                   (identity, body, self.version, lane, time.time()))

    def start(self, identity):
        row = self.db.execute("SELECT state,version FROM jobs WHERE id=?", (identity,)).fetchone()
        if row is None or row[0] != "queued" or row[1] != self.version:
            raise WorkerError("job start lacks current captured authority")
        self.write("UPDATE jobs SET state='started',updated=? WHERE id=? AND state='queued'",
                   (time.time(), identity))

    def result(self, identity, body):
        # Validate a complete data envelope; it never becomes evaluator code.
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("status") not in ("completed", "failed", "unknown"):
            raise WorkerError("invalid job result")
        body = packed(value)
        old = self.db.execute("SELECT result FROM jobs WHERE id=?", (identity,)).fetchone()
        if old is None or (old[0] is not None and old[0] != body):
            raise WorkerError("conflicting job result")
        self.write("UPDATE jobs SET state='result',result=?,updated=? WHERE id=?", (body, time.time(), identity))

    def saved_result(self, identity):
        path = self.directory / identity / "result.json"
        if not path.exists(): return None
        private(path)
        if path.stat().st_size > BODY_MAX: raise WorkerError("oversized saved result")
        return packed(json.loads(path.read_text()))

    def reported(self, identity):
        row = self.db.execute("SELECT body,result FROM jobs WHERE id=? AND state='result'", (identity,)).fetchone()
        if not row: raise WorkerError("result was not ready for acknowledgment")
        self.write("INSERT OR IGNORE INTO receipts VALUES(?,?,?,?)",
                   (identity, hashlib.sha256(row[0].encode()).hexdigest(),
                    hashlib.sha256(row[1].encode()).hexdigest(), time.time()))
        self.write("UPDATE jobs SET state='reported',updated=? WHERE id=? AND state='result'", (time.time(), identity))
        # STORED is durable local observation, so this worker's temporary
        # buffers/files can retire. Native input/result receipts remain owned.
        self.retire_files(identity)
        self.write("DELETE FROM jobs WHERE state='reported' AND id NOT IN "
                   "(SELECT id FROM jobs WHERE state='reported' ORDER BY updated DESC LIMIT ?)", (self.retained,))
        self.write("DELETE FROM receipts WHERE id NOT IN "
                   "(SELECT id FROM receipts ORDER BY updated DESC LIMIT ?)", (max(self.retained, 256),))

    def retire_files(self, identity):
        directory = self.directory / identity
        if directory.exists():
            private(directory, directory=True); shutil.rmtree(directory)

    def close(self):
        if self.db is not None:
            with contextlib.suppress(sqlite3.Error): self.db.close()
            self.db = None
        if self.fd is not None:
            os.close(self.fd); self.fd = None


def request(body, args):
    value = json.loads(body)
    if not isinstance(value, dict) or value.get("lane") not in ("interactive", "background"):
        raise WorkerError("invalid captured lane")
    if value.get("kind") == "model":
        if set(value) != {"kind", "lane", "model", "tokens", "effort", "prompt"}:
            raise WorkerError("invalid captured model request")
        if value["model"] != args.model or type(value["tokens"]) is not int or not 1 <= value["tokens"] <= args.max_tokens:
            raise WorkerError("model request exceeds native configuration")
        if value["effort"] not in ("minimal", "low", "medium", "high") or not isinstance(value["prompt"], str):
            raise WorkerError("invalid model request data")
    elif value.get("kind") == "tool":
        if set(value) != {"kind", "lane", "tool", "arguments"} or value["tool"] not in args.tools:
            raise WorkerError("tool is not registered")
        if not isinstance(value["arguments"], list) or len(value["arguments"]) > 16 or any(
                not isinstance(item, str) or "\0" in item for item in value["arguments"]):
            raise WorkerError("invalid captured tool arguments")
    else:
        raise WorkerError("unsupported captured operation")
    return value


def fingerprint(args):
    digest = hashlib.sha256(b"native-activity-worker/1\0")
    for name in ("activity_worker.py", "activity_model_host.py", "activity_model_worker.metta",
                 "activity_tool_exec.py", "native_llm_host.py", "native_llm.metta", "native_lifetime.py",
                 "provider_protocol.py", "provider_stream.py", "llm_policy.py"):
        digest.update((ROOT / "src" / name).read_bytes()); digest.update(b"\0")
    for path in [args.engine, *args.tools.values()]:
        # Streaming the executable digest avoids retaining another engine-sized
        # buffer in every worker and at every launch boundary.
        with path.open("rb") as stream:
            while block := stream.read(65536): digest.update(block)
    digest.update(packed({"model": args.model, "tokens": args.max_tokens,
                         "parallel": args.parallel, "reserve": args.reserve_interactive,
                         "tools": {k: str(v) for k, v in args.tools.items()}}).encode())
    return digest.hexdigest()


class Controller:
    def __init__(self, args):
        self.args = args; self.client = Client(args.socket)
        self.cache = Cache(args.state, fingerprint(args), args.retain_results)
        self.running = {}; self.stopped = False
        self.wake = Changes([args.journal_dir, args.state], ignore_names=[
            "worker.sqlite3", "worker.sqlite3-wal", "worker.sqlite3-shm"])

    def model_environment(self, identity, directory, value):
        # Only provider configuration enters this isolated model process.
        names = ("PATH", "LANG", "LC_ALL", "PYTHONHOME", "LD_LIBRARY_PATH", "SSL_CERT_FILE",
                 "SYNTHETIC_API_KEY", "SYNTHETIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
        env = {k: os.environ[k] for k in names if k in os.environ}
        for name in ("REQUESTS_PER_HOUR", "OUTPUT_TOKENS_PER_DAY", "FAILURES_PER_WINDOW",
                     "FAILURE_WINDOW_SECONDS", "QUOTA_COOLDOWN_SECONDS"):
            key = "METTACLAW_LLM_" + name
            if key in os.environ: env[key] = os.environ[key]
        env.update(PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "modes"), str(ROOT / "modes/iter")]),
            METTACLAW_ACTIVE_ENGINE="cetta", METTACLAW_ENGINE="cetta", METTACLAW_LLM_TRANSPORT="native",
            METTACLAW_LLM_POLICY=os.environ.get("METTACLAW_LLM_POLICY", "managed"),
            METTACLAW_LLM_POLICY_PATH=os.environ.get("METTACLAW_LLM_POLICY_PATH", ""),
            METTACLAW_LLM_LEDGER_PATH=str(self.args.budget), METTACLAW_ACTIVE_LOOP_MODE="godelclaw",
            METTACLAW_OBSERVED_STRATEGY="0", METTACLAW_ACTIVITY_CALL_SCOPE=identity,
            METTACLAW_ACTIVITY_CALL_LANE=value["lane"], METTACLAW_ACTIVITY_PARENT_PID=str(os.getpid()),
            METTACLAW_ACTIVITY_CALL_CAPACITY=str(self.args.parallel),
            METTACLAW_ACTIVITY_INTERACTIVE_RESERVE=str(self.args.reserve_interactive),
            METTACLAW_ACTIVITY_RESERVED_OUTPUT=str(self.args.reserve_interactive * self.args.max_tokens),
            METTACLAW_ACTIVITY_INPUT=str(directory / "input.json"),
            METTACLAW_ACTIVITY_RESULT=str(directory / "result.json"),
            METTACLAW_LLM_RESPONSE_BYTES="1048576", SYNTHETIC_MODEL=value["model"], SYNTHETIC_EMPTY_BACKOFF="0")
        for stem in ("ENGINE_STATE", "MODEL_STATE", "LOOP_MODE", "COGNITIVE_HEALTH", "ANTHROPIC_USAGE", "RECYCLE_REQUEST", "LIFECYCLE"):
            env["METTACLAW_" + stem + "_PATH"] = str(directory / (stem.lower() + ".json"))
        (directory / "lifecycle.json").write_text('{"schema":1,"state":"running"}')
        return env

    def launch(self, row):
        if row["version"] != self.cache.version or fingerprint(self.args) != self.cache.version:
            self.cache.result(row["id"], packed({"status": "failed", "reason": "captured-adapter-changed"})); return
        try:
            value = request(row["body"], self.args)
        except (ValueError, WorkerError):
            self.cache.result(row["id"], packed({"status": "failed", "reason": "invalid-captured-request"})); return
        directory = self.args.state / row["id"]
        directory.mkdir(mode=0o700, exist_ok=True); private(directory, directory=True)
        (directory / "input.json").write_text(row["body"])
        self.cache.start(row["id"])
        if value["kind"] == "model":
            command = [str(self.args.engine), "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet",
                       str(ROOT / "src/activity_model_worker.metta")]
            env = self.model_environment(row["id"], directory, value)
            stdout = subprocess.DEVNULL
        else:
            command = [sys.executable, str(ROOT / "src/activity_tool_exec.py"), str(os.getpid()),
                       str(self.args.tools[value["tool"]]), *value["arguments"]]
            env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
            stdout = subprocess.PIPE
        try:
            process = subprocess.Popen(command, env=env, cwd=directory, stdout=stdout,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.cache.result(row["id"], packed({"status": "failed", "reason": "process-not-started"})); return
        if process.stdout: os.set_blocking(process.stdout.fileno(), False)
        self.running[row["id"]] = {"process": process, "kind": value["kind"], "lane": value["lane"],
                                     "output": bytearray(), "overflow": False}

    def collect(self):
        for identity, run in list(self.running.items()):
            process = run["process"]
            def drain():
                if not process.stdout: return
                with contextlib.suppress(BlockingIOError):
                    while True:
                        block = os.read(process.stdout.fileno(), 8192)
                        if not block: break
                        room = max(0, 32768 - len(run["output"]))
                        run["output"].extend(block[:room])
                        if len(block) > room:
                            run["overflow"] = True
                            with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
                            break
            drain()
            if process.poll() is None: continue
            # Exit can race the first nonblocking read. Read the final bytes
            # before closing the pipe; descendants are not allowed to keep it.
            drain()
            if process.stdout: process.stdout.close()
            result = self.cache.saved_result(identity) if run["kind"] == "model" else None
            if result is None:
                if run["kind"] == "tool" and not run["overflow"] and process.returncode >= 0:
                    output = bytes(run["output"])
                    decoded = output.decode("utf-8", errors="replace")
                    result = packed({"status": "completed" if process.returncode == 0 else "failed",
                                     "kind": "tool", "exit_code": process.returncode,
                                     "value": decoded[:8000], "truncated": len(decoded) > 8000,
                                     "output_bytes": len(output),
                                     "output_digest": hashlib.sha256(output).hexdigest()})
                else:
                    result = packed({"status": "unknown", "reason": "child-ended-without-recorded-result"})
            self.cache.result(identity, result); del self.running[identity]

    def step(self):
        self.collect()
        for row in self.cache.rows("result"):
            status, _, _ = self.client.rpc(2, row["id"], row["result"])
            if status != 66: raise WorkerError("result was not recorded under its original identity")
            self.cache.reported(row["id"])
        for identity, body in self.client.pending():
            try:
                lane = request(body, self.args)["lane"]
            except (ValueError, WorkerError): lane = "interactive"
            self.cache.add(identity, body, lane)
        for row in self.cache.rows("queued"):
            if len(self.running) >= self.args.parallel: break
            backgrounds = sum(item["lane"] == "background" for item in self.running.values())
            if row["lane"] == "background" and backgrounds >= self.args.parallel - self.args.reserve_interactive:
                continue
            self.launch(row)

    def run(self):
        while not self.stopped:
            self.wake.directories = tuple([str(self.args.journal_dir), str(self.args.state)] +
                                          [str(self.args.state / key) for key in self.running])
            self.wake.arm()  # arm BEFORE authoritative reads/child checks
            try:
                self.step()
            except (OSError, TimeoutError):
                # The service may restart. Keep exact results and live jobs;
                # re-observe the same durable IDs on the next event/fallback.
                pass
            fds = [self.wake.fd] if self.wake.fd >= 0 else []
            fds += [r["process"].stdout.fileno() for r in self.running.values() if r["process"].stdout]
            if fds: select.select(fds, [], [], .2 if self.running else 1)
            else: time.sleep(.2 if self.running else 1)

    def close(self):
        for run in self.running.values():
            process = run["process"]
            with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            if process.stdout: process.stdout.close()
        self.running.clear(); self.wake.close(); self.cache.close()


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--journal-dir", type=Path, required=True)
    parser.add_argument("--budget", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--reserve-interactive", type=int, default=1)
    parser.add_argument("--retain-results", type=int, default=64)
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv); args.tools = {}
    if not 1 <= args.reserve_interactive < args.parallel <= 16 or args.max_tokens <= 0 or args.retain_results < 0:
        raise WorkerError("invalid worker resource configuration")
    for path in (args.state, args.journal_dir): private(path, directory=True)
    if not args.engine.is_file() or not os.access(args.engine, os.X_OK):
        raise WorkerError("configured engine is not executable")
    for item in args.tool:
        name, separator, path = item.partition("=")
        if not separator or not IDENTITY.fullmatch(name) or name in args.tools:
            raise WorkerError("invalid registered tool")
        executable = Path(path).resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise WorkerError("registered tool is not executable")
        args.tools[name] = executable
    return args


def main(argv=None):
    os.umask(0o077)
    args = options(argv)
    if args.check:
        print("activity-worker: configuration valid; no task acquired"); return 0
    controller = Controller(args)
    def stop(*_): controller.stopped = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        controller.run()
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (WorkerError, ValueError, OSError, sqlite3.Error):
        print("activity-worker: configuration, transport or journal requires attention", file=sys.stderr)
        raise SystemExit(78)
