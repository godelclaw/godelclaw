"""One input ledger store shared by processes through the native database server.

Cognition (mark-processed, committed) and the control process (register,
submitted, receipt, due, check) are separate processes in the live agent, so
neither can own the store: both reach it with db:connect on one server. Every
case starts one server on a fresh temporary store, runs real cetta client
processes against it, and compares the records with input_ack.py (the SQLite
oracle) run sequentially in the order the commits happened.

How that order is known:
  * the turn tests make it the script: each client waits for the completion
    file of the previous operation, so two live processes interleave in an order
    fixed in advance (this proves cross-process visibility, not contention);
  * the free-running test lets two clients race, with a third process committing
    unrelated writes all the while (every such commit makes an in-flight
    transaction lose its race and run again). It then asks which of the
    interleavings of the two operation lists, run through the oracle, give
    every answer and the final records that were seen. Per-client order is
    known, so the interleavings are the whole space of commit orders; a result
    that no interleaving explains fails. The limit: when several interleavings
    give the same answers and records, they are equivalent for these inputs and
    the exact one is not recovered.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import itertools
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, closing, contextmanager
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import input_ack
from observed_strategy import Ledger
import test_input_ledger as base

MODULE = ROOT / "src/input_ledger.metta"
ENGINE = os.environ.get("CETTA_BIN")
WORKER = Path(os.environ.get("CETTA_VALUE_WORKER_BIN") or (Path(ENGINE).with_name("cetta-value-worker") if ENGINE else ""))
SERVER_MODULE = Path(os.environ.get("CETTA_DB_SERVER_MODULE") or ROOT / "src/input_ledger_server.metta")
LANE = "42.0"
MAX_AGE = 172800
TEST_SECONDS = 150


def available():
    return bool(ENGINE) and WORKER.is_file() and SERVER_MODULE.is_file()


class DbServer:
    """One native db server process on `directory`/store, listening on `directory`/s.sock."""
    def __init__(self, directory, value_bytes=input_ack.LEDGER_VALUE_BYTES):
        self.directory = Path(directory)
        self.store = self.directory / "store"
        self.socket = self.directory / "s.sock"
        self.value_bytes = value_bytes  # the unit's allowance unless a test asks for another
        self.process = None
        self.starts = 0
        self.pids = []

    def start(self):
        assert self.process is None
        self.starts += 1
        environment = dict(os.environ, CETTA_DB_SOURCE=str(self.store), CETTA_DB_SOCKET=str(self.socket))
        log = open(self.directory / ("server-%d.log" % self.starts), "wb")
        with log:
            self.process = subprocess.Popen(
                [str(WORKER), "--value-bytes", str(self.value_bytes), "--serve", "--lang", "petta", str(SERVER_MODULE),
                 "db-server:configuration"],
                env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        self.pids.append(self.process.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("the db server exited: %s" % (self.directory / ("server-%d.log" % self.starts)).read_text())
            try:
                with closing(socket.socket(socket.AF_UNIX)) as probe:
                    probe.settimeout(2)
                    probe.connect(str(self.socket))
                return
            except OSError:
                time.sleep(0.02)
        raise AssertionError("the db server did not listen within 30 seconds")

    def stop(self, crash=False):
        process, self.process = self.process, None
        if process is None:
            return None
        if process.poll() is None:
            process.kill() if crash else process.terminate()
        try:
            return process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise AssertionError("the db server did not stop on SIGTERM")


def literal(text):
    return base.literal(text)


PRELUDE = """!(import! &self %s)
!(import! &self (library fs))
!(import! &self (library system))
(= (rt:wait-for $file) (if (fs:exists $file) done (rt:wait-for $file)))
(= (rt:pump $db $n $now) (rt:pump-rows $db $n $now (input-ledger:due $db 32)))
(= (rt:pump-rows $db $n $now $rows)
   (if (== $rows ())
       done
       (let* ((($head $tail) (decons-atom $rows))
              ((input-notice $t $u $l $s $c $m $p $d $st $ch) $head)
              ($answer (input-ledger:check $db $t $now %d))
              ($_ (println! (check $n $t $answer))))
         (rt:pump-rows $db $n $now $tail))))
""" % (MODULE.with_suffix(""), MAX_AGE)


def key_of(operation_key):
    """A receipt key given as a literal or as ("send", task, lane)."""
    if isinstance(operation_key, tuple):
        return base.send_key_of("default", operation_key[2], operation_key[1])
    return operation_key


def step(handle, number, operation):
    """The MeTTa steps that run one operation on `handle` and print its answer."""
    kind = operation[0]
    if kind == "register":
        _, task, update_id, lane, now = operation
        send_key = base.send_key_of("default", lane, task)
        call = "(input-ledger:register %s %s %s %s %s %r)" % (
            handle, literal(task), literal(update_id), literal(lane), literal(send_key), now)
    elif kind == "submitted":
        call = "(input-ledger:submitted %s %s)" % (handle, literal(operation[1]))
    elif kind == "receipt":
        call = "(input-ledger:receipt %s %s %s)" % (handle, literal(key_of(operation[1])), base.atom(operation[2]))
    elif kind == "mark":
        call = "(input-ledger:mark-processed %s %s %r)" % (handle, base.idlist(operation[1]), operation[2])
    elif kind == "committed":
        call = "(input-ledger:committed %s %s)" % (handle, base.idlist(operation[1]))
    elif kind == "pump":
        return ['($_ (rt:pump %s %d %r))' % (handle, number, operation[1]),
                '($_ (println! (answer %d (done))))' % number]
    else:
        raise ValueError(operation)
    return ['($_ (println! (answer %d (collapse %s))))' % (number, call)]


def client_program(socket_path, operations, *, gate=None, turns=None, stamp=False, holds=None):
    """A cetta program: connect, wait for `gate`, run the (number, operation) pairs.

    With `turns` (a directory) operation n waits for the file done-<n-1> there and
    creates done-<n> after it, so the order across processes is the numbering.
    With `holds` ({number: file}) operation n also waits for that file.
    """
    steps = ['($db (db:connect %s))' % literal(str(socket_path))]
    if gate:
        steps.append('($_ (rt:wait-for %s))' % literal(str(gate)))
    for number, operation in operations:
        if holds and number in holds:
            steps.append('($_ (rt:wait-for %s))' % literal(str(holds[number])))
        if turns and number > 0:
            steps.append('($_ (rt:wait-for %s))' % literal(str(Path(turns) / ("done-%d" % (number - 1)))))
        if stamp:
            steps.append('($bs%d (system:monotonic-ns))' % number)
            steps.append('($_ (println! (begin %d $bs%d)))' % (number, number))
        steps += step("$db", number, operation)
        if stamp:
            steps.append('($ns%d (system:monotonic-ns))' % number)
            steps.append('($_ (println! (stamp %d $ns%d)))' % (number, number))
        if turns:
            steps.append('($_ (fs:write %s ""))' % literal(str(Path(turns) / ("done-%d" % number))))
    steps.append("($_ (db:close $db))")
    return PRELUDE + "!(let* (%s)\n   done)\n" % "\n        ".join(steps)


def dump_program(socket_path):
    """Print the notices and processed ids of the store, as the ledger tests do."""
    return PRELUDE + (
        '!(let* (($db (db:connect %s))\n'
        '        ($view (db:snapshot $db inputs))\n'
        '        ($_ (println! (notices (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) '
        '(input-notice $a $b $c $d $e $f $g $h $i $j))))))\n'
        '        ($_ (println! (processed (collapse (match $view (processed-input $id $time) (processed-input $id $time))))))\n'
        '        ($_ (db:close $view)))\n   (db:close $db))\n') % literal(str(socket_path))


def parse_answers(output):
    """{operation number: python value} for every (answer n <x>) line."""
    answers = {}
    for number, text in re.findall(r"^\(answer (\d+) (.*)\)$", output, re.M):
        value = base.parse(text)[0]
        answers[int(number)] = value
    return answers


def python_value(parsed):
    """An (answer n (collapse ...)) payload as the oracle's value: one item, or ()."""
    if parsed == []:
        return None
    if isinstance(parsed, list) and len(parsed) == 1:
        item = parsed[0]
        if item == ("symbol", "true"):
            return True
        if item == ("symbol", "false"):
            return False
        return item
    return parsed


def parse_state(output):
    notices = base.parse(re.search(r"^\(notices (.*)\)$", output, re.M).group(1))[0]
    processed = base.parse(re.search(r"^\(processed (.*)\)$", output, re.M).group(1))[0]
    rows = sorted(base.notice_values(record) for record in notices)
    ids = {record[1]: record[2] for record in processed}
    return rows, ids


CLOCK, MONOTONIC = [0.0], [1000.0]


def scratch_root():
    """A memory-backed directory for the oracle's SQLite files when there is one: it commits with fsync."""
    shm = Path("/dev/shm")
    return str(shm) if shm.is_dir() and os.access(shm, os.W_OK) else None


