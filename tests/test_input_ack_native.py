"""input_ack.py on the native input ledger (METTACLAW_INPUT_LEDGER_SOCKET).

Every case starts a native db server on a fresh temporary store (the server
module of the product, src/input_ledger_server.metta; CETTA_DB_SERVER_MODULE
overrides it) and runs the public functions of input_ack.py in-process through
the native path. The sequential model is the same operation sequence on a
native ledger of its own: answers and the final records are compared, the
latter read from the server by an independent cetta client. Clocks are mocked and every METTACLAW_* variable the tests rely
on is set here (the rest is removed), so the result does not depend on the
caller's environment. Every wait has a deadline and each test has a SIGALRM
limit; the server, the clients and the ledger workers are stopped and their
pids checked in cleanup.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from execution_gateway.cetta_worker import CettaValueWorker, E, S, WorkerReplyUncertain
from native_ledger_client import NativeRunner
import test_input_ledger as base
from test_input_ledger_remote import (DropPublishProxy, ENGINE, LANE, ProgramCase, RemoteCase, SCENARIO, SERVER_MODULE,
                                      WORKER, available, delete_key_of, mark_id, mark_processed, oracle_environment,
                                      program_without_server, pump, run_oracle)

UNIT = ROOT / "deploy/systemd/pettaclaw-input-ledger@.service"
SKIP = "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)"
UNI_TASK, UNI_UPDATE = 't\u00e4"q \u65e5\u672c', 'u\u00e4"q \u65e5\u672c'

EVERY_STATE = [
    ("register", "t1", "u1", LANE, 100.0),
    ("register", "t2", "u2", LANE, 101.0),
    ("register", UNI_TASK, UNI_UPDATE, LANE, 102.0),
    ("register", "t4", "u4", LANE, 103.0),
    ("submitted", "t2"),
    ("submitted", UNI_TASK),
    ("receipt", ("send", "t1", LANE), ["delivered", 11]),
    ("receipt", ("send", UNI_TASK, LANE), ["delivered", 12]),
    ("receipt", ("send", "t2", LANE), ["delivered", 5]),
    ("mark", ["u1", UNI_UPDATE], 200.0),
    ("committed", ["u1", UNI_UPDATE]),
    ("committed", ["u1", "u2"]),
    ("pump", 300.0),
    ("receipt", delete_key_of("t1", LANE), ["delivered", 11]),
    ("receipt", delete_key_of(UNI_TASK, LANE), ["delivered", 0]),
    ("register", "t7", "u1", LANE, 310.0),
    ("register", "t5", "u5", LANE, 399990.0),
    ("register", "t6", "u6", LANE, 399991.0),
    ("submitted", "t6"),
    ("pump", 400000.0),
    ("receipt", "unknown.key", ["failed"]),
    ("receipt", ("send", "t5", LANE), ["failed", "x"]),
    ("committed", []),
    ("mark", [], 410.0),
    ("mark", ["u\u00e4", 'q"uote', "back\\slash", "u1"], 420.0),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def worker_processes():
    """This test process's ledger clients; other modules/agents own their workers."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) != os.getpid():
                    continue
                command = (entry / "cmdline").read_bytes().split(b"\0")
            except (OSError, ValueError, IndexError):
                continue
            if any(part.endswith(b"ledger_client.metta") for part in command) and b"--serve" not in command:
                found.append(int(entry.name))
    return found


def is_running(pid):
    try:
        return Path("/proc/%d/stat" % pid).read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