@contextmanager
def oracle_environment():
    """The clocks and environment input_ack.py runs under, as run_notice_oracle sets them; yields a directory."""
    with tempfile.TemporaryDirectory(prefix="il-oracle-", dir=scratch_root()) as directory, ExitStack() as stack:
        for patch in (base.sqlite_environment({"METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_INPUT_ACK_SECONDS": "15", "METTACLAW_INSTANCE": "default"}),
                      mock.patch.object(input_ack.time, "time", side_effect=lambda: CLOCK[0]),
                      mock.patch.object(input_ack.time, "monotonic", side_effect=lambda: MONOTONIC[0]),
                      mock.patch.object(input_ack, "_pending", {}), mock.patch.object(input_ack, "_last_notice", {}),
                      mock.patch("cognitive_health.failure_pending", return_value=False)):
            stack.enter_context(patch)
        yield Path(directory)


class Oracle:
    """input_ack.py on one SQLite ledger file, an operation at a time; fork() copies the file, so a search can branch."""
    numbers = itertools.count()

    def __init__(self, path):
        self.path = path

    @classmethod
    def create(cls, directory):
        path = directory / ("ledger-%d.db" % next(cls.numbers))
        oracle = cls(path)
        oracle.select()
        Ledger(path)
        input_ack.processed_update_ids([])
        with closing(sqlite3.connect(path)) as db:
            db.execute(base.NOTICE_TABLE)
            db.commit()
        return oracle

    def select(self):
        os.environ["METTACLAW_STRATEGY_PATH"] = str(self.path)

    def fork(self):
        copy = Oracle(self.path.with_name("ledger-%d.db" % next(self.numbers)))
        shutil.copyfile(self.path, copy.path)
        return copy

    def discard(self):
        self.path.unlink()

    def apply(self, operation):
        self.select()
        kind = operation[0]
        if kind == "register":
            _, task, update_id, lane, now = operation
            input_ack._pending.clear()
            input_ack._last_notice.clear()
            update = base.json.dumps({"update_id": update_id} if update_id else {})
            CLOCK[0] = now
            MONOTONIC[0] += 100
            assert input_ack.key(task, lane, "message", "operator", update) == ""
            MONOTONIC[0] += 15
            return input_ack.key(task, lane, "message", "operator", update)
        if kind == "submitted":
            return input_ack.submitted(operation[1]) == 1
        if kind == "receipt":
            return input_ack.receipt(["delivery", key_of(operation[1]), 0, 1, operation[2]])
        if kind == "mark":
            CLOCK[0] = operation[2]
            return bool(input_ack.processed_update_ids(list(operation[1])))
        if kind == "committed":
            return bool(input_ack.committed(list(operation[1])))
        if kind == "pump":
            CLOCK[0] = operation[1]
            input_ack.pump(base.FakeChannel([], None))
            return None
        raise ValueError(operation)

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            notices = sorted(db.execute("SELECT %s FROM input_notices" % ", ".join(base.COLUMNS)))
            processed = {identity: seen for identity, seen in db.execute("SELECT id, processed FROM processed_inputs")}
        return [tuple(row) for row in notices], processed


def run_oracle(operations):
    """(answers by position, final notices, final processed ids) of the operations in this order."""
    with oracle_environment() as directory:
        oracle = Oracle.create(directory)
        answers = [oracle.apply(operation) for operation in operations]
        notices, processed = oracle.state()
    return answers, notices, processed


def explain(first, second, observed, rows, ids):
    """The interleavings of two operation lists that the oracle reproduces, and how many nodes the search visited.

    `observed(which, index)` is the answer one client got for its index-th operation. The search walks the
    interleavings depth first, forking the oracle at every step, and drops a branch at the first operation whose
    oracle answer differs (pump answers nothing); a complete order must also end with the observed records.
    """
    matches, visited = [], [0]
    with oracle_environment() as directory:
        def walk(oracle, taken, order):
            if taken == [len(first), len(second)]:
                if oracle.state() == (rows, ids):
                    matches.append(list(order))
                return
            for which, operations in enumerate((first, second)):
                if taken[which] == len(operations):
                    continue
                operation = operations[taken[which]]
                child = oracle.fork()
                visited[0] += 1
                try:
                    if child.apply(operation) == observed(which, taken[which]) or operation[0] == "pump":
                        taken[which] += 1
                        order.append((which, taken[which] - 1))
                        walk(child, taken, order)
                        order.pop()
                        taken[which] -= 1
                finally:
                    child.discard()
        walk(Oracle.create(directory), [0, 0], [])
    return matches, visited[0]


def delete_key_of(task, lane):
    return base.delete_key_of(lane, base.send_key_of("default", lane, task))


# Cognition's side: mark-processed and committed. Control's side: everything else.
SCENARIO = [
    ("K", ("register", "t1", "u1", LANE, 100.0)),
    ("C", ("committed", ["u1"])),
    ("K", ("submitted", "t1")),
    ("K", ("pump", 200.0)),
    ("K", ("receipt", ("send", "t1", LANE), ["delivered", 77])),
    ("C", ("mark", ["u1", "u2"], 210.0)),
    ("C", ("committed", ["u1", "u2"])),
    ("K", ("register", "t2", "u1", LANE, 212.0)),
    ("K", ("register", "t2", "u3", LANE, 215.0)),
    ("K", ("pump", 220.0)),
    ("K", ("receipt", delete_key_of("t1", LANE), ["delivered", 77])),
    ("C", ("mark", ["u3", "", "u\"quoted é 日本"], 230.0)),
    ("K", ("pump", 400000.0)),
    ("C", ("committed", ["u3", "u\"quoted é 日本"])),
    ("K", ("receipt", "unknown.key", ["delivered", 5])),
    ("K", ("submitted", "nobody")),
]


class RemoteCase(unittest.TestCase):
    """A fresh server and scratch directory per test; every process started here is stopped here."""
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="il-"))
        if len(str(self.directory / "proxy0.sock")) > 100:  # a unix socket path holds fewer than 108 bytes
            shutil.rmtree(self.directory, True)
            self.directory = Path(tempfile.mkdtemp(prefix="il-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.server = DbServer(self.directory)
        self.children = []
        self.addCleanup(self.cleanup)
        previous = signal.signal(signal.SIGALRM, self.timeout)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        signal.alarm(TEST_SECONDS)
        self.addCleanup(signal.alarm, 0)
        self.server.start()

    def timeout(self, signum, frame):
        raise AssertionError("test exceeded %d seconds" % TEST_SECONDS)

    def cleanup(self):
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.wait()
        self.server.stop(crash=True)
        for pid in self.server.pids + [process.pid for process in self.children]:
            self.assertFalse(Path("/proc/%d" % pid).exists() and
                             Path("/proc/%d/stat" % pid).read_text().split(")")[-1].split()[0] != "Z",
                             "process %d is still running" % pid)

    def spawn(self, name, program):
        path = self.directory / (name + ".metta")
        path.write_text(program, encoding="utf-8")
        log = open(self.directory / (name + ".out"), "wb")
        with log:
            process = subprocess.Popen([ENGINE, "--lang", "petta", str(path)], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, cwd=self.directory)
        process.output = self.directory / (name + ".out")
        self.children.append(process)
        return process

    def finish(self, process, timeout=90):
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            self.fail("a client did not finish within %d seconds:\n%s" % (timeout, process.output.read_text()[-3000:]))
        return process.output.read_text(encoding="utf-8")

    def run_client(self, name, program, timeout=90):
        return self.finish(self.spawn(name, program), timeout)

    def dump(self):
        return parse_state(self.run_client("dump", dump_program(self.server.socket)))

    def ensure(self):
        out = self.run_client("ensure", PRELUDE + (
            '!(let* (($db (db:connect %s))\n        ($_ (println! (ensure (input-ledger:ensure $db)))))\n   (db:close $db))\n')
            % literal(str(self.server.socket)))
        self.assertIn("(ensure true)", out)

    def release(self, name="go"):
        (self.directory / name).write_text("go")


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class TurnTest(RemoteCase):
    """Two live client processes, one store, a commit order fixed in advance."""
    def check_against_oracle(self, scenario, outputs):
        operations = [operation for _, operation in scenario]
        expected, notices, processed = run_oracle(operations)
        answers = {}
        for output in outputs:
            self.assertNotIn("(Error", output, output[:3000])
            answers.update(parse_answers(output))
        self.assertEqual(sorted(answers), list(range(len(operations))))
        for number, operation in enumerate(operations):
            if operation[0] != "pump":
                self.assertEqual(python_value(answers[number]), expected[number], (number, operation))
        rows, ids = self.dump()
        self.assertEqual(rows, notices)
        self.assertEqual(ids, processed)
        return rows, ids

    def test_cognition_and_control_in_turns_match_the_oracle(self):
        self.ensure()
        for owner in "CK":
            self.assertTrue(any(who == owner for who, _ in SCENARIO))
        turns = self.directory / "turns"
        turns.mkdir()
        numbered = list(enumerate(SCENARIO))
        gate = self.directory / "go"
        programs = {owner: client_program(self.server.socket, [(n, op) for n, (who, op) in numbered if who == owner],
                                          gate=gate, turns=turns) for owner in "CK"}
        processes = {owner: self.spawn("client-" + owner, programs[owner]) for owner in "CK"}
        self.release()
        outputs = [self.finish(processes[owner]) for owner in "CK"]
        rows, ids = self.check_against_oracle(SCENARIO, outputs)
        # The scenario reaches the interesting states: a delete key stored, an expiry, a refused register.
        self.assertIn("expired", {row[8] for row in rows})
        self.assertTrue(any(row[7] for row in rows))
        self.assertEqual(ids["u1"], 210.0)
        self.assertIn("u\"quoted é 日本", ids)



# ---- request ids, derived independently in Python ----

def mrepr(value):
    """The text of MeTTa's repr for a string, number or (head item ...) expression."""
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (int, float)):
        return repr(value)
    return "(" + " ".join(item if n == 0 else mrepr(item) for n, item in enumerate(value)) + ")"


def field(text):
    return "%d:%s" % (len(text.encode("utf-8")), text)


def request_id(verb, *values):
    """il1.<verb>.<sha256 of the verb and the values, each length-prefixed>, as the module derives it."""
    framed = field(verb) + "".join(field(mrepr(value)) for value in values)
    return "il1.%s.%s" % (verb, hashlib.sha256(framed.encode("utf-8")).hexdigest())


def batch(ids):
    """The ids of a mark-processed batch as the module sorts them: by the text of their repr."""
    return sorted(mrepr(value) for value in ids)


def mark_id(ids, now):
    # The sorted texts are themselves fields, so each goes through repr once more when framed.
    framed = field("input-ledger:mark-processed") + field(mrepr(now)) + "".join(field(mrepr(text)) for text in batch(ids))
    return "il1.input-ledger:mark-processed.%s" % hashlib.sha256(framed.encode("utf-8")).hexdigest()


def verb_id(name, *values):
    return request_id("input-ledger:" + name, *values)


class DropPublishProxy:
    """A unix-socket relay to the server that loses the reply of the next publish after the server committed."""
    def __init__(self, path, upstream):
        self.path, self.upstream = Path(path), Path(upstream)
        self.armed, self.dropped = threading.Event(), threading.Event()
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.path))
        self.listener.listen(8)
        self.closing = False
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    @staticmethod
    def frame(connection):
        header = b""
        while len(header) < 8:
            part = connection.recv(8 - len(header))
            if not part:
                return None
            header += part
        size, = struct.unpack(">I", header[4:])
        body = b""
        while len(body) < size:
            part = connection.recv(size - len(body))
            if not part:
                return None
            body += part
        return header + body

    def accept(self):
        while not self.closing:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.relay, args=(client,), daemon=True).start()

    def relay(self, client):
        with client, socket.socket(socket.AF_UNIX) as server:
            client.settimeout(60)
            server.settimeout(60)
            try:
                server.connect(str(self.upstream))
                while True:
                    request = self.frame(client)
                    if request is None:
                        return
                    server.sendall(request)
                    reply = self.frame(server)
                    if reply is None:
                        return
                    # A publish of a client-side transaction, or a ledger verb run by the server.
                    if (b"DbPublish" in request or b"LedgerCall" in request) and self.armed.is_set():
                        self.armed.clear()
                        self.dropped.set()
                        return
                    client.sendall(reply)
            except OSError:
                return

    def close(self):
        self.closing = True
        self.listener.close()


def connect_program(socket_path, *steps):
    return PRELUDE + "!(let* (($db (db:connect %s))\n        %s\n        ($_ (db:close $db)))\n   done)\n" % (
        literal(str(socket_path)), "\n        ".join(steps))


def show(label, expression):
    # A label that is also a function of the engine (`first`, `again`...) would be evaluated: all carry a prefix.
    return '($_ (println! (out-%s (collapse %s))))' % (label, expression)


def outputs_of(output, label):
    return [base.parse(text)[0] for text in re.findall(r"^\(out-%s (.*)\)$" % label, output, re.M)]


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class ReplayTest(RemoteCase):
    def register_call(self, task="t1", update="u1", now=100.0):
        key = base.send_key_of("default", LANE, task)
        return "(input-ledger:register $db %s %s %s %s %r)" % (
            literal(task), literal(update), literal(LANE), literal(key), now), key

    def test_a_lost_reply_is_reported_uncertain_and_a_retry_replays_the_first_answer(self):
        self.ensure()
        proxy = DropPublishProxy(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        proxy.armed.set()
        call, key = self.register_call()
        out = self.run_client("lost", connect_program(proxy.path, show("first", call),
                              show("again", call)))
        self.assertTrue(proxy.dropped.is_set(), "the proxy never saw a publish")
        wanted = verb_id("register", "t1", "u1", LANE, key, 100.0)
        # The first call: the server committed, the reply was lost. The shape is the verb's own failure shape
        # and carries the request id of the write; the dead handle then says unavailable, not "not committed".
        self.assertEqual(outputs_of(out, "first"),
                         [[[("symbol", "Error"), ("symbol", "input-ledger:register"),
                            [("symbol", "db:uncertain"), wanted, ("symbol", "inputs"), ("symbol", "input-ledger:register")]]]])
        self.assertEqual(outputs_of(out, "again"), [[[("symbol", "Error"), ("symbol", "db:transaction"), ("symbol", "unavailable")]]])
        rows, ids = self.dump()
        self.assertEqual([row[0] for row in rows], ["t1"])
        # Cognition now processes the update; a retry by a new connection, with the same inputs and the same
        # request id, must give the first answer (the key) and apply nothing again. Running the write afresh
        # would answer "": the update is processed. A later $now is a new write and does answer "".
        out = self.run_client("retry", connect_program(
            self.server.socket,
            show("mark", '(input-ledger:mark-processed $db ("u1") 120.0)'),
            show("retry", '(input-ledger:register $db "t1" "u1" %s %s 100.0)' % (literal(LANE), literal(key))),
            show("later", '(input-ledger:register $db "t1" "u1" %s %s 130.0)' % (literal(LANE), literal(key))),
            show("resolved", "(db:resolve $db inputs %s input-ledger:register)" % literal(wanted))))
        self.assertEqual(outputs_of(out, "mark"), [[("symbol", "true")]])
        self.assertEqual(outputs_of(out, "retry"), [[key]])
        self.assertEqual(outputs_of(out, "later"), [[""]])
        self.assertEqual(outputs_of(out, "resolved"), [[[("symbol", "db:resolved"), wanted, [key]]]])
        rows, ids = self.dump()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0][0], rows[0][4], rows[0][6]), ("t1", 100.0, 1))
        self.assertEqual(ids, {"u1": 120.0})

    def test_an_uncertain_attempt_is_tried_again_with_its_request_and_gives_the_first_answer(self):
        self.ensure()
        first = '(|-> ($space) (let $_ (add-atom $space (processed-input "synthetic" 1.0)) done))'
        second = '(|-> ($space) (let $_ (add-atom $space (processed-input "never" 2.0)) done))'
        uncertain = '(db:uncertain "il1.synthetic" inputs input-ledger:mark-processed)'
        out = self.run_client("synthetic", connect_program(
            self.server.socket,
            show("commit", '(input-ledger:write $db input-ledger:mark-processed "il1.synthetic" %s 3 none)' % first),
            # An attempt whose outcome is uncertain, as a lost reply would leave it, for a write that did commit.
            show("retried", '(input-ledger:settle $db input-ledger:mark-processed "il1.synthetic" %s 3 none %s)' % (second, uncertain)),
            show("exhausted", '(input-ledger:settle $db input-ledger:mark-processed "il1.synthetic" %s 1 none %s)' % (second, uncertain)),
            show("fresh", '(input-ledger:settle $db input-ledger:mark-processed "il1.unseen" %s 3 none %s)' % (second, uncertain))))
        self.assertEqual(outputs_of(out, "commit"), [[("symbol", "done")]])
        # The retry resolves the receipt: the answer of the first commit, and the second body never runs.
        self.assertEqual(outputs_of(out, "retried"), [[("symbol", "done")]])
        # With no attempt left the uncertain outcome is returned as it is.
        self.assertEqual(outputs_of(out, "exhausted"),
                         [[[("symbol", "db:uncertain"), "il1.synthetic", ("symbol", "inputs"), ("symbol", "input-ledger:mark-processed")]]])
        # An uncertain write that did not commit is applied by its retry.
        self.assertEqual(outputs_of(out, "fresh"), [[("symbol", "done")]])
        self.assertEqual(self.dump()[1], {"synthetic": 1.0, "never": 2.0})

    def test_a_lost_reply_of_every_verb_is_replayed_with_its_normalized_shape(self):
        self.ensure()
        key = base.send_key_of("default", LANE, "t1")
        delete = delete_key_of("t1", LANE)
        verbs = [
            ("register", '(input-ledger:register $db "t1" "u1" %s %s 100.0)' % (literal(LANE), literal(key)),
             verb_id("register", "t1", "u1", LANE, key, 100.0), [key]),
            ("submitted", '(input-ledger:submitted $db "t1")', verb_id("submitted", "t1"), [("symbol", "true")]),
            ("receipt", '(input-ledger:receipt $db %s (delivered 77))' % literal(key),
             verb_id("receipt", key, ["delivered", 77]), [1]),
            ("mark-processed", '(input-ledger:mark-processed $db ("u1" "u2") 150.0)', mark_id(["u1", "u2"], 150.0),
             [("symbol", "true")]),
            ("check", '(input-ledger:check $db "t1" 200.0 %d)' % MAX_AGE, verb_id("check", "t1", 200.0, MAX_AGE),
             [[("symbol", "delete"), delete, 77]]),
        ]
        for number, (name, call, wanted, answer) in enumerate(verbs):
            proxy = DropPublishProxy(self.directory / ("proxy%d.sock" % number), self.server.socket)
            self.addCleanup(proxy.close)
            proxy.armed.set()
            out = self.run_client("lost-" + name, connect_program(proxy.path, show("first", call)))
            self.assertTrue(proxy.dropped.is_set(), name)
            self.assertEqual(outputs_of(out, "first"),
                             [[[("symbol", "Error"), ("symbol", "input-ledger:" + name),
                                [("symbol", "db:uncertain"), wanted, ("symbol", "inputs"), ("symbol", "input-ledger:" + name)]]]], name)
            # A new connection retries the same write: the answer of the first commit, as the verb answers it.
            out = self.run_client("retry-" + name, connect_program(self.server.socket, show("retry", call)))
            self.assertEqual(outputs_of(out, "retry"), [answer], name)
        rows, ids = self.dump()
        expected, notices, processed = run_oracle([
            ("register", "t1", "u1", LANE, 100.0), ("submitted", "t1"), ("receipt", ("send", "t1", LANE), ["delivered", 77]),
            ("mark", ["u1", "u2"], 150.0), ("pump", 200.0)])
        self.assertEqual(rows, notices)
        self.assertEqual(ids, processed)