class NativeCase(RemoteCase):
    """RemoteCase (fresh server, scratch directory, limits) plus a pinned environment and tracked ledger workers."""
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in [name for name in os.environ if name.startswith("METTACLAW_")]:
            del os.environ[name]
        os.environ.update({"CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER)})
        input_ack.close_ledger()
        self.worker_pids = []
        original = CettaValueWorker._start

        def recording(worker):
            original(worker)
            self.worker_pids.append(worker.process.pid)

        start = mock.patch.object(CettaValueWorker, "_start", recording)
        start.start()
        self.addCleanup(start.stop)
        self.addCleanup(self.close_workers)

    def close_workers(self):
        input_ack.close_ledger()
        deadline = time.monotonic() + 10
        while any(is_running(pid) for pid in self.worker_pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([pid for pid in self.worker_pids if is_running(pid)], [], "a ledger worker is still running")

    def run_native(self, operations, socket_path=None):
        """The answers of input_ack.py on the native ledger."""
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, socket_path or self.server.socket)
            return [runner.apply(operation) for operation in operations]

    def check_sequence(self, operations):
        expected, notices, processed = run_oracle(operations)
        answers = self.run_native(operations)
        for number, operation in enumerate(operations):
            if operation[0] != "pump":
                self.assertEqual(answers[number], expected[number], (number, operation))
        rows, ids = self.dump()
        self.assertEqual(rows, notices)
        self.assertEqual(ids, processed)
        return rows, ids

    def session(self, socket_path=None):
        """Select the native path on `socket_path` (default: this case's server) for direct calls to input_ack."""
        context = oracle_environment()
        directory = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        NativeRunner.create(directory, socket_path or self.server.socket)
        return directory


FORK_SCRIPT = """
import os, sys
sys.path.insert(0, sys.argv[1])
import input_ack
assert input_ack._ledger_flag("mark-processed", (1.0, ("parent",))) == 1
parent = (input_ack._ledger[0], input_ack._ledger[2].process.pid)
child = os.fork()
if child == 0:
    code = 1
    try:
        ok = input_ack.committed(["parent"]) == 1 and input_ack._ledger_flag("mark-processed", (2.0, ("child",))) == 1
        fresh = input_ack._ledger[0] == os.getpid() and input_ack._ledger[2].process.pid != parent[1]
        code = 0 if ok and fresh else 2
        input_ack._ledger[2].close()
    finally:
        os._exit(code)
_, status = os.waitpid(child, 0)
assert os.waitstatus_to_exitcode(status) == 0, status
assert input_ack.committed(["parent", "child"]) == 1
assert input_ack._ledger[2].process.pid == parent[1], "the parent's worker was replaced"
print("ok")
"""


@unittest.skipUnless(available(), SKIP)
class OracleTest(NativeCase):
    def test_the_scenario_of_cognition_and_control_gives_the_sequential_answers_and_records(self):
        rows, ids = self.check_sequence([operation for _, operation in SCENARIO])
        self.assertTrue(rows and ids)
        self.assertIsNotNone(input_ack._ledger, "the native path never started its worker")

    def test_every_state_of_a_notice_and_quotes_and_non_ascii_ids(self):
        rows, ids = self.check_sequence(EVERY_STATE)
        self.assertEqual({row[8] for row in rows}, {"registered", "submitted", "deleted", "expired"})
        self.assertIn(UNI_UPDATE, ids)
        self.assertIn("back\\slash", ids)

    def test_a_processed_update_leaves_no_pending_entry_and_a_later_one_does(self):
        with oracle_environment():
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            mark_processed(["u1"])
            done = input_ack.key("t1", LANE, "message", "operator", '{"update_id": "u1"}')
            kept = dict(input_ack._pending)
            other = input_ack.key("t2", LANE, "message", "operator", '{"update_id": "u2"}')
            pending = sorted(input_ack._pending)
        self.assertEqual((done, kept, other), ("", {}, ""))
        self.assertEqual(pending, ["t2"])

    def test_receipts_with_keys_and_ids_that_are_not_what_the_service_sends(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            runner.apply(("register", "t1", "u1", LANE, 1.0))
            for key in (5, None, "", "no.such"):
                self.assertEqual(input_ack.receipt(["delivery", key, 0, 1, ["delivered", 1]]), 0, key)
            send = base.send_key_of("default", LANE, "t1")
            # Evidence of a failure grants nothing but is recorded as seen (1); a float or boolean is no integer.
            for outcome in (["failed", "x"], ["delivered", 1.0], ["delivered", True], ["delivered", "7"], "x", [1]):
                self.assertEqual(input_ack.receipt(["delivery", send, 0, 1, outcome]), 1, outcome)
            self.assertEqual(self.dump()[0][0][5], None)
            with self.assertRaises(OverflowError):
                input_ack.receipt(["delivery", send, 0, 1, ["delivered", 2 ** 63]])
            self.assertEqual(input_ack.receipt(["delivery", send, 0, 1, ["delivered", 2 ** 63 - 1]]), 1)
            self.assertEqual(self.dump()[0][0][5], 2 ** 63 - 1)

    def test_a_forked_process_gets_a_worker_of_its_own(self):
        # Run in a fresh single-threaded interpreter: forking this multi-threaded test process is not safe.
        script = self.directory / "fork.py"
        script.write_text(FORK_SCRIPT)
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.directory), "LANG": "C.UTF-8",
                       "CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER),
                       "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_STRATEGY_PATH": str(self.directory / "gate.db"),
                       input_ack.LEDGER_SOCKET: str(self.server.socket)}
        done = subprocess.run([sys.executable, str(script), str(ROOT / "src")], env=environment, cwd=self.directory,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ok"), done.stderr[-2000:])

    def test_an_empty_socket_variable_means_unset(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, "")
            self.assertEqual(runner.apply(("mark", ["a"], 1.0)), True)
            self.assertIsNone(input_ack._ledger, "no ledger, no worker")

    def test_the_worker_serves_many_calls_and_keeps_no_connection(self):
        before = set(worker_processes())
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            for n in range(120):
                self.assertEqual(runner.apply(("mark", ["id%d" % n], float(n))), True)
                self.assertEqual(runner.apply(("committed", ["id%d" % n])), True)
        self.assertEqual(len(self.worker_pids), 1, "one worker for all the calls")
        sockets = lambda: len(os.listdir("/proc/%d/fd" % self.server.process.pid))
        grown = sockets()
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            for n in range(150):
                runner.apply(("committed", ["id%d" % n]))
        self.assertLess(sockets() - grown, 8, "the calls leave their connections open on the server")
        self.assertEqual(len(self.dump()[1]), 120)
        self.assertEqual(set(worker_processes()) - before, {self.worker_pids[0]})


class Hang:
    """A unix socket that accepts connections and never answers."""
    def __init__(self, path):
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(path))
        self.listener.listen(8)
        self.held = []
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while True:
            try:
                self.held.append(self.listener.accept()[0])
            except OSError:
                return

    def close(self):
        self.listener.close()
        for connection in self.held:
            connection.close()


class RecordingProxy(DropPublishProxy):
    """DropPublishProxy that keeps the frames that mention DbPublish or a request id, and counts connections."""
    def __init__(self, path, upstream):
        self.publishes, self.reads, self.ids, self.connections = [], [], [], 0
        self.cut_capture, self.cut = threading.Event(), threading.Event()
        self.cut_after_ensure, self.cut_in = threading.Event(), 0
        super().__init__(path, upstream)

    def frame(self, connection):
        data = DropPublishProxy.frame(connection)
        if self.cut_in:
            self.cut_in -= 1
            if not self.cut_in:
                self.cut.set()
                return None  # the request after the reply to ensure finds a closed connection
        if data and b"DbEnsure" in data and self.cut_after_ensure.is_set():
            self.cut_after_ensure.clear()
            self.cut_in = 2
        if data and b"LedgerCall" in data and self.cut_after_ensure.is_set():
            # A ledger verb is one call on its own connection: it is closed before the verb runs.
            self.cut_after_ensure.clear()
            self.cut.set()
            return None
        if data and (b"DbCapture" in data or b"LedgerCall" in data) and self.cut_capture.is_set():
            self.cut_capture.clear()
            self.cut.set()
            return None  # the connection is lost in the middle of a snapshot read
        # A write: a publish of a client-side transaction, or a ledger verb that carries a request id.
        if data and (b"DbPublish" in data or (b"LedgerCall" in data and re.search(rb"(rq1|ck1|il1)\.", data))):
            self.publishes.append(data)
        elif data and b"LedgerCall" in data:
            self.reads.append(data)  # a ledger verb without a request id of the caller
        if data:
            self.ids += re.findall(rb"il1\.[^.\x00-\x20]+\.[0-9a-f]{64}", data)
        return data

    def relay(self, client):
        self.connections += 1
        super().relay(client)


class DbServerAt:
    """The product's server module on any store and socket path (the harness server fixes both)."""
    def __init__(self, socket_path, store):
        self.socket, self.store, self.process = Path(socket_path), Path(store), None

    def command(self):
        return [str(WORKER), "--value-bytes", str(input_ack.LEDGER_VALUE_BYTES), "--serve", "--lang", "petta",
                str(SERVER_MODULE), "db-server:configuration"]

    def start(self):
        env = {"PATH": os.environ["PATH"], "CETTA_DB_SOURCE": str(self.store), "CETTA_DB_SOCKET": str(self.socket)}
        self.process = subprocess.Popen(self.command(), env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return self.process.returncode
            try:
                with closing(socket.socket(socket.AF_UNIX)) as probe:
                    probe.connect(str(self.socket))
                return None
            except OSError:
                time.sleep(0.02)
        raise AssertionError("the server neither listened nor exited")

    def stop(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            process.wait(30)
            process.stderr.close()


@unittest.skipUnless(available(), SKIP)
class FailureTest(NativeCase):
    def test_an_unreachable_server_raises_in_every_function(self):
        self.server.stop()
        self.session()
        errors = []
        for call in (lambda: mark_processed(["u1"]),
                     lambda: input_ack.committed(["u1"]),
                     lambda: input_ack.already_committed("u1"),
                     lambda: input_ack.key("t", LANE, "message", "operator", '{"update_id": 7}'),
                     lambda: input_ack.submitted("t"),
                     lambda: input_ack.receipt(["delivery", "k", 0, 1, ["delivered", 1]]),
                     lambda: pump(base.FakeChannel([], None))):
            with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                call()
            errors.append(raised.exception)
        self.assertTrue(all("db:connect" in str(error) for error in errors), [str(error) for error in errors])
        # An unreachable server is not a lost race: none of these is LedgerBusy.
        self.assertFalse(any(isinstance(error, input_ack.LedgerBusy) for error in errors))

    def test_the_memory_of_submitted_is_updated_before_the_store_fails(self):
        self.server.stop()
        self.session()
        input_ack._pending["t"] = [1.0, False, LANE]
        with self.assertRaises(input_ack.LedgerUnavailable):
            input_ack.submitted("t")
        self.assertTrue(input_ack._pending["t"][1])

    def restart_case(self, crash):
        self.session()
        self.assertEqual(mark_processed(["before"]), 1)
        old_pids = list(self.worker_pids)
        self.assertEqual(self.server.stop(crash=crash), -9 if crash else 0)
        with self.assertRaises(input_ack.LedgerUnavailable):
            input_ack.committed(["before"])
        self.server.start()
        # A new connection per call: nothing to repair, the data is there, writes work.
        self.assertEqual(input_ack.committed(["before"]), 1)
        self.assertEqual(mark_processed(["after"]), 1)
        self.assertEqual(input_ack.committed(["before", "after"]), 1)
        self.assertEqual(set(self.dump()[1]), {"before", "after"})
        self.assertEqual(self.worker_pids, old_pids, "the worker had no reason to restart")

    def test_the_next_call_after_a_server_restart_reconnects(self):
        self.restart_case(crash=False)

    def test_the_next_call_after_a_killed_server_reconnects(self):
        self.restart_case(crash=True)

    def test_a_killed_worker_is_replaced_by_the_next_call(self):
        self.session()
        self.assertEqual(mark_processed(["a"]), 1)
        process = input_ack._ledger[2].process
        process.kill()
        process.wait()
        self.assertEqual(input_ack.committed(["a"]), 1)
        self.assertEqual(len(self.worker_pids), 2)

    def test_a_hung_server_fails_within_the_deadline_and_the_next_call_recovers(self):
        hung = Hang(self.directory / "hung.sock")
        self.addCleanup(hung.close)
        self.session(self.directory / "hung.sock")
        started = time.monotonic()
        with self.assertRaises(input_ack.LedgerUnavailable) as raised:
            input_ack.receipt(["delivery", "k", 0, 1, ["delivered", 1]], timeout=1)
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn("worker", str(raised.exception))
        os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
        self.assertEqual(mark_processed(["ok"]), 1)

    def test_a_socket_path_longer_than_a_unix_socket_holds_is_refused_by_name(self):
        base_path = str(self.directory) + "/"
        for size in (107, 108):
            path = base_path + "s" * (size - len(base_path))
            self.assertEqual(len(path.encode()), size)
            os.environ["METTACLAW_OBSERVED_STRATEGY"] = "1"
            os.environ["METTACLAW_STRATEGY_PATH"] = str(self.directory / "gate.db")
            os.environ[input_ack.LEDGER_SOCKET] = path
            input_ack.close_ledger()
            if size == 107:
                # The limit is the engine's: 107 bytes are served and reached.
                server = DbServerAt(path, self.directory / "long-store")
                self.addCleanup(server.stop)
                self.assertIsNone(server.start())
                self.assertEqual(mark_processed(["x"]), 1)
                server.stop()
            else:
                server = DbServerAt(path, self.directory / "long-store-2")
                self.addCleanup(server.stop)
                self.assertEqual(server.start(), 1, "the engine serves a socket path of 108 bytes")
                self.assertIn(b"listener unavailable", server.process.stderr.read())
                before = len(self.worker_pids)
                with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                    input_ack.committed(["x"])
                self.assertIn("108 bytes", str(raised.exception))
                self.assertEqual(len(self.worker_pids), before, "no worker for a path that cannot work")

    def test_a_write_that_lost_every_race_is_busy_and_the_rest_is_unavailable(self):
        import mode_channel
        import telegram

        class Stub:
            lock = threading.RLock()
            timeout = 1

            def __init__(self, reply):
                self.reply = reply

            def call(self, request):
                return self.reply

        conflict = E("answer", E("Error", S("db:transaction"), E("StoreRefused", S("conflict"))))
        refused = E("answer", E("Error", S("db:transaction"), E("StoreRefused", S("closed"))))
        uncertain = E("answer", E("Error", S("input-ledger:mark-processed"),
                                  E("db:uncertain", "il1.x", S("inputs"), S("input-ledger:mark-processed"))))
        self.session()
        with mock.patch.object(mode_channel, "acknowledge_handover", return_value=True):
            with mock.patch.object(input_ack, "_ledger_worker", return_value=Stub(conflict)):
                with self.assertRaises(input_ack.LedgerBusy) as raised:
                    mark_processed(["a"])
            for reply in (refused, uncertain):
                with mock.patch.object(input_ack, "_ledger_worker", return_value=Stub(reply)):
                    with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                        mark_processed(["a"])
                    self.assertNotIsInstance(raised.exception, input_ack.LedgerBusy)

    def test_a_reply_lost_between_python_and_the_worker_is_repeated_with_the_same_request(self):
        self.session()
        original = CettaValueWorker.call
        requests, lost = [], [True]

        def lossy(worker, request):
            if request[2] not in ("mark-processed", "check"):
                return original(worker, request)
            requests.append(request)
            reply = original(worker, request)
            if lost[0]:
                lost[0] = False
                raise WorkerReplyUncertain("simulated: the reply was lost", request_id="x", payload=request)
            return reply

        clock = itertools.count(1000.0, 7.0)
        with mock.patch.object(CettaValueWorker, "call", lossy), \
                mock.patch.object(input_ack.time, "time", side_effect=lambda: next(clock)):
            self.assertEqual(mark_processed(["u1", "u2"]), 1)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], requests[1], "the repeated request must be the same one")
            self.assertEqual(requests[0][3][0], 1000.0)
            self.assertEqual(next(clock), 1007.0, "$now was read once for the call and its repeat")
        self.assertEqual(self.dump()[1], {"u1": 1000.0, "u2": 1000.0})
        # The same for the step of pump: the first check commits, its reply is lost, the repeat replays it.
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            runner.apply(("register", "t3", "u3", LANE, 100.0))
            runner.apply(("receipt", ("send", "t3", LANE), ["delivered", 77]))
            runner.apply(("mark", ["u3"], 150.0))
            requests.clear()
            lost[0] = True
            channel = base.FakeChannel([], None)
            clock = itertools.count(5000.0, 11.0)
            with mock.patch.object(CettaValueWorker, "call", lossy), \
                    mock.patch.object(input_ack.time, "time", side_effect=lambda: next(clock)):
                pump(channel)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], requests[1])
            self.assertEqual(next(clock), 5011.0)
        self.assertEqual(len(channel.submits), 1)
        self.assertEqual(channel.submits[0][1], '[["delete",77]]')
        row = [row for row in self.dump()[0] if row[0] == "t3"][0]
        self.assertEqual((row[7], row[9]), (delete_key_of("t3", LANE), 5000.0))

    def test_a_connection_lost_in_a_read_is_replaced_by_a_new_one_inside_the_call(self):
        self.ensure()
        proxy = RecordingProxy(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        self.session(self.server.socket)
        self.assertEqual(mark_processed(["known"]), 1)
        os.environ[input_ack.LEDGER_SOCKET] = str(proxy.path)
        proxy.cut_capture.set()
        self.assertEqual(input_ack.committed(["known"]), 1)
        self.assertTrue(proxy.cut.is_set(), "the proxy never cut a read")
        self.assertGreaterEqual(proxy.connections, 2)
        self.assertEqual(len(self.worker_pids), 1, "the worker had no reason to restart")
        proxy.cut.clear()
        proxy.cut_capture.set()
        self.assertEqual(input_ack.committed(["known", "unknown"]), 0, "a lost read must not read as processed")
        self.assertTrue(proxy.cut.is_set())

    def test_a_connection_that_closed_before_the_verb_is_replaced_inside_the_call(self):
        self.ensure()
        proxy = RecordingProxy(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        self.session(proxy.path)
        for call, wanted in ((lambda: input_ack.committed(["nobody"]), 0),
                             (lambda: mark_processed(["via-proxy"]), 1),
                             (lambda: input_ack.committed(["via-proxy"]), 1)):
            proxy.cut.clear()
            before = proxy.connections
            proxy.cut_after_ensure.set()
            self.assertEqual(call(), wanted)
            self.assertTrue(proxy.cut.is_set(), "the proxy never closed a connection")
            self.assertGreater(proxy.connections, before + 1)
        self.assertEqual(self.dump()[1].keys(), {"via-proxy"})

    def test_a_reply_lost_after_the_commit_is_replayed_by_the_new_connection_the_module_opens(self):
        self.ensure()
        proxy = RecordingProxy(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        self.session(proxy.path)
        clock = itertools.count(300.0, 1.0)
        proxy.armed.set()
        with mock.patch.object(input_ack.time, "time", side_effect=lambda: next(clock)):
            self.assertEqual(mark_processed(["u2", "u1"]), 1)
            self.assertEqual(next(clock), 301.0)
        self.assertTrue(proxy.dropped.is_set(), "the proxy never lost a reply")
        # The one write committed and lost its reply. The module opened a new connection for the retry, which sent
        # the same verb and arguments ($now among them), so the server derived the same request id and replayed:
        # nothing was applied twice.
        calls = [frame[frame.index(b"LedgerCallV1"):] for frame in proxy.publishes + proxy.reads if b"LedgerCallV1" in frame]
        marks = [call for call in calls if b"mark-processed" in call]
        self.assertGreaterEqual(len(marks), 2, "the retry did not repeat the call")
        self.assertEqual(len(set(marks)), 1, "the retry changed the call")
        self.assertGreaterEqual(proxy.connections, 2)
        self.assertEqual(self.dump()[1], {"u1": 300.0, "u2": 300.0})
        # Applied once: a later call with the same ids and another time changes nothing.
        with mock.patch.object(input_ack.time, "time", side_effect=lambda: 999.0):
            self.assertEqual(mark_processed(["u1", "u2"]), 1)
        self.assertEqual(self.dump()[1], {"u1": 300.0, "u2": 300.0})


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class EntryTest(ProgramCase):
    """The entry of the worker (input-ledger:run), where no server is needed."""
    def printed(self, expressions):
        steps = ['($_ (println! (out-e %d (collapse %s))))' % (number, text) for number, text in enumerate(expressions)]
        out = self.run_program(program_without_server(*steps))
        return {int(n): text for n, text in re.findall(r"^\(out-e (\d+) (.*)\)$", out, re.M)}

    def test_only_a_lost_connection_or_reply_is_run_again_on_a_new_connection(self):
        cases = [
            ("(Error db:transaction unavailable)", "true"),
            ("(Error db:snapshot unavailable)", "true"),
            ("(Error db:snapshot uncertain)", "true"),
            ('(Error input-ledger:register (db:uncertain "r" inputs input-ledger:register))', "true"),
            ("(Error db:transaction (StoreRefused conflict))", "false"),
            ("(Error db:transaction (StoreRefused closed))", "false"),
            ("(Error db:connect (SpaceConnectionRefused connect))", "false"),
            ("(Error input-ledger:run (unknown-request x))", "false"),
            ("True", "false"), ('""', "false"), ("wait", "false"), ('(delete "k" 5)', "false"), ("0", "false"),
        ]
        printed = self.printed(["(input-ledger:lost %s)" % text for text, _ in cases])
        for number, (text, wanted) in enumerate(cases):
            self.assertEqual(printed.get(number), "(%s)" % wanted, text)

    def test_the_entry_answers_a_refused_connection_at_once_and_unknown_requests_with_errors(self):
        printed = self.printed([
            '(input-ledger:run (ledger "%s" "committed" (("a"))))' % (self.directory / "nobody.sock"),
            '(input-ledger:run (ledger "%s" "nosuch" ()))' % (self.directory / "nobody.sock"),
            "(input-ledger:run (nothing))",
        ])
        self.assertEqual(printed[0], "((answer (Error db:connect (SpaceConnectionRefused connect))))")
        self.assertEqual(printed[1], "((answer (Error db:connect (SpaceConnectionRefused connect))))")
        self.assertEqual(printed[2], "((Error input-ledger:run (unknown-request (nothing))))")


@unittest.skipUnless(available(), SKIP)
class TwoProcessTest(NativeCase):
    """Cognition and control are two processes with their own workers and one store."""
    def client(self, name, operations, turns):
        spec = self.directory / (name + ".spec")
        spec.write_text(repr({"socket": str(self.server.socket), "turns": str(turns), "operations": operations}))
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.directory), "LANG": "C.UTF-8",
                       "CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER)}
        log = open(self.directory / (name + ".out"), "wb")
        with log:
            process = subprocess.Popen([sys.executable, str(HERE / "native_ledger_client.py"), str(spec)],
                                       env=environment, cwd=self.directory, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
        process.output = self.directory / (name + ".out")
        self.children.append(process)
        return process

    def test_cognition_and_control_processes_share_the_store_and_match_the_sequential_model(self):
        turns = self.directory / "turns"
        turns.mkdir()
        numbered = list(enumerate(SCENARIO))
        cognition = [(n, operation) for n, (who, operation) in numbered if who == "C"]
        control = [(n, operation) for n, (who, operation) in numbered if who == "K"]
        processes = [self.client("cognition", cognition, turns), self.client("control", control, turns)]
        outputs = [self.finish(process, 120) for process in processes]
        answers, workers = {}, []
        for output in outputs:
            for line in output.splitlines():
                item = json.loads(line)
                if item[0] == "answer":
                    answers[item[1]] = item[2]
                elif item[0] == "worker":
                    workers.append(item[1])
        operations = [operation for _, operation in SCENARIO]
        expected, notices, processed = run_oracle(operations)
        self.assertEqual(sorted(answers), list(range(len(operations))), outputs)
        for number, operation in enumerate(operations):
            if operation[0] != "pump":
                self.assertEqual(answers[number], expected[number], (number, operation))
        rows, ids = self.dump()
        self.assertEqual(rows, notices)
        self.assertEqual(ids, processed)
        # Two processes, two workers, both gone with their processes.
        self.assertEqual(len(set(workers)), 2)
        deadline = time.monotonic() + 10
        while any(is_running(pid) for pid in workers) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([pid for pid in workers if is_running(pid)], [])

    def test_a_worker_does_not_outlive_its_killed_process(self):
        turns = self.directory / "turns"
        turns.mkdir()
        # The second operation waits for a turn that never comes: the process is killed there.
        process = self.client("doomed", [(0, ("mark", ["a"], 1.0)), (2, ("committed", ["a"]))], turns)
        deadline = time.monotonic() + 60
        while not (turns / "done-0").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue((turns / "done-0").exists(), "the client did not run")
        lines = [json.loads(line) for line in process.output.read_text().splitlines()]
        pid = [item[1] for item in lines if item[0] == "worker"][0]
        # A killed client cannot remove its scratch directory (in /dev/shm): this test does.
        self.addCleanup(shutil.rmtree, [item[1] for item in lines if item[0] == "directory"][0], True)
        self.assertTrue(is_running(pid))
        os.kill(process.pid, signal.SIGKILL)
        process.wait()
        deadline = time.monotonic() + 10
        while is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(is_running(pid), "the worker of a killed process stays behind")


def specifiers(text, state_home, home, instance="t"):
    """The systemd specifiers the unit uses, expanded for a user manager."""
    return (text.replace("%h/pettaclaw-%i", str(ROOT)).replace("%S", str(state_home))
            .replace("%h", str(home)).replace("%i", instance).replace("%%", "%"))


def unit_values():
    values = {}
    for line in UNIT.read_text().splitlines():
        if "=" in line and not line.startswith(("[", "#")):
            key, value = line.split("=", 1)
            values.setdefault(key, []).append(value)
    return values


class LocationTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="il-loc-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in [name for name in os.environ if name.startswith(("METTACLAW_", "XDG_"))]:
            del os.environ[name]
        os.environ["HOME"] = str(self.directory)

    def test_the_telegram_service_state_directory_keeps_its_derivation(self):
        home = self.directory
        self.assertEqual(input_ack.telegram_service_state_dir(), home / ".local/state/cetta-telegram-agent")
        os.environ["METTACLAW_INSTANCE"] = "inst"
        self.assertEqual(input_ack.telegram_service_state_dir(), home / ".local/state/cetta-telegram-inst")
        os.environ["XDG_STATE_HOME"] = str(home / "xdg")
        self.assertEqual(input_ack.telegram_service_state_dir(), home / "xdg/cetta-telegram-inst")
        os.environ["METTACLAW_TELEGRAM_SERVICE_STATE_DIR"] = str(home / "explicit")
        self.assertEqual(input_ack.telegram_service_state_dir(), home / "explicit")
        self.assertFalse(hasattr(input_ack, "state_dir"), "the old name must be gone: it named the wrong directory")

    def test_the_ledger_lives_in_the_agents_own_state_directory_the_caller_passes(self):
        home = self.directory
        self.assertEqual(input_ack.ledger_paths(home / "other"),
                         (home / "other/input-ledger/store", home / "other/input-ledger/ledger.sock"))
        with self.assertRaises(TypeError):
            input_ack.ledger_paths()  # no default: it is not the Telegram service's directory
        # The caller derives the directory as the rest of the agent does: the parent of METTACLAW_ENGINE_STATE_PATH
        # (run.sh: <XDG_STATE_HOME or ~/.local/state>/<instance>/engine).
        import agent_checkpoint
        with self.assertRaises(RuntimeError):
            agent_checkpoint.state_dir()
        with self.assertRaises(RuntimeError):
            agent_checkpoint.path()  # unconfigured, as before the refactoring
        os.environ["METTACLAW_ENGINE_STATE_PATH"] = str(home / ".local/state/godel/engine")
        self.assertEqual(agent_checkpoint.state_dir(), home / ".local/state/godel")
        self.assertEqual(agent_checkpoint.path(), home / ".local/state/godel/observed-strategy.sqlite3")
        self.assertEqual(input_ack.ledger_paths(agent_checkpoint.state_dir())[1],
                         home / ".local/state/godel/input-ledger/ledger.sock")
        os.environ["METTACLAW_STRATEGY_PATH"] = str(home / "elsewhere.db")  # the checkpoint file may be named; the directory is not
        self.assertEqual(agent_checkpoint.path(), home / "elsewhere.db")
        self.assertEqual(agent_checkpoint.state_dir(), home / ".local/state/godel")

    def test_the_change_watch_uses_the_same_state_directory(self):
        import durable_telegram
        import mode_channel
        os.environ["METTACLAW_INSTANCE"] = "watch"
        with mock.patch.object(input_ack, "_changes", None), \
                mock.patch.object(mode_channel, "state", return_value=self.directory / "mode"), \
                mock.patch.object(durable_telegram, "Changes") as changes:
            changes.return_value.arm.return_value = 1
            input_ack.arm_changes()
        self.assertEqual(changes.call_args[0][0], [str(input_ack.telegram_service_state_dir()), self.directory / "mode/channel-outbox"])

    def test_the_unit_serves_the_paths_the_python_side_derives(self):
        values = unit_values()
        os.environ["METTACLAW_INSTANCE"] = "t"
        os.environ["XDG_STATE_HOME"] = str(self.directory / "state")
        # the agent's own state directory, as run.sh derives it: <state home>/<instance>
        store, sock = input_ack.ledger_paths(self.directory / "state" / "t")
        paths = dict(item.split("=", 1) for item in values["Environment"])
        self.assertEqual(specifiers(paths["CETTA_DB_SOURCE"], self.directory / "state", self.directory), str(store))
        self.assertEqual(specifiers(paths["CETTA_DB_SOCKET"], self.directory / "state", self.directory), str(sock))
        self.assertEqual(set(paths), {"CETTA_DB_SOURCE", "CETTA_DB_SOCKET", "LEDGER_CODE"})
        self.assertEqual(specifiers(paths["LEDGER_CODE"], self.directory / "state", self.directory), str(ROOT))
        # systemd creates the directory the socket lives in, under the state directory: the owner's group (the
        # accounts this home is shared with) may use it, nobody else.
        self.assertEqual(values["StateDirectory"], ["%i/input-ledger"])
        self.assertEqual(self.directory / "state" / "t" / "input-ledger", sock.parent)
        self.assertNotIn("cetta-telegram", UNIT.read_text())
        self.assertEqual(values["StateDirectoryMode"], ["0770"])
        self.assertEqual(values["UMask"], ["0007"])

    def test_the_unit_is_a_user_template_like_the_other_pettaclaw_units(self):
        text = UNIT.read_text()
        values = unit_values()
        for key, value in (("Type", ["exec"]), ("Restart", ["always"]), ("KillMode", ["control-group"]),
                           ("WantedBy", ["default.target"]),
                           ("EnvironmentFile", ["%h/.config/godelclaw/runtime.env"]),
                           ("WorkingDirectory", ["%S/%i/input-ledger"])):
            self.assertEqual(values[key], value, key)
        self.assertEqual(values["Description"], ["Native input ledger server for %i"])
        self.assertTrue(values["ExecStart"][0].startswith("/bin/sh -c '"))
        self.assertIn('"$LEDGER_CODE/src/input_ledger_server.metta"', values["ExecStart"][0])
        self.assertTrue((ROOT / "src/input_ledger_server.metta").is_file())
        for name in ("[Unit]", "[Service]", "[Install]"):
            self.assertIn(name, text)
        # Nothing in the unit or the server is specific to one agent.
        for source in (text, (ROOT / "src/input_ledger_server.metta").read_text()):
            self.assertNotIn("godel", source.lower().replace("godelclaw", ""))  # the project's name is not an agent's

    def test_socket_paths_of_the_sample_instances_fit_a_unix_socket(self):
        for home in ("/home/agent", "/home/a-rather-long-user-name"):
            for instance in ("godel", "research", "x" * 20):
                sock = input_ack.ledger_paths(Path(home) / ".local/state" / instance)[1]
                self.assertLessEqual(len(str(sock).encode()), input_ack.SOCKET_BYTES, str(sock))


@unittest.skipUnless(available(), SKIP)
class UnitCommandTest(unittest.TestCase):
    """The ExecStart of the unit, run as systemd would (specifiers expanded) but by hand in a scratch directory."""
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="il-unit-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in [name for name in os.environ if name.startswith("METTACLAW_")]:
            del os.environ[name]
        self.addCleanup(input_ack.close_ledger)
        self.processes = []
        self.addCleanup(self.stop_all)
        previous = signal.signal(signal.SIGALRM, lambda *args: self.fail("test exceeded 120 seconds"))
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        signal.alarm(120)
        self.addCleanup(signal.alarm, 0)

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()

    def command(self):
        state = self.directory / "state"
        return shlex.split(specifiers(unit_values()["ExecStart"][0], state, self.directory))

    def serve(self, extra):
        values = unit_values()
        state = self.directory / "state"
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.directory)}
        environment.update(dict(item.split("=", 1) for item in
                                (specifiers(value, state, self.directory) for value in values["Environment"])))
        environment.update(extra)
        (state / "t/input-ledger").mkdir(parents=True)  # what StateDirectory= makes
        process = subprocess.Popen(self.command(), env=environment, cwd=self.directory, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        sock = Path(environment["CETTA_DB_SOCKET"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), "the unit's command exited")
            try:
                with closing(socket.socket(socket.AF_UNIX)) as probe:
                    probe.connect(str(sock))
                break
            except OSError:
                time.sleep(0.02)
        else:
            self.fail("the unit's command did not listen")
        return process, sock, Path(environment["CETTA_DB_SOURCE"])

    def check(self, extra):
        process, sock, store = self.serve(extra)
        os.environ.update({input_ack.LEDGER_SOCKET: str(sock), "CETTA_BIN": str(ENGINE),
                           "CETTA_VALUE_WORKER_BIN": str(WORKER), "METTACLAW_OBSERVED_STRATEGY": "1",
                           "METTACLAW_STRATEGY_PATH": str(self.directory / "gate.db")})
        self.assertEqual(mark_processed(["via-unit"]), 1)
        self.assertEqual(input_ack.committed(["via-unit"]), 1)
        self.assertTrue((store / "root.csl").is_file())
        self.assertEqual(sock.stat().st_mode & 0o077, 0, "the socket is private to its owner")
        process.terminate()
        self.assertEqual(process.wait(30), 0)

    def test_the_command_finds_the_worker_next_to_cetta_bin(self):
        self.check({"CETTA_BIN": str(ENGINE)})

    def test_the_command_prefers_cetta_value_worker_bin(self):
        self.check({"CETTA_BIN": "/nonexistent/cetta", "CETTA_VALUE_WORKER_BIN": str(WORKER)})

    def test_the_command_fails_at_once_without_any_engine(self):
        process = subprocess.run(self.command(), env={"PATH": os.environ["PATH"], "CETTA_DB_SOURCE": "x",
                                                      "CETTA_DB_SOCKET": "y"},
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertNotEqual(process.returncode, 0)

    def test_the_server_refuses_to_start_without_a_store_a_socket_or_a_usable_store(self):
        (self.directory / "file").write_text("")
        for source, sock in (("", str(self.directory / "a.sock")), (str(self.directory / "store"), ""),
                             (str(self.directory / "file"), str(self.directory / "b.sock"))):
            server = DbServerAt(sock, source)
            server.store = source  # an empty string must reach the server as it is
            environment = {"PATH": os.environ["PATH"], "CETTA_DB_SOURCE": source, "CETTA_DB_SOCKET": sock}
            process = subprocess.run(server.command(), env=environment, stdin=subprocess.DEVNULL,
                                     capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 1, (source, sock, process.stderr[-300:]))
            self.assertIn(b"invalid configuration", process.stderr)
        self.assertFalse((self.directory / "store").exists(), "a store was created for a service that cannot listen")

    def test_a_second_server_on_the_same_socket_is_refused(self):
        process, sock, store = self.serve({"CETTA_BIN": str(ENGINE)})
        second = DbServerAt(sock, self.directory / "other")
        self.addCleanup(second.stop)
        # start() returns as soon as the socket answers, which the first server does: look at the second one.
        second.start()
        try:
            code = second.process.wait(30)
        except subprocess.TimeoutExpired:
            self.fail("a second server runs on a served socket")
        self.assertNotEqual(code, 0)
        self.assertIsNone(process.poll(), "the first server went down")
        os.environ.update({input_ack.LEDGER_SOCKET: str(sock), "CETTA_BIN": str(ENGINE),
                           "CETTA_VALUE_WORKER_BIN": str(WORKER), "METTACLAW_OBSERVED_STRATEGY": "1",
                           "METTACLAW_STRATEGY_PATH": str(self.directory / "gate.db")})
        self.assertEqual(input_ack.committed(["x"]), 0)
        process.terminate()
        process.wait(30)


if __name__ == "__main__":
    unittest.main()