def interleavings(first, second):
    """Every merge of two operation lists that keeps each list's order."""
    total = len(first) + len(second)
    for places in itertools.combinations(range(total), len(first)):
        merged, left, right = [], iter(first), iter(second)
        for position in range(total):
            merged.append(next(left) if position in places else next(right))
        yield merged


def free_running_pair(tag, now):
    """Cognition's and control's operations for one round, ids and tasks named by `tag`; order matters."""
    t1, t2, u1, u2, u3 = ("%s-%s" % (tag, name) for name in ("t1", "t2", "u1", "u2", "u3"))
    cognition = [("mark", [u1], now + 10.0), ("committed", [u1]), ("mark", [u2, u3], now + 30.0),
                 ("committed", [u2]), ("committed", [u1, u3])]
    control = [("register", t1, u1, LANE, now), ("submitted", t1), ("register", t2, u2, LANE, now + 20.0),
               ("receipt", ("send", t1, LANE), ["delivered", 5]), ("pump", now + 50.0)]
    return cognition, control


# The third writer commits to another collection, which makes every in-flight transaction of the clients lose
# its race (whole-store validation) and run again. It is count-driven: it makes exactly NOISE_COMMITS commits per
# round, whatever the clients' speed, and the round waits for it, so "the noise really committed" never depends
# on timing. A commit that itself loses a race is repeated (the lost attempts are counted: each one is a client
# commit that landed inside its window).
#
# Overlap with the clients' operations is by construction, not by luck. The first NOISE_HOLD_AFTER commits are
# paced 50 ms apart (a writer committing at the pace of a commit, about 30 ms, starves the others: the first
# version of this test did), then the noise creates `hold` and commits the remaining ones back to back. Each
# client's first write after its reads (cognition's mark of two ids, control's second register) waits for `hold`,
# so the two writes are released by the same event that starts the burst of NOISE_COMMITS - NOISE_HOLD_AFTER
# consecutive noise commits (about 30 ms each). A write that starts inside the burst cannot commit without
# overlapping a noise commit, and a lost race is only repeated, at most 32 times, against fewer noise commits
# than that. The clocks of all processes are the same monotonic clock: every operation prints its begin and end
# and every noise commit its start and end, and the test prints how many noise commits overlapped the
# clients' writes (informational: the assertions are on counts and on the order the hold imposes).
NOISE_COMMITS = 14
NOISE_HOLD_AFTER = 2
NOISE_PACE_NS = 50000000
NOISE = """
(= (rt:pace $until) (if (> (system:monotonic-ns) $until) done (rt:pace $until)))
(= (rt:commit $db $n $lost)
   (case (db:transaction $db noise (|-> ($space) (add-atom $space (noise $n))))
     ((true $lost)
      ($other (rt:commit $db $n (+ $lost 1))))))
(= (rt:noise $db $n $total $after $hold $lost)
   (if (== $n $total)
       (let* (($_ (println! (noise-commits $n)))
              ($_ (println! (noise-lost $lost))))
         $n)
       (let* (($started (system:monotonic-ns))
              ($lost-here (rt:commit $db $n 0))
              ($ended (system:monotonic-ns))
              ($_ (println! (noise-span $n $started $ended)))
              ($_ (if (== (+ $n 1) $after) (fs:write $hold "") done))
              ($_ (if (< (+ $n 1) $after) (rt:pace (+ $started %d)) done)))
         (rt:noise $db (+ $n 1) $total $after $hold (+ $lost $lost-here)))))
""" % NOISE_PACE_NS


def noise_program(socket_path, gate, hold, total=NOISE_COMMITS, after=NOISE_HOLD_AFTER):
    return PRELUDE + NOISE + (
        '!(let* (($db (db:connect %s))\n        ($_ (db:ensure $db noise ()))\n'
        '        ($_ (rt:wait-for %s))\n        ($count (rt:noise $db 0 %d %d %s 0)))\n   (db:close $db))\n') % (
            literal(str(socket_path)), literal(str(gate)), total, after, literal(str(hold)))


def noise_dump_program(socket_path):
    """Print how many atoms the noise collection holds, and which."""
    return PRELUDE + (
        '!(let* (($db (db:connect %s))\n'
        '        ($view (db:snapshot $db noise))\n'
        '        ($_ (println! (noise-atoms (collapse (match $view (noise $n) $n)))))\n'
        '        ($_ (db:close $view)))\n   (db:close $db))\n') % literal(str(socket_path))


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class FreeRunningTest(RemoteCase):
    def test_racing_clients_and_a_noisy_third_writer_match_some_interleaving_in_the_oracle(self):
        report = []
        for round_number, now in enumerate((1000.0, 5000.0, 9000.0)):
            self.server.stop()
            shutil.rmtree(self.server.store)
            self.server.start()
            self.ensure()
            cognition, control = free_running_pair("r%d" % round_number, now)
            gate, hold = self.directory / ("go%d" % round_number), self.directory / ("hold%d" % round_number)
            mine = client_program(self.server.socket, list(enumerate(cognition)), gate=gate, stamp=True,
                                  holds={2: hold})
            theirs = client_program(self.server.socket, [(100 + n, op) for n, op in enumerate(control)], gate=gate,
                                    stamp=True, holds={102: hold})
            noisy = self.spawn("noise%d" % round_number, noise_program(self.server.socket, gate, hold))
            clients = [self.spawn("cognition%d" % round_number, mine), self.spawn("control%d" % round_number, theirs)]
            time.sleep(1.0)  # all three connected and waiting at the gate
            self.release(gate.name)
            outputs = [self.finish(process) for process in clients]
            noise_output = self.finish(noisy)  # ends by itself after its fixed number of commits
            for output in outputs:
                self.assertNotIn("(Error", output, output[:3000])
            written = int(re.search(r"^\(noise-commits (\d+)\)$", noise_output, re.M).group(1))
            noise_lost = int(re.search(r"^\(noise-lost (\d+)\)$", noise_output, re.M).group(1))
            spans = {int(n): (int(a), int(b)) for n, a, b in re.findall(r"^\(noise-span (\d+) (\d+) (\d+)\)$", noise_output, re.M)}
            self.assertEqual(sorted(spans), list(range(NOISE_COMMITS)), noise_output)
            stored = re.search(r"^\(noise-atoms \((.*)\)\)$", self.run_client("noise-dump%d" % round_number, noise_dump_program(self.server.socket)), re.M)
            self.assertEqual(sorted(int(n) for n in stored.group(1).split()), list(range(NOISE_COMMITS)))
            answers = {}
            for output in outputs:
                answers.update(parse_answers(output))
            stamps = {int(n): int(ns) for output in outputs for n, ns in re.findall(r"^\(stamp (\d+) (\d+)\)$", output, re.M)}
            self.assertEqual(sorted(answers), list(range(len(cognition))) + [100 + n for n in range(len(control))])
            rows, ids = self.dump()
            matches, visited = explain(
                cognition, control,
                lambda which, index: python_value(answers[index if which == 0 else 100 + index]), rows, ids)
            total = len(list(interleavings(cognition, control)))
            # The order in which the clients printed their completions is one candidate commit order (a reply can
            # reach its client after a later commit's reply reached the other): noted, not required.
            completion = sorted([(0, n) for n in range(len(cognition))] + [(1, n) for n in range(len(control))],
                                key=lambda item: stamps[item[1] if item[0] == 0 else 100 + item[1]])
            begins = {int(n): int(ns) for output in outputs for n, ns in re.findall(r"^\(begin (\d+) (\d+)\)$", output, re.M)}
            held = {"cognition": (begins[2], stamps[2]), "control": (begins[102], stamps[102])}
            # What the hold imposes, whatever the speed: the held writes began after the commit that released them.
            self.assertTrue(all(begin > spans[NOISE_HOLD_AFTER - 1][1] for begin, _ in held.values()), (held, spans))
            # Operations that ran while at least one noise commit was in flight, over all ten operations.
            overlapping = sum(1 for n in stamps if any(start < stamps[n] and begins[n] < end for start, end in spans.values()))
            report.append((round_number, written, noise_lost, overlapping, len(matches), total, visited, completion in matches))
            self.assertTrue(matches, "no interleaving of the two operation lists explains the answers and records "
                            "(round %d, %d noise commits): answers %s rows %s" % (round_number, written, answers, rows))
            self.assertLess(len(matches), total, "every interleaving matched: the round does not constrain the order")
        if os.environ.get("INPUT_LEDGER_VERBOSE"):
            print("round, noise commits, noise attempts lost, client operations that overlapped a noise commit, "
                  "matching orders, orders, search nodes, completion order matches:", report, file=sys.stderr)
        # The noise writer made exactly its commits in every round (a count, not a timing).
        self.assertEqual([item[1] for item in report], [NOISE_COMMITS] * 3, report)
        # The one clock-based check, as weak as can be: some client operation, in some round, was in flight while
        # a noise commit was (all three processes are released by the same file and the noise goes on for at least
        # 14 commits, about 300 ms; seen: 2 to 8 operations per round, with 24 busy loops on 16 cores as well).
        self.assertGreater(sum(item[3] for item in report), 0, report)



@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class RestartTest(RemoteCase):
    def after_restart(self, crash):
        self.ensure()
        key = base.send_key_of("default", LANE, "t1")
        register = '(input-ledger:register $db "t1" "u1" %s %s 100.0)' % (literal(LANE), literal(key))
        out = self.run_client("before", connect_program(
            self.server.socket, show("register", register), show("mark", '(input-ledger:mark-processed $db ("u9") 50.0)')))
        self.assertEqual(outputs_of(out, "register"), [[key]])
        self.assertEqual(self.server.stop(crash=crash), -9 if crash else 0)
        self.server.start()
        self.assertEqual(len(set(self.server.pids)), 2)
        # The data survived, the notice is still due, and the receipt of the first register survived too: after
        # cognition marks u1, the same register replays its first answer (the key), where running it again says "".
        out = self.run_client("after", connect_program(
            self.server.socket,
            show("committed", '(input-ledger:committed $db ("u9"))'),
            show("lost", '(input-ledger:committed $db ("u1"))'),
            show("due", "(input-ledger:due $db 5)"),
            show("mark", '(input-ledger:mark-processed $db ("u1") 120.0)'),
            show("replay", register),
            show("fresh", '(input-ledger:register $db "t1" "u1" %s %s 101.0)' % (literal(LANE), literal(key)))))
        self.assertEqual(outputs_of(out, "committed"), [[("symbol", "true")]])
        self.assertEqual(outputs_of(out, "lost"), [[("symbol", "false")]])
        self.assertEqual(len(outputs_of(out, "due")[0][0]), 1)
        self.assertEqual(outputs_of(out, "replay"), [[key]])
        self.assertEqual(outputs_of(out, "fresh"), [[""]])
        expected, notices, processed = run_oracle([("register", "t1", "u1", LANE, 100.0), ("mark", ["u9"], 50.0),
                                                    ("mark", ["u1"], 120.0)])
        rows, ids = self.dump()
        self.assertEqual((rows, ids), (notices, processed))

    def test_a_restart_keeps_the_records_and_the_receipts(self):
        self.after_restart(crash=False)

    def test_a_crashed_server_restarts_with_the_records_and_the_receipts(self):
        self.after_restart(crash=True)

    def test_a_handle_opened_before_a_restart_answers_with_an_error_and_a_new_connection_works(self):
        self.ensure()
        gate1, gate2 = self.directory / "phase1", self.directory / "phase2"
        program = PRELUDE + (
            '!(let* (($db (db:connect %s))\n'
            '        %s\n'
            '        ($_ (fs:write %s ""))\n'
            '        ($_ (rt:wait-for %s))\n'
            '        %s\n        %s\n'
            '        ($new (db:connect %s))\n'
            '        %s\n        %s\n'
            '        ($_ (db:close $new)))\n   done)\n') % (
            literal(str(self.server.socket)),
            show("before", '(input-ledger:mark-processed $db ("h1") 1.0)'),
            literal(str(gate1)), literal(str(gate2)),
            show("old-read", '(input-ledger:committed $db ("h1"))'),
            show("old-write", '(input-ledger:mark-processed $db ("h2") 2.0)'),
            literal(str(self.server.socket)),
            show("new-write", '(input-ledger:mark-processed $new ("h2") 2.0)'),
            show("new-read", '(input-ledger:committed $new ("h1" "h2"))'))
        process = self.spawn("live", program)
        deadline = time.monotonic() + 60
        while not gate1.exists():
            self.assertLess(time.monotonic(), deadline, "the client never reached the restart")
            self.assertIsNone(process.poll(), process.output.read_text())
            time.sleep(0.05)
        self.server.stop()
        self.server.start()
        gate2.write_text("go")
        out = self.finish(process)
        self.assertEqual(outputs_of(out, "before"), [[("symbol", "true")]])
        old_read, old_write = outputs_of(out, "old-read"), outputs_of(out, "old-write")
        # An error each, not a hang and not a false answer: the verbs' reads and writes fail the same way.
        self.assertEqual([len(old_read), len(old_write)], [1, 1])
        for answer in (old_read[0], old_write[0]):
            self.assertEqual(len(answer), 1, answer)
            self.assertEqual(answer[0][0], ("symbol", "Error"), answer)
        self.assertEqual(outputs_of(out, "new-write"), [[("symbol", "true")]])
        self.assertEqual(outputs_of(out, "new-read"), [[("symbol", "true")]])
        self.assertEqual(self.dump()[1], {"h1": 1.0, "h2": 2.0})


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class DeadClientTest(RemoteCase):
    def test_a_client_killed_inside_a_transaction_publishes_nothing_and_blocks_nobody(self):
        self.ensure()
        inside = self.directory / "inside"
        body = ('(|-> ($space) (let* (($_ (add-atom $space (processed-input "zombie" 1.0)))\n'
                '                   ($_ (add-atom $space (input-notice "zt" "zu" "42.0" "zk" 1.0 none 0 none registered 0)))\n'
                '                   ($_ (fs:write %s "in"))\n'
                '                   ($_ (rt:wait-for %s))) done))') % (literal(str(inside)), literal(str(self.directory / "never")))
        request = literal("il1.killed-mid-transaction")
        process = self.spawn("doomed", connect_program(
            self.server.socket, show("doomed", "(input-ledger:write $db input-ledger:mark-processed %s %s 3 none)" % (request, body))))
        deadline = time.monotonic() + 60
        while not inside.exists():
            self.assertLess(time.monotonic(), deadline, "the client never entered its transaction")
            self.assertIsNone(process.poll(), process.output.read_text())
            time.sleep(0.02)
        process.kill()
        self.assertEqual(process.wait(30), -signal.SIGKILL)
        out = self.run_client("survivor", connect_program(
            self.server.socket,
            show("committed", '(input-ledger:committed $db ("zombie"))'),
            show("mark", '(input-ledger:mark-processed $db ("alive") 5.0)'),
            show("register", '(input-ledger:register $db "t1" "u1" "42.0" "K" 6.0)'),
            show("resolve", "(db:resolve $db inputs %s input-ledger:mark-processed)" % request)))
        self.assertEqual(outputs_of(out, "committed"), [[("symbol", "false")]])
        self.assertEqual(outputs_of(out, "mark"), [[("symbol", "true")]])
        self.assertEqual(outputs_of(out, "register"), [["K"]])
        self.assertEqual(outputs_of(out, "resolve"), [[[("symbol", "db:unrecorded"), "il1.killed-mid-transaction"]]])
        rows, ids = self.dump()
        self.assertEqual([row[0] for row in rows], ["t1"])
        self.assertEqual(ids, {"alive": 5.0})

    def test_a_write_that_lost_its_race_runs_again_with_the_same_request_and_commits_once(self):
        self.ensure()
        effect, entered, release = (self.directory / name for name in ("effect", "entered", "release"))
        body = ('(|-> ($space) (let* (($_ (fs:append-text %s "x"))\n'
                '                   ($_ (fs:write %s "in"))\n'
                '                   ($_ (rt:wait-for %s))\n'
                '                   ($_ (add-atom $space (processed-input "raced" 7.0)))) done))') % (
                    literal(str(effect)), literal(str(entered)), literal(str(release)))
        racing = self.spawn("racing", connect_program(
            self.server.socket,
            '($_ (fs:write %s ""))' % literal(str(effect)),
            show("raced", "(input-ledger:write $db input-ledger:mark-processed \"il1.race\" %s 5 none)" % body)))
        deadline = time.monotonic() + 60
        while not entered.exists():
            self.assertLess(time.monotonic(), deadline, "the client never entered its transaction")
            time.sleep(0.02)
        # Another writer commits between the client's snapshot and its publish; only then may the body finish.
        self.run_client("intruder", connect_program(self.server.socket, '($_ (input-ledger:mark-processed $db ("intruder") 4.0))'))
        release.write_text("go")
        out = self.finish(racing)
        self.assertEqual(outputs_of(out, "raced"), [[("symbol", "done")]])
        self.assertEqual((self.directory / "effect").read_text(), "xx", "the body must run once per attempt: lost, then won")
        self.assertEqual(self.dump()[1], {"intruder": 4.0, "raced": 7.0})
        # With a single attempt the lost race is the engine's own error, nothing is published, and the body ran once.
        entered.unlink()
        release.unlink()
        effect.unlink()
        racing = self.spawn("single", connect_program(
            self.server.socket,
            '($_ (fs:write %s ""))' % literal(str(effect)),
            show("raced", "(input-ledger:write $db input-ledger:mark-processed \"il1.race2\" %s 1 none)" % body.replace("raced", "raced-again"))))
        deadline = time.monotonic() + 60
        while not entered.exists():
            self.assertLess(time.monotonic(), deadline, "the client never entered its transaction")
            time.sleep(0.02)
        self.run_client("intruder2", connect_program(self.server.socket, '($_ (input-ledger:mark-processed $db ("intruder2") 8.0))'))
        release.write_text("go")
        out = self.finish(racing)
        self.assertEqual(outputs_of(out, "raced"), [[[("symbol", "Error"), ("symbol", "db:transaction"),
                                                       [("symbol", "StoreRefused"), ("symbol", "conflict")]]]])
        self.assertEqual(effect.read_text(), "x")
        self.assertEqual(self.dump()[1], {"intruder": 4.0, "raced": 7.0, "intruder2": 8.0})


def program_without_server(*steps):
    return PRELUDE + "!(let* (%s)\n   done)\n" % "\n        ".join(steps)


SETTLE = [
    # (outcome, attempts left, prior, the answer as the verbs see it)
    ('(db:replayed "r" ("K"))', 1, "none", '("K")'),
    ('(db:committed "r" ("K"))', 1, "none", '("K")'),
    ('(db:replayed "r" (done))', 1, "none", "(done)"),
    ('(db:replayed "r" (1))', 1, "none", "(1)"),
    ('(db:replayed "r" (0))', 1, "none", "(0)"),
    ('(db:replayed "r" (wait))', 1, "none", "(wait)"),
    ('(db:replayed "r" ((delete "k\\"é 日本" 5)))', 1, "none", '((delete "k\\"é 日本" 5))'),
    ('(db:replayed "r" ((delete "k" 5)))', 1, "none", '((delete "k" 5))'),
    ("(Error db:transaction (StoreRefused conflict))", 1, "none", "((Error db:transaction (StoreRefused conflict)))"),
    ('(db:uncertain "r" inputs v)', 1, "none", '((db:uncertain "r" inputs v))'),
    ("(Error db:transaction unavailable)", 1, "none", "((Error db:transaction unavailable))"),
    ('(Error db:transaction unavailable)', 1, '(db:uncertain "r" inputs v)', '((db:uncertain "r" inputs v))'),
    ("(Error db:transaction (StoreRefused conflict))", 1, '(db:uncertain "r" inputs v)', '((db:uncertain "r" inputs v))'),
    ('(db:aborted "r")', 1, "none", '((db:aborted "r"))'),
]


class ProgramCase(unittest.TestCase):
    """Runs a cetta program that needs no server."""
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="il-"))
        self.addCleanup(shutil.rmtree, self.directory, True)

    def run_program(self, program):
        path = self.directory / "case.metta"
        path.write_text(program, encoding="utf-8")
        done = subprocess.run([ENGINE, "--lang", "petta", str(path)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class ReplayNormalizationTest(ProgramCase):
    """A replayed answer is the value of the first commit and takes the same verb shape as a committed one."""
    def test_settle_gives_the_value_of_a_committed_or_replayed_answer_and_keeps_failures_whole(self):
        steps = ['($_ (println! (out-s %d (collapse (input-ledger:settle none v "r" none %d %s %s)))))' % (n, left, prior, outcome)
                 for n, (outcome, left, prior, _) in enumerate(SETTLE)]
        out = self.run_program(program_without_server(*steps))
        printed = dict((int(n), text) for n, text in re.findall(r"^\(out-s (\d+) (.*)\)$", out, re.M))
        for number, (outcome, left, prior, expected) in enumerate(SETTLE):
            self.assertEqual(printed.get(number), expected, (outcome, prior))

    def test_every_verb_normalizes_a_replayed_answer_like_a_committed_one(self):
        cases = [
            ('(input-ledger:register-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" ("K"))) "K")', '("K")'),
            ('(input-ledger:register-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" (""))) "K")', '("")'),
            ('(input-ledger:register-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" ("other"))) "K")',
             '((Error input-ledger:register "other"))'),
            ('(input-ledger:receipt-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" (1))))', "(1)"),
            ('(input-ledger:receipt-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" (7))))',
             "((Error input-ledger:receipt 7))"),
            ('(input-ledger:done-answer input-ledger:submitted (input-ledger:settle none v "r" none 1 none (db:replayed "r" (done))))',
             "(true)"),
            ('(input-ledger:done-answer input-ledger:mark-processed (input-ledger:settle none v "r" none 1 none (db:replayed "r" (1))))',
             "((Error input-ledger:mark-processed 1))"),
            ('(input-ledger:check-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" ((delete "k" 5)))))',
             '((delete "k" 5))'),
            ('(input-ledger:check-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" (expired))))', "(expired)"),
            ('(input-ledger:check-answer (input-ledger:settle none v "r" none 1 none (db:replayed "r" ((delete 1 5)))))',
             "((Error input-ledger:check (delete 1 5)))"),
            ('(input-ledger:check-answer (input-ledger:settle none v "r" none 1 none (db:uncertain "r" inputs v)))',
             '((Error input-ledger:check (db:uncertain "r" inputs v)))'),
            ('(input-ledger:receipt-answer (input-ledger:unchanged (input-ledger:settle none v "r" none 1 none (db:aborted "r")) 0))', "(0)"),
            ('(input-ledger:done-answer input-ledger:submitted (input-ledger:settle none v "r" none 1 none (db:aborted "r")))', "()"),
        ]
        steps = ['($_ (println! (out-n %d (collapse %s))))' % (n, expression) for n, (expression, _) in enumerate(cases)]
        out = self.run_program(program_without_server(*steps))
        printed = dict((int(n), text) for n, text in re.findall(r"^\(out-n (\d+) (.*)\)$", out, re.M))
        for number, (expression, expected) in enumerate(cases):
            self.assertEqual(printed.get(number), expected, expression)


def id_cases():
    """(MeTTa call that yields the id, expected id from the Python derivation)."""
    awkward = 'a"b\\c é 日本 🙂 \'; ) ('
    long_batch = ["id-%04d é" % n for n in range(1500)]
    key = "42.0.00000000000000000001"
    cases = [
        ('(input-ledger:request input-ledger:submitted (input-ledger:field "t"))', verb_id("submitted", "t")),
        ('(input-ledger:request input-ledger:submitted (input-ledger:field %s))' % literal(awkward), verb_id("submitted", awkward)),
        ('(input-ledger:request input-ledger:receipt (str:concat (input-ledger:field %s) (input-ledger:field (delivered 7))))'
         % literal(awkward), verb_id("receipt", awkward, ["delivered", 7])),
        ('(input-ledger:request input-ledger:receipt (str:concat (input-ledger:field %s) (input-ledger:field (delivered 7.5))))'
         % literal(key), verb_id("receipt", key, ["delivered", 7.5])),
        ('(input-ledger:request input-ledger:receipt (str:concat (input-ledger:field %s) (input-ledger:field (delivered %s))))'
         % (literal(key), literal("7")), verb_id("receipt", key, ["delivered", "7"])),
        ('(input-ledger:request input-ledger:check (str:concat (input-ledger:field "t") (str:concat (input-ledger:field 200.0) (input-ledger:field 172800))))',
         verb_id("check", "t", 200.0, 172800)),
        ('(input-ledger:request input-ledger:check (str:concat (input-ledger:field "t") (str:concat (input-ledger:field 201.5) (input-ledger:field 172800))))',
         verb_id("check", "t", 201.5, 172800)),
        ('(input-ledger:request input-ledger:mark-processed (str:concat (input-ledger:field 5.5) (input-ledger:batch-fields %s)))'
         % base.idlist(["b", "a", awkward, ""]), mark_id(["b", "a", awkward, ""], 5.5)),
        ('(input-ledger:request input-ledger:mark-processed (str:concat (input-ledger:field 5.5) (input-ledger:batch-fields %s)))'
         % base.idlist(["", awkward, "a", "b"]), mark_id(["b", "a", awkward, ""], 5.5)),
        ('(input-ledger:request input-ledger:mark-processed (str:concat (input-ledger:field 5.5) (input-ledger:batch-fields %s)))'
         % base.idlist(long_batch), mark_id(long_batch, 5.5)),
        ('(input-ledger:request input-ledger:mark-processed (str:concat (input-ledger:field 5.5) (input-ledger:batch-fields %s)))'
         % base.idlist(list(reversed(long_batch))), mark_id(long_batch, 5.5)),
    ]
    return cases


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class RequestIdTest(ProgramCase):
    def test_ids_match_the_derivation_from_the_canonical_encoding(self):
        cases = id_cases()
        steps = ['($_ (println! (out-i %d %s)))' % (n, call) for n, (call, _) in enumerate(cases)]
        out = self.run_program(program_without_server(*steps))
        printed = dict((int(n), base.unprint(text)) for n, text in re.findall(r'^\(out-i (\d+) "(.*)"\)$', out, re.M))
        for number, (call, expected) in enumerate(cases):
            self.assertEqual(printed.get(number), expected, call[:100])
        # The reordered and reversed batches are the same write as the sorted one.
        self.assertEqual(printed[7], printed[8])
        self.assertEqual(printed[9], printed[10])
        # Whatever the batch, the id is a short fixed-size text.
        self.assertEqual(len(printed[9]), len("il1.input-ledger:mark-processed.") + 64)
        self.assertTrue(re.fullmatch(r"il1\.input-ledger:mark-processed\.[0-9a-f]{64}", printed[9]))

    def test_inputs_that_are_different_writes_have_different_ids(self):
        key = "42.0.00000000000000000001"
        distinct = [
            verb_id("check", "t", 200.0, MAX_AGE), verb_id("check", "t", 200.5, MAX_AGE), verb_id("check", "t", 200.0, 5),
            verb_id("check", "u", 200.0, MAX_AGE),
            verb_id("register", "ab", "c", LANE, key, 1.0), verb_id("register", "a", "bc", LANE, key, 1.0),
            verb_id("register", "ab", "c", LANE, key, 2.0), verb_id("register", "a", "bc", LANE, key, 2.0),
            verb_id("receipt", key, ["delivered", 7]), verb_id("receipt", key, ["delivered", 8]),
            verb_id("receipt", key, ["delivered", 7.0]), verb_id("receipt", key, ["delivered", "7"]),
            mark_id(["a"], 1.0), mark_id(["a"], 2.0), mark_id(["a", "b"], 1.0), mark_id(["a b"], 1.0), mark_id(["ab"], 1.0),
            verb_id("submitted", "t"), verb_id("submitted", "t "),
        ]
        self.assertEqual(len(set(distinct)), len(distinct))
        # An id carries its verb: the same fields under another verb are another id.
        self.assertNotEqual(verb_id("submitted", "t"), verb_id("check", "t"))



def shape_steps():
    """Calls of the db library on a handle `$db`, each printed under its own label."""
    body = lambda atom: '(|-> ($s) (let $_ (add-atom $s %s) done))' % atom
    calls = [
        ("ensure", '(db:ensure $db shapes ((SpaceIndexV1 2 0)))'),
        ("ensure-again", '(db:ensure $db shapes ((SpaceIndexV1 2 0)))'),
        ("ensure-layout", '(db:ensure $db shapes ((SpaceIndexV1 2 1)))'),
        ("tx", '(db:transaction $db shapes %s)' % body("(a 1)")),
        ("tx-values", '(db:transaction $db shapes (|-> ($s) (let $_ (add-atom $s (a 2)) (superpose (1 "two" (three))))))'),
        ("tr-commit", '(db:transaction-result $db shapes r-1 op %s)' % body("(a 3)")),
        ("tr-replay", '(db:transaction-result $db shapes r-1 op %s)' % body("(a 4)")),
        ("tr-collision-op", '(db:transaction-result $db shapes r-1 other-op %s)' % body("(a 4)")),
        ("tr-collision-collection", '(db:transaction-result $db other r-1 op %s)' % body("(a 4)")),
        ("tr-aborted", '(db:transaction-result $db shapes r-2 op (|-> ($s) (empty)))'),
        ("tr-error-body", '(db:transaction-result $db shapes r-3 op (|-> ($s) (Error x y)))'),
        ("tr-answer-without-edit", '(db:transaction-result $db shapes r-4 op (|-> ($s) 5))'),
        ("tr-replay-without-edit", '(db:transaction-result $db shapes r-4 op (|-> ($s) 6))'),
        ("resolve-known", '(db:resolve $db shapes r-1 op)'),
        ("resolve-unknown", '(db:resolve $db shapes r-9 op)'),
    ]
    steps = [show(label, call) for label, call in calls]
    steps += ['($view (db:snapshot $db shapes))',
              show("rows", "(collapse (match $view (a $x) $x))"),
              '($_ (db:close $view))',
              show("snapshot-absent", "(db:snapshot $db nosuch)"),
              show("transaction-absent", "(db:transaction $db nosuch %s)" % body("(a 1)")),
              show("close", "(db:close $db)"),
              show("closed-snapshot", "(db:snapshot $db shapes)"),
              show("closed-transaction", "(db:transaction $db shapes %s)" % body("(a 9)")),
              show("closed-result", "(db:transaction-result $db shapes r-77 op %s)" % body("(a 9)")),
              show("closed-close", "(db:close $db)")]
    return steps


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class HandleKindTest(RemoteCase):
    def test_a_connect_handle_answers_the_db_calls_as_an_open_handle_does_except_where_listed(self):
        def run(name, handle):
            out = self.run_client(name, PRELUDE + "!(let* (($db %s)\n        %s)\n   done)\n" % (
                handle, "\n        ".join(shape_steps())))
            return dict((label, text) for label, text in re.findall(r"^\(out-(\S+) (.*)\)$", out, re.M))
        opened = run("open", "(db:open %s)" % literal(str(self.directory / "local")))
        connected = run("connect", "(db:connect %s)" % literal(str(self.server.socket)))
        self.assertEqual(sorted(opened), sorted(connected))
        listed = {"rows", "snapshot-absent", "transaction-absent", "closed-snapshot", "closed-transaction",
                  "closed-result", "closed-close"}
        for label in sorted(set(opened) - listed):
            self.assertEqual(connected[label], opened[label], label)
        # The same atoms; the order of matches is the store's and is not the same from run to run.
        self.assertEqual(sorted(base.parse(opened["rows"])[0][0]), [1, 2, 3])
        self.assertEqual(sorted(base.parse(connected["rows"])[0][0]), [1, 2, 3])
        # An absent collection and a closed handle are errors on both, with the engine's own reason.
        self.assertEqual(opened["snapshot-absent"], "((Error db:snapshot (StoreRefused absent)))")
        self.assertEqual(connected["snapshot-absent"], "((Error db:snapshot invalid-response))")
        self.assertEqual(opened["transaction-absent"], "((Error db:transaction (StoreRefused absent)))")
        self.assertEqual(connected["transaction-absent"], "((Error db:snapshot invalid-response))")
        self.assertEqual(opened["closed-snapshot"], "((Error db:snapshot (StoreRefused closed-or-busy)))")
        self.assertEqual(connected["closed-snapshot"], "((Error db:snapshot closed-remote-db))")
        self.assertEqual(opened["closed-transaction"], "((Error db:transaction (StoreRefused closed-or-busy)))")
        self.assertEqual(connected["closed-transaction"], "((Error db:transaction (StoreRefused closed-remote-db)))")
        self.assertEqual(opened["closed-result"], opened["closed-transaction"])
        self.assertEqual(connected["closed-result"], connected["closed-transaction"])
        self.assertEqual(opened["closed-close"], "()")
        self.assertEqual(connected["closed-close"], "((Error db:close (StoreRefused closed)))")

    def test_every_verb_fails_with_an_error_on_a_closed_connect_handle(self):
        key = base.send_key_of("default", LANE, "t1")
        calls = [
            '(input-ledger:mark-processed $db ("a") 1.0)', '(input-ledger:committed $db ("a"))',
            '(input-ledger:register $db "t1" "u1" %s %s 1.0)' % (literal(LANE), literal(key)),
            '(input-ledger:submitted $db "t1")', '(input-ledger:receipt $db %s (delivered 5))' % literal(key),
            '(input-ledger:due $db 5)', '(input-ledger:check $db "t1" 1.0 %d)' % MAX_AGE]
        out = self.run_client("closed", PRELUDE + "!(let* (($db (db:connect %s))\n        ($_ (db:close $db))\n        %s)\n   done)\n" % (
            literal(str(self.server.socket)), "\n        ".join(show("c%d" % n, call) for n, call in enumerate(calls))))
        for number in range(len(calls)):
            answer = outputs_of(out, "c%d" % number)
            self.assertEqual(len(answer), 1, (number, out))
            self.assertEqual(len(answer[0]), 1, (number, answer))
            self.assertEqual(answer[0][0][0], ("symbol", "Error"), (number, answer))
            self.assertIn(answer[0][0][1], (("symbol", "db:transaction"), ("symbol", "db:snapshot")))


def ensure_program(socket_path, gate):
    return PRELUDE + (
        '!(let* (($db (db:connect %s))\n        ($_ (rt:wait-for %s))\n'
        '        ($_ (println! (out-ensure (input-ledger:ensure $db))))\n'
        '        ($_ (println! (out-mark (input-ledger:mark-processed $db ("e") 1.0))))\n'
        '        ($_ (println! (out-ensure-again (input-ledger:ensure $db)))))\n   (db:close $db))\n') % (
            literal(str(socket_path)), literal(str(gate)))


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class EnsureRaceTest(RemoteCase):
    def test_two_processes_creating_the_ledger_at_once_both_succeed(self):
        for round_number in range(4):
            self.server.stop()
            shutil.rmtree(self.server.store)
            self.server.start()
            gate = self.directory / ("go%d" % round_number)
            processes = [self.spawn("ensure%d-%d" % (round_number, n), ensure_program(self.server.socket, gate)) for n in range(2)]
            time.sleep(0.8)
            self.release(gate.name)
            for output in (self.finish(process) for process in processes):
                self.assertNotIn("(Error", output, output)
                for label in ("ensure", "mark", "ensure-again"):
                    self.assertEqual(outputs_of(output, label), [("symbol", "true")], label)
            self.assertEqual(self.dump()[1], {"e": 1.0})


SPONTANEOUS = [
    # Writes that change nothing give up and record no receipt, so their answers are never replayed stale.
    ("submitted", "n1"),
    ("receipt", ("send", "n1", LANE), ["delivered", 9]),
    ("register", "n1", "u1", LANE, 100.0),
    ("submitted", "n1"),
    ("receipt", ("send", "n1", LANE), ["delivered", 9]),
    ("pump", 200.0),
    ("mark", ["u1"], 210.0),
    ("pump", 220.0),
    ("receipt", delete_key_of("n1", LANE), ["delivered", 9]),
    ("submitted", "n1"),
]


@unittest.skipUnless(available(), "set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")
class RepeatTest(RemoteCase):
    def run_alone(self, operations):
        self.ensure()
        out = self.run_client("alone", client_program(self.server.socket, list(enumerate(operations))))
        self.assertNotIn("(Error", out, out[:3000])
        return out, parse_answers(out)

    def test_a_write_that_finds_nothing_to_do_is_not_remembered(self):
        out, answers = self.run_alone(SPONTANEOUS)
        expected, notices, processed = run_oracle(SPONTANEOUS)
        for number, operation in enumerate(SPONTANEOUS):
            if operation[0] != "pump":
                self.assertEqual(python_value(answers[number]), expected[number], (number, operation))
        rows, ids = self.dump()
        self.assertEqual((rows, ids), (notices, processed))
        # submitted, then receipt, before the notice exists changed nothing and left no receipt: the same calls
        # after register did their work (state submitted, message id 9) instead of replaying the first answers.
        self.assertEqual((rows[0][4], rows[0][5], rows[0][8]), (100.0, 9, "deleted"))

    def test_submitted_before_the_notice_exists_does_not_swallow_the_one_after_it(self):
        operations = [("submitted", "n1"), ("register", "n1", "u1", LANE, 100.0), ("submitted", "n1")]
        out, answers = self.run_alone(operations)
        expected, notices, processed = run_oracle(operations)
        self.assertEqual([python_value(answers[n]) for n in range(3)], expected)
        rows, ids = self.dump()
        self.assertEqual((rows, ids), (notices, processed))
        self.assertEqual(rows[0][8], "submitted")

    def test_writes_that_differ_in_one_input_are_different_writes(self):
        operations = [
            ("register", "t1", "u1", LANE, 100.0), ("register", "t2", "u2", LANE, 100.0),
            ("mark", ["a"], 5.0), ("mark", ["b"], 5.0), ("mark", ["a", "c"], 5.0), ("mark", ["a"], 6.0),
            ("submitted", "t1"), ("submitted", "t2"),
            ("receipt", ("send", "t1", LANE), ["delivered", 5]), ("receipt", ("send", "t2", LANE), ["delivered", 6]),
            ("pump", 200.0)]
        out, answers = self.run_alone(operations)
        expected, notices, processed = run_oracle(operations)
        for number, operation in enumerate(operations):
            if operation[0] != "pump":
                self.assertEqual(python_value(answers[number]), expected[number], (number, operation))
        rows, ids = self.dump()
        self.assertEqual((rows, ids), (notices, processed))
        self.assertEqual(sorted(ids), ["a", "b", "c"])
        self.assertEqual([row[0] for row in rows], ["t1", "t2"])
        self.assertEqual([(row[5], row[8], row[9]) for row in rows], [(5, "submitted", 200.0), (6, "submitted", 200.0)])

    def test_a_check_repeated_with_the_same_now_replays_and_a_later_now_is_a_new_write(self):
        key = base.send_key_of("default", LANE, "t1")
        delete = delete_key_of("t1", LANE)
        out = self.run_client("setup", connect_program(
            self.server.socket, '($_ (input-ledger:ensure $db))',
            '($_ (input-ledger:register $db "t1" "u1" %s %s 100.0))' % (literal(LANE), literal(key)),
            '($_ (input-ledger:receipt $db %s (delivered 5)))' % literal(key),
            show("first", '(input-ledger:check $db "t1" 200.0 %d)' % MAX_AGE),
            '($_ (input-ledger:mark-processed $db ("u1") 210.0))',
            show("same-now", '(input-ledger:check $db "t1" 200.0 %d)' % MAX_AGE),
            show("later", '(input-ledger:check $db "t1" 201.0 %d)' % MAX_AGE),
            show("later-again", '(input-ledger:check $db "t1" 201.0 %d)' % MAX_AGE),
            show("other-max-age", '(input-ledger:check $db "t1" 201.0 %d)' % (MAX_AGE + 1))))
        wait = [[("symbol", "wait")]]
        self.assertEqual(outputs_of(out, "first"), wait)
        # The same $now is the same write: its first answer comes back though the notice is processed since.
        # The oracle's second check, run again at that moment, would answer delete: the documented divergence.
        self.assertEqual(outputs_of(out, "same-now"), wait)
        answer = [[[("symbol", "delete"), delete, 5]]]
        self.assertEqual(outputs_of(out, "later"), answer)
        self.assertEqual(outputs_of(out, "later-again"), answer)
        self.assertEqual(outputs_of(out, "other-max-age"), answer)
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            for operation in [("register", "t1", "u1", LANE, 100.0), ("receipt", key, ["delivered", 5]),
                              ("pump", 200.0), ("mark", ["u1"], 210.0)]:
                oracle.apply(operation)
            expected_same_now = oracle.fork()
            expected_same_now.apply(("pump", 200.0))
            self.assertEqual(expected_same_now.state()[0][0][7], delete, "the oracle would have stored the delete key")
            oracle.apply(("pump", 201.0))
            notices, processed = oracle.state()
        rows, ids = self.dump()
        self.assertEqual((rows, ids), (notices, processed))


if __name__ == "__main__":
    unittest.main()
