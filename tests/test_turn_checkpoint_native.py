"""The turn checkpoint in the native input ledger (METTACLAW_INPUT_LEDGER_SOCKET).

observed_strategy.Ledger.checkpoint is the unit of a turn: the working set, the settled input ids (with their
notices' processed flags) and the request's processed status commit together: one native transaction
(input-ledger:checkpoint) is the only writer of all of it. Every case starts a native db server on a fresh store,
runs a sequence of turns and checks the answers of Ledger.checkpoint / working_checkpoint / input_ack.committed
and the final contents (working set, processed ids, notices, request status), read by an independent cetta
client. Every METTACLAW_* variable a test relies on is set here (the rest is removed), child processes get
an environment built from scratch (no inherited variable, so no token), and every wait has a deadline.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
import observed_strategy
from observed_strategy import Ledger
from execution_gateway.cetta_worker import CettaValueWorker, E, S, WorkerReplyUncertain
from native_ledger_client import NativeRunner
import test_input_ledger as base
from test_input_ack_native import NativeCase, RecordingProxy, SKIP
from test_input_ledger_remote import (DbServer, ENGINE, LANE, PRELUDE, WORKER, available, literal, noise_program,
                                      oracle_environment)

AWKWARD = ('q"uote \\back \nnew\tline \u00e9\u65e5\u672c \U0001d11e \u2028\u2029 {"a":[1,2]} \x7f \x00\x01\x1f '
           ';; (paren) $var !(bang) \'single\' %s {} \r\n end')
WIDE = "\u00e9\u65e5\u672c\U0001d11e"


def working(saved_at, **fields):
    value = {"loops": 3, "sleepInterval": 1, "lastresults": "r%s" % saved_at, "last_heartbeat": 9.5,
             "continuation_pending": False, "banked_loops": 0, "rest_await_input": False, "saved_at": saved_at}
    value.update(fields)
    return value


def large_working(saved_at, chars=200000):
    """The biggest ordinary working set: lastresults at its 200000-character cap, in wide characters."""
    return working(saved_at, lastresults=(WIDE * (chars // len(WIDE) + 1))[:chars])


REQUESTS = [("input:one", "message"), ("input:two", "message"), ("input:three", "message"),
            ("note-1", "notification"), ("task-1", "task"), ("goal:x", "goal"), ("autonomous", "autonomous")]

# One entry per step: ("register", task, update, now) | ("mark", ids, now) | ("checkpoint", working, ids, request)
# | ("read",)
TURNS = [
    ("register", "t1", "u1", 100.0),                                 # a notice registered before its turn
    ("checkpoint", working(10.0), ["u1"], "input:one"),
    ("read",),
    ("checkpoint", working(20.0, loops=9), ["u1", "u2"], "input:one"),   # u1 repeated: first time kept; request again
    ("register", "t2", "u2", 130.0),                                 # registered after: refused (already settled)
    ("register", "t3", "u3", 131.0),
    ("checkpoint", working(30.0), [], None),                         # no ids, no request
    ("checkpoint", working(40.0), ["", "u3"], "input:two"),          # an empty id; the notice of u3 settles
    ("checkpoint", working(50.0), ["u4", "u4", "u4"], "input:one"),  # an id three times; a settled request again
    ("checkpoint", working(55.0), ["u5"], "note-1"),                 # a notification
    ("checkpoint", working(56.0), ["u6"], "task-1"),                 # a task: not settled by a checkpoint
    ("checkpoint", working(57.0), ["u7"], "goal:x"),                 # a goal: not settled
    ("checkpoint", working(58.0), ["u8"], "no-such-request"),        # unknown request: nothing, the rest commits
    ("checkpoint", working(59.0), ["u9"], "autonomous"),
    ("register", "t9", AWKWARD, 135.0),                              # a notice with an awkward update id, before
    ("checkpoint", working(60.0, lastresults=AWKWARD, rest_timer={"deadline": 5.25, "command": AWKWARD, "status": "waiting"}),
     ["u\u00e9\u65e5 \"q\" \\ \n x", AWKWARD, "id%s", "(a b)"], "input:three"),
    ("register", "t10", AWKWARD, 140.0),                             # a notice for an awkward update id, after
    ("mark", ["u1", "after-mark"], 150.0),                           # mark-processed beside checkpoints: first time kept
    ("checkpoint", working(70.0), ["after-mark", "later"], None),
    ("checkpoint", working(71.0, lastresults="x" * 5), ["u11"], "input:three"),
    ("read",),
]
LARGE = [("checkpoint", large_working(80.0), ["big"], "input:one"), ("read",),
         ("checkpoint", working(81.0, lastresults=WIDE * 3), ["small"], None), ("read",)]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encoded(value):
    return observed_strategy.encoded(value)


def native_dump_program(socket_path):
    """What the native store holds: notices, processed ids, the working-set atoms (as the sha256 and the byte
    length of their text, so the reader does not depend on how a long string prints), the request records (the
    heads whole, the contents as their id, sha256 and length in characters, the counter) and the number of
    atoms of any kind."""
    return PRELUDE + (
        '!(import! &self (library str))\n'
        '!(let* (($db (db:connect %s))\n'
        '        ($view (db:snapshot $db inputs))\n'
        '        ($_ (println! (notices (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) '
        '(input-notice $a $b $c $d $e $f $g $h $i $j))))))\n'
        '        ($_ (println! (processed (collapse (match $view (processed-input $id $time) (processed-input $id $time))))))\n'
        '        ($_ (println! (working (collapse (match $view (working-set "current" $t) ((str:sha256 $t) (str:byte-length $t)))))))\n'
        '        ($_ (println! (requests (collapse (match $view (request $a $b $c $d $e $f $g $h $i) '
        '(request $a $b $c $d $e $f $g $h $i))))))\n'
        '        ($_ (println! (texts (collapse (match $view (request-text $r $t) ($r (str:sha256 $t) (str:char-length $t)))))))\n'
        '        ($_ (println! (counter (collapse (match $view (request-tail "tail" $n $r) $n)))))\n'
        '        ($_ (println! (chain (collapse (match $view (request-prev $r $b) ($r $b))))))\n'
        '        ($_ (println! (tail (collapse (match $view (request-tail "tail" $n $r) $r)))))\n'
        '        ($_ (println! (legacy (collapse (match $view (request-processed $r $t) (request-processed $r $t))))))\n'
        '        ($_ (println! (arrivals (collapse (match $view (ledger:arrival $r $p) ($r $p))))))\n'
        '        ($_ (println! (states (collapse (match $view (ledger:state $k $v) $k)))))\n'
        '        ($_ (println! (atoms (size-atom (collapse (match $view $a $a))))))\n'
        '        ($_ (db:close $view)))\n   (db:close $db))\n') % literal(str(socket_path))


def parse_native_full(output):
    """The dump's lines as values. The whole output is parsed at once: an id with a newline prints over two lines.
    requests: id -> (kind, checker, status, settled or None, note, summary, digest, seq); texts: id -> (sha256, characters)."""
    named = {item[0][1]: item[1] for item in base.parse(output) if isinstance(item, list) and item and isinstance(item[0], tuple)}
    heads = {record[1]: (record[3], record[4], record[5], record[6], record[7], record[8], record[9], record[2])
             for record in named["requests"]}
    return dict(notices=sorted(base.notice_values(record) for record in named["notices"]),
                processed={record[1]: record[2] for record in named["processed"]},
                working=[(record[0], record[1]) for record in named["working"]],
                requests=heads, texts={record[0]: (record[1], record[2]) for record in named["texts"]},
                counter=named["counter"], legacy=named["legacy"], atoms=named["atoms"],
                arrivals={record[0]: record[1] for record in named["arrivals"]}, states=sorted(named["states"]),
                chain={record[0]: record[1] for record in named["chain"]}, tail=named["tail"])


def parse_native(output):
    """parse_native_full as the tests of the checkpoint use it: the processed requests are those with a settled time."""
    full = parse_native_full(output)
    requests = {key: value[3] for key, value in full["requests"].items() if value[3] is not None}
    return full["notices"], full["processed"], full["working"], requests, full["atoms"]


class Turns:
    """The same turns on one ledger, on SQLite (socket None) or on the native store."""
    def __init__(self, case, socket_path=None):
        self.case, self.socket = case, socket_path
        self.seen = []

    def run(self, steps, accepted=REQUESTS):
        observed = []
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.socket)
            self.path = runner.path
            store = Ledger(self.path)
            for key, kind in accepted:
                store.accept(key, "content of " + key, kind=kind)
            self.store, self.runner = store, runner
            for step in steps:
                observed.append(self.step(step))
            self.final = self.finish()
        return observed

    def step(self, step):
        kind = step[0]
        if kind == "register":
            return ("register", self.runner.apply(("register", step[1], step[2], LANE, step[3])))
        if kind == "mark":
            return ("mark", self.runner.apply(("mark", step[1], step[2])))
        if kind == "checkpoint":
            _, value, ids, request = step
            self.seen += [str(i) for i in ids if str(i)]
            before = self.store.working_checkpoint()
            answer = self.store.checkpoint(value, ids, request)
            current = self.store.working_checkpoint()
            every = sorted(set(self.seen))
            return ("checkpoint", answer, current, current != before,
                    [input_ack.already_committed(i) for i in every],
                    input_ack.committed(every), input_ack.committed(every + ["never"]))
        if kind == "read":
            return ("read", self.store.working_checkpoint())
        raise ValueError(step)

    def finish(self):
        full = parse_native_full(self.case.run_client("dump", native_dump_program(self.socket)))
        notices, ids, stored, atoms = full["notices"], full["processed"], full["working"], full["atoms"]
        requests = {key: value[3] for key, value in full["requests"].items() if value[3] is not None}
        return {"notices": notices, "processed": ids, "requests": requests, "working": stored, "atoms": atoms,
                "arrivals": full["arrivals"], "states": full["states"],
                "statuses": {key: value[2] for key, value in full["requests"].items()}, "full": full}


@unittest.skipUnless(available(), SKIP)
class OracleTest(NativeCase):
    maxDiff = None

    def check(self, steps, accepted=REQUESTS):
        """The turns on the native ledger: (the answer of every step, the final records)."""
        side = Turns(self, self.server.socket)
        answers = side.run(steps, accepted)
        return answers, side.final

    def test_several_turns_repeated_empty_and_awkward_ids_and_notices_before_and_after(self):
        _, native = self.check(TURNS)
        self.assertEqual(native["processed"]["u1"], 10.0, "the first time of a repeated id is kept")
        self.assertEqual(native["processed"]["after-mark"], 150.0, "a mark before the checkpoint keeps its time")
        self.assertNotIn("", native["processed"], "the empty id is no input")
        self.assertEqual(set(native["requests"]), {"input:one", "input:two", "input:three", "note-1"},
                         "message and notification settle; task, goal, autonomous and unknown do not")
        self.assertEqual(len(native["working"]), 1)
        self.assertEqual(native["working"][0][0], sha(encoded(working(71.0, lastresults="x" * 5))))
        flags = {row[1]: row[6] for row in native["notices"]}
        self.assertEqual(flags["u1"], 1)
        self.assertEqual(flags["u3"], 1)
        self.assertEqual(flags[AWKWARD.replace("\r\n", "\n")], 1)
        self.assertEqual(native["statuses"]["input:one"], "processed")
        # The exact awkward id (CR LF, NUL, U+2028 included) is what the native store holds.
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            self.assertEqual(input_ack.committed([AWKWARD, "u\u00e9\u65e5 \"q\" \\ \n x", "id%s"]), 1)
            self.assertEqual(input_ack.committed([AWKWARD.replace("\r\n", "\n")]), 0)

    def test_a_large_working_set_is_stored_and_read_back_byte_for_byte(self):
        self.check(LARGE)
        self.assertGreater(len(encoded(large_working(80.0)).encode("utf-8")), 500000)
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            store = Ledger(directory / "x.db")
            store.checkpoint(large_working(90.0), ["big2"], None)
            self.assertEqual(input_ack.working_set(), encoded(large_working(90.0)))
            self.assertEqual(store.working_checkpoint(), large_working(90.0))

    def test_the_checkpoint_writes_all_four_facts_and_nothing_else(self):
        _, native = self.check(TURNS)
        self.assertEqual(native["statuses"]["input:one"], "processed")
        self.assertEqual(native["statuses"]["note-1"], "processed")
        # the working set, the processed ids, the notices, and per request a record, a content, a link and an
        # arrival, plus the tail atom and the meta keys an acceptance writes (the revision and the clock)
        self.assertEqual(set(native["arrivals"]), {key for key, _ in REQUESTS})
        self.assertEqual(native["states"], ["clock", "revision"])
        self.assertEqual(native["atoms"], 1 + len(native["processed"]) + len(native["notices"]) + 4 * len(REQUESTS) + 1 + 2)

    def test_after_a_native_checkpoint_the_message_can_be_addressed_and_is_no_longer_active(self):
        # The checkpoint's status write and disposition/active/protected/request_record see one store.
        seen = {}
        for socket_path in (self.server.socket,):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, socket_path)
                store = Ledger(runner.path)
                store.accept("input:one", "m", kind="message")
                before = (store.disposition("input:one", "addressed", "too early"), store.refusal,
                          [row["id"] for row in store.active()])
                store.checkpoint(working(1.0), ["a"], "input:one")
                record = store.request_record("input:one")
                protected_before = store.protected("input:one")["counts"]
                refused = store.disposition("input:one", "addressed", "done")
                seen[socket_path is not None] = (before, record["processed"], record["status"], protected_before, refused,
                                                 [row["id"] for row in store.active()],
                                                 store.protected("input:one")["counts"], store.request_record("input:one")["status"],
                                                 store.request_record("input:one")["note"], store.processed("input:one"))
        self.assertEqual(seen[True], ((False, "a message is addressed only after a turn has processed it", ["input:one"]),
                                       1, "processed", {"processed": 1}, True, [], {"addressed": 1}, "addressed", "done", False))

    def test_with_the_variable_unset_or_empty_a_checkpoint_fails_loudly_without_a_worker(self):
        for value in (None, ""):
            with oracle_environment() as directory:
                os.environ.pop(input_ack.LEDGER_SOCKET, None)
                if value is not None:
                    os.environ[input_ack.LEDGER_SOCKET] = value
                store = Ledger(directory / "l")
                with self.assertRaisesRegex(input_ack.LedgerUnavailable, "METTACLAW_INPUT_LEDGER_SOCKET is not set"):
                    store.checkpoint(working(1.0), ["a"], None)
                with self.assertRaises(input_ack.LedgerUnavailable):
                    store.working_checkpoint()
        self.assertIsNone(input_ack._ledger, "a worker was started with the variable unset")

    def test_a_missing_saved_at_with_ids_fails_and_writes_nothing(self):
        for socket_path in (self.server.socket,):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, socket_path)
                store = Ledger(runner.path)
                with self.assertRaises(KeyError):
                    store.checkpoint({"loops": 1}, ["a"], None)
                self.assertIsNone(store.working_checkpoint())

    def test_a_working_set_that_is_not_utf8_encodable_fails_and_writes_nothing(self):
        value = working(5.0, lastresults="\ud800")
        outcomes = []
        for socket_path in (self.server.socket,):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, socket_path)
                store = Ledger(runner.path)
                with self.assertRaises(Exception) as raised:
                    store.checkpoint(value, ["a"], None)
                outcomes.append(type(raised.exception))
                self.assertIsNone(store.working_checkpoint())
                self.assertEqual(input_ack.committed(["a"]), 0)
        self.assertTrue(all(issubclass(kind, ValueError) for kind in outcomes), outcomes)

    def test_the_boot_read_of_an_empty_store_is_none_and_the_helper_falls_back_to_the_file(self):
        import helper
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            os.environ["METTACLAW_WORKING_SET_PATH"] = str(directory / "working.json")
            (directory / "working.json").write_text(json.dumps({"loops": 4, "from": "file"}))
            self.assertIsNone(input_ack.working_set())
            store = Ledger(directory / "x.db")
            self.assertIsNone(store.working_checkpoint())
            self.assertEqual(helper._working_read(), {"loops": 4, "from": "file"})
            store.checkpoint(working(3.0, loops=11), [], None)
            self.assertEqual(helper._working_read()["loops"], 11)
            self.assertEqual(store.working_checkpoint(), working(3.0, loops=11))
            self.assertEqual(input_ack.working_set(), encoded(working(3.0, loops=11)))


@unittest.skipUnless(available(), SKIP)
class RequestIdTest(NativeCase):
    def dump_native(self):
        return parse_native(self.run_client("dump", native_dump_program(self.server.socket)))

    def test_the_same_request_id_with_the_same_arguments_replays_and_other_arguments_collide(self):
        self.session()
        first = encoded(working(1.0, lastresults="A"))
        second = encoded(working(2.0, lastresults="B"))
        self.assertTrue(input_ack.turn_checkpoint(first, ["a"], "", 1.0, request_id="ck1.fixed-a"))
        self.assertTrue(input_ack.turn_checkpoint(second, ["b"], "", 2.0, request_id="ck1.fixed-b"))
        self.assertEqual(input_ack.working_set(), second)
        # The same id and arguments again: a replay of the first commit, applied no second time (the working set
        # stays the later one, which a re-application would have overwritten).
        self.assertTrue(input_ack.turn_checkpoint(first, ["a"], "", 1.0, request_id="ck1.fixed-a"))
        self.assertEqual(input_ack.working_set(), second)
        # Another id with the very same arguments is another checkpoint: it applies (A, B, A).
        self.assertTrue(input_ack.turn_checkpoint(first, ["a"], "", 1.0, request_id="ck1.fixed-c"))
        self.assertEqual(input_ack.working_set(), first)
        # The same id with other arguments is refused by the engine, loudly, and changes nothing.
        before = self.dump_native()
        for arguments in ((second, ["a"], "", 1.0), (first, ["a", "z"], "", 1.0), (first, ["a"], "input:x", 1.0),
                          (first, ["a"], "", 1.5), (first + " ", ["a"], "", 1.0)):
            with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                input_ack.turn_checkpoint(*arguments, request_id="ck1.fixed-a")
            self.assertIn("receipt-collision", str(raised.exception))
            self.assertNotIsInstance(raised.exception, input_ack.LedgerBusy)
        self.assertEqual(self.dump_native(), before)
        self.assertEqual(input_ack.working_set(), first)

    def test_the_ids_of_a_batch_are_a_set_for_the_request_and_the_empty_id_is_nothing(self):
        self.session()
        text = encoded(working(3.0))
        self.assertTrue(input_ack.turn_checkpoint(text, ["b", "a"], "", 3.0, request_id="ck1.order"))
        self.assertTrue(input_ack.turn_checkpoint(text, ["a", "b"], "", 3.0, request_id="ck1.order"))  # replay
        self.assertTrue(input_ack.turn_checkpoint(text, ["a", "", "b"], "", 3.0, request_id="ck1.order"))
        self.assertEqual(self.dump_native()[1], {"a": 3.0, "b": 3.0})
        # Python drops the empty id before the call; the module skips it too (the verb called directly).
        self.assertTrue(input_ack._ledger_call("checkpoint", (text, ("c", "", "d"), "", 4.0, "ck1.direct-empty")))
        self.assertEqual(self.dump_native()[1], {"a": 3.0, "b": 3.0, "c": 4.0, "d": 4.0})

    def test_each_call_makes_its_own_request_id_and_every_repeat_of_one_call_carries_it(self):
        self.session()
        seen = []
        original = CettaValueWorker.call

        def spy(worker, request):
            if request[2] == "checkpoint":
                seen.append(request)
            return original(worker, request)

        with mock.patch.object(CettaValueWorker, "call", spy):
            store = Ledger(self.directory / "ids.db")
            store.checkpoint(working(1.0), ["a"], None)
            store.checkpoint(working(1.0), ["a"], None)  # identical arguments: still a second checkpoint
        self.assertEqual(len(seen), 2)
        ids = [request[3][4] for request in seen]
        self.assertTrue(all(re.fullmatch(r"ck1\.[0-9a-f]{32}", item) for item in ids), ids)
        self.assertNotEqual(ids[0], ids[1])


class ProxyCheckpoint(RecordingProxy):
    """RecordingProxy that also keeps the checkpoint request ids it sees."""
    def frame(self, connection):
        data = RecordingProxy.frame(self, connection)
        if data:
            self.ids += re.findall(rb"ck1\.[0-9a-f]{32}", data)
        return data


STEPS = [(working(1.0, lastresults="first"), ["a1"], "input:one"),
         (working(2.0, lastresults="second"), ["a2", "a1"], None),
         (working(3.0, lastresults="third \u00e9\u65e5"), ["a3"], "input:two")]


@unittest.skipUnless(available(), SKIP)
class RetryTest(NativeCase):
    def run_steps(self, socket_path, drop_at=None, proxy=None):
        """The working set and the settled requests after STEPS on the ledger at socket_path."""
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, socket_path)
            store = Ledger(runner.path)
            for key, kind in REQUESTS:
                store.accept(key, "c", kind=kind)
            for number, (value, ids, request) in enumerate(STEPS):
                if proxy is not None and number == drop_at:
                    proxy.armed.set()
                store.checkpoint(value, ids, request)
            flags = {row["id"] for row in input_ack.request_rows() if row["processed"]}
            return store.working_checkpoint(), flags

    def test_a_checkpoint_whose_reply_is_lost_after_the_commit_is_replayed_once_and_ends_as_without_the_loss(self):
        self.ensure()
        fresh = DbServer(self.directory / "fresh")
        (self.directory / "fresh").mkdir()
        fresh.start()
        self.addCleanup(fresh.stop)
        expected_working, expected_flags = self.run_steps(fresh.socket)
        proxy = ProxyCheckpoint(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        got_working, _ = self.run_steps(proxy.path, drop_at=1, proxy=proxy)
        self.assertTrue(proxy.dropped.is_set(), "the proxy never lost a reply")
        self.assertEqual(got_working, expected_working)
        notices, ids, stored, requests, atoms = parse_native(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(ids, {"a1": 1.0, "a2": 2.0, "a3": 3.0})
        self.assertEqual(set(requests), expected_flags)
        text = encoded(STEPS[2][0])
        self.assertEqual(stored, [(sha(text), len(text.encode("utf-8")))])
        # the working set, 3 processed ids, a record, a content, a link and an arrival per request, the tail
        # atom, and the meta keys an acceptance writes: the revision and the clock
        self.assertEqual(atoms, 1 + 3 + 4 * len(REQUESTS) + 1 + 2)
        # Three checkpoints, the second lost its reply: its request id was presented again by the retry on a new
        # connection (the server replayed it), and the writes carried one request id each.
        by_id = {}
        for item in proxy.ids:
            by_id[item.decode()] = by_id.get(item.decode(), 0) + 1
        self.assertEqual(len(by_id), 3, by_id)
        self.assertGreaterEqual(max(by_id.values()), 2, "the lost one was not presented again: %s" % by_id)
        written = {name for frame in proxy.publishes for name in re.findall(rb"(?:rq1|ck1|il1)\.[0-9a-f]+", frame)}
        self.assertEqual(len(written), len(REQUESTS) + 3)  # a write per accepted request, then the three
        self.assertGreaterEqual(proxy.connections, 4)

    def test_a_reply_lost_between_python_and_the_worker_repeats_the_same_request(self):
        self.session()
        original = CettaValueWorker.call
        requests, lost = [], [True]

        def lossy(worker, request):
            if request[2] != "checkpoint":
                return original(worker, request)
            requests.append(request)
            reply = original(worker, request)
            if lost[0]:
                lost[0] = False
                raise WorkerReplyUncertain("simulated: the reply was lost", request_id="x", payload=request)
            return reply

        store = Ledger(self.directory / "gate.db")
        store.accept("input:one", "m", kind="message")
        with mock.patch.object(CettaValueWorker, "call", lossy):
            store.checkpoint(working(4.0, lastresults="once"), ["u1"], "input:one")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1], "the repeated request must be the very same one")
        self.assertEqual(store.working_checkpoint(), working(4.0, lastresults="once"))
        notices, ids, stored, flags, atoms = parse_native(self.run_client("dump", native_dump_program(self.server.socket)))
        # the working set, the processed id, a record, a content, a link and an arrival, the tail atom, and the
        # revision and the clock
        self.assertEqual((ids, set(flags), len(stored), atoms), ({"u1": 4.0}, {"input:one"}, 1, 1 + 1 + 4 + 1 + 2))

    def test_a_retry_that_arrives_after_a_newer_checkpoint_is_a_replay_and_does_not_revert_it(self):
        # What content-derived request ids would get wrong: the retry of a lost reply comes after another
        # checkpoint changed the working set. The id belongs to the call, so the retry replays the first commit.
        self.session()
        original = CettaValueWorker.call
        late = []

        def interleave(worker, request):
            reply = original(worker, request)
            if request[2] == "checkpoint" and not late:
                late.append(request)
                input_ack.turn_checkpoint(encoded(working(9.0, lastresults="newer")), ["y"], "", 9.0, request_id="ck1.other")
                raise WorkerReplyUncertain("simulated: the reply was lost", request_id="x", payload=request)
            return reply

        store = Ledger(self.directory / "gate.db")
        with mock.patch.object(CettaValueWorker, "call", interleave):
            store.checkpoint(working(5.0, lastresults="mine"), ["z"], None)
        self.assertEqual(len(late), 1)
        self.assertEqual(input_ack.working_set(), encoded(working(9.0, lastresults="newer")))
        self.assertEqual(input_ack.committed(["y", "z"]), 1)


@unittest.skipUnless(available(), SKIP)
class ContentionTest(NativeCase):
    def test_checkpoints_racing_a_noisy_writer_commit_once_each(self):
        # The count-driven noise writer of the free-running test commits to another collection (14 commits, the
        # last 12 back to back) while this process checkpoints: each checkpoint commits once. The server runs the checkpoints next to the store, between the noise writer's publishes;
        # a client-side transaction that lost a race would run again with the same request id. The proxy counts
        # the writes: at least one per checkpoint.
        self.ensure()
        proxy = ProxyCheckpoint(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        gate, hold = self.directory / "go", self.directory / "hold"
        noisy = self.spawn("noise", noise_program(self.server.socket, gate, hold))
        time.sleep(1.0)  # connected and waiting at the gate
        self.release(gate.name)
        deadline = time.monotonic() + 60
        while "noise-span 0" not in noisy.output.read_text():
            self.assertLess(time.monotonic(), deadline, "the noise writer never committed")
            time.sleep(0.005)
        steps = [(working(float(n), lastresults="turn %d" % n), ["c%d" % n, "shared"], "input:one" if n == 0 else None)
                 for n in range(8)]
        with oracle_environment() as directory:
            NativeRunner.create(directory, proxy.path)
            store = Ledger(directory / "x.db")
            store.accept("input:one", "m", kind="message")
            for value, ids, request in steps:
                store.checkpoint(value, ids, request)
            self.assertEqual(store.working_checkpoint(), steps[-1][0])
        output = self.finish(noisy)
        self.assertIn("(noise-commits 14)", output)
        notices, ids, stored, requests, atoms = parse_native(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(ids, dict({"c%d" % n: float(n) for n in range(8)}, shared=0.0))
        self.assertEqual(set(requests), {"input:one"})
        self.assertEqual(len(stored), 1)
        if os.environ.get("INPUT_LEDGER_VERBOSE"):
            print("checkpoints %d, publishes %d (lost races repeated: %d)" % (len(steps), len(proxy.publishes),
                                                                              len(proxy.publishes) - len(steps)), file=sys.stderr)
        self.assertGreaterEqual(len(proxy.publishes), len(steps))


CHILD_CHECKPOINT = r"""
import json, os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import helper
helper._turn_commit = (("runtime:101", "102"), "input:one")
saved = helper.working_set_save(7, 3, "result \u00e9\u65e5 \"q\"", 12.5)
data = helper._working_read()
print(json.dumps({"saved": saved, "loops": data["loops"], "lastresults": data["lastresults"]}), flush=True)
os.kill(os.getpid(), signal.SIGKILL)   # the checkpoint committed; the transport ack never happens
"""

CHILD_RESTART = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import helper, input_ack, telegram
telegram.enqueue_runtime_event("again", identity="runtime:101")            # a runtime event the turn settled
telegram.enqueue_runtime_event("fresh", identity="runtime:never-settled")  # control: an unsettled one is presented
print(json.dumps({
    "working": helper._working_read(),
    "committed": [input_ack.already_committed(i) for i in ("runtime:101", "102", "103")],
    "gate": [input_ack.committed(["runtime:101", "102"]), input_ack.committed(["runtime:101", "103"])],
    "queued": [event.get("update_id") for event in telegram._metta_events],
    "ack_runtime": telegram.activity_source_ack(["runtime:101"]),
}), flush=True)
"""


@unittest.skipUnless(available(), SKIP)
class CrashTest(NativeCase):
    def environment(self, directory, native):
        env = {"PATH": os.environ["PATH"], "HOME": str(directory), "LANG": "C.UTF-8",
               "CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER),
               "METTACLAW_INSTANCE": "default", "METTACLAW_OBSERVED_STRATEGY": "1",
               "METTACLAW_STRATEGY_PATH": str(directory / "strategy"),
               "METTACLAW_WORKING_SET_PATH": str(directory / "working.json"),
               "METTACLAW_TELEGRAM_SERVICE_SOCKET": "", "METTACLAW_TELEGRAM_SERVICE_STATE_DIR": str(directory / "state"),
               "METTACLAW_ENGINE_STATE_PATH": str(directory / "engine"), "METTACLAW_TELEGRAM_LOG_PATH": str(directory / "u.jsonl")}
        if native:
            env[input_ack.LEDGER_SOCKET] = str(self.server.socket)
        return env

    def child(self, script, directory, native):
        return subprocess.run([sys.executable, "-c", script, str(ROOT / "src"), str(ROOT / "channels")],
                              env=self.environment(directory, native), cwd=directory, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)

    def crash_and_restart(self, native):
        directory = self.directory / "crash-native"
        directory.mkdir()
        # the request is a record of the native store the child checkpoints into
        with mock.patch.dict(os.environ, {input_ack.LEDGER_SOCKET: str(self.server.socket)}):
            Ledger(directory / "strategy").accept("input:one", "m", kind="message")
        first = self.child(CHILD_CHECKPOINT, directory, native)
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr[-2000:])
        report = json.loads(first.stdout.strip().splitlines()[-1])
        self.assertEqual(report["saved"], 1)
        second = self.child(CHILD_RESTART, directory, native)
        self.assertEqual(second.returncode, 0, second.stderr[-2000:])
        return report, json.loads(second.stdout.strip().splitlines()[-1])

    def test_a_crash_between_checkpoint_and_ack_leaves_the_input_processed(self):
        for native in (True,):
            report, restarted = self.crash_and_restart(native)
            # The working set is the checkpointed one, read at boot; the inputs count as processed (so the service
            # acknowledges them from the journal and never presents them again), the gate for the ack opens for the
            # settled ids and stays shut for an unsettled one.
            self.assertEqual(restarted["working"]["loops"], 7)
            self.assertEqual(restarted["working"]["lastresults"], 'result \u00e9\u65e5 "q"')
            self.assertEqual(restarted["committed"], [1, 1, 0])
            self.assertEqual(restarted["gate"], [1, 0])
            self.assertEqual(restarted["queued"], ["runtime:never-settled"], "a settled runtime event was presented again")
            self.assertEqual(restarted["ack_runtime"], 1)
        # The native store holds all four facts.
        notices, ids, stored, requests, atoms = parse_native(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(set(ids), {"runtime:101", "102"})
        self.assertEqual(set(requests), {"input:one"})
        self.assertEqual(len(stored), 1)

    def test_the_restarted_process_finds_the_checkpoint_even_when_the_server_restarted_too(self):
        directory = self.directory / "crash-server"
        directory.mkdir()
        with mock.patch.dict(os.environ, {input_ack.LEDGER_SOCKET: str(self.server.socket)}):
            Ledger(directory / "strategy").accept("input:one", "m", kind="message")
        first = self.child(CHILD_CHECKPOINT, directory, True)
        self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr[-2000:])
        self.assertEqual(self.server.stop(crash=True), -9)
        self.server.start()
        second = self.child(CHILD_RESTART, directory, True)
        self.assertEqual(second.returncode, 0, second.stderr[-2000:])
        restarted = json.loads(second.stdout.strip().splitlines()[-1])
        self.assertEqual((restarted["working"]["loops"], restarted["committed"]), (7, [1, 1, 0]))


@unittest.skipUnless(available(), SKIP)
class FailureMappingTest(NativeCase):
    def test_an_unreachable_server_raises_for_checkpoint_and_boot_read_and_writes_nothing_anywhere(self):
        import helper
        self.session()
        store = Ledger(self.directory / "gate.db")
        store.accept("input:one", "m", kind="message")
        os.environ["METTACLAW_WORKING_SET_PATH"] = str(self.directory / "working.json")
        self.server.stop()
        with self.assertRaises(input_ack.LedgerUnavailable) as raised:
            store.checkpoint(working(1.0), ["a"], "input:one")
        self.assertNotIsInstance(raised.exception, input_ack.LedgerBusy)
        self.assertIn("db:connect", str(raised.exception))
        # The boot read raises: an unavailable store is not an empty working set (and not the file's content).
        (self.directory / "working.json").write_text(json.dumps({"loops": 99}))
        with self.assertRaises(input_ack.LedgerUnavailable):
            store.working_checkpoint()
        with self.assertRaises(input_ack.LedgerUnavailable):
            helper._working_read()
        # working_set_save swallows every Exception: 0, so no ack follows, and the file projection is not
        # written either.
        helper._turn_commit = (("a",), "input:one")
        self.addCleanup(setattr, helper, "_turn_commit", None)
        with mock.patch.object(helper, "_working_read", return_value={}):
            self.assertEqual(helper.working_set_save(1, 1, "x", 1.0), 0)
        self.assertEqual(json.loads((self.directory / "working.json").read_text()), {"loops": 99})
        # Back up: nothing was half applied, and the first checkpoint now commits.
        self.server.start()
        self.assertIsNone(input_ack.working_set())
        self.assertEqual(input_ack.committed(["a"]), 0)
        store.checkpoint(working(2.0), ["a"], "input:one")
        self.assertEqual(input_ack.committed(["a"]), 1)

    def test_a_lost_race_is_busy_and_other_failures_are_unavailable(self):
        class Stub:
            lock = threading.RLock()
            timeout = 1

            def __init__(self, reply):
                self.reply = reply

            def call(self, request):
                return self.reply

        conflict = E("answer", E("Error", S("db:transaction"), E("StoreRefused", S("conflict"))))
        refused = E("answer", E("Error", S("db:transaction"), E("StoreRefused", S("closed"))))
        uncertain = E("answer", E("Error", S("input-ledger:checkpoint"),
                                  E("db:uncertain", "ck1.x", S("inputs"), E("input-ledger:checkpoint-digest", "d"))))
        self.session()
        store = Ledger(self.directory / "gate.db")
        with mock.patch.object(input_ack, "_ledger_worker", return_value=Stub(conflict)):
            with self.assertRaises(input_ack.LedgerBusy) as raised:
                store.checkpoint(working(1.0), [], None)
        for reply in (refused, uncertain):
            with mock.patch.object(input_ack, "_ledger_worker", return_value=Stub(reply)):
                with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                    store.checkpoint(working(1.0), [], None)
                self.assertNotIsInstance(raised.exception, input_ack.LedgerBusy)
        # The boot read: an Error answer raises, a malformed answer raises, never None.
        for reply in (refused, E("answer", 5)):
            with mock.patch.object(input_ack, "_ledger_worker", return_value=Stub(reply)):
                with self.assertRaises(input_ack.LedgerUnavailable):
                    store.working_checkpoint()

    def test_a_snapshot_that_cannot_be_read_is_an_error_for_the_boot_read_not_an_empty_working_set(self):
        self.ensure()
        self.session(self.server.socket)
        store = Ledger(self.directory / "gate.db")
        store.checkpoint(working(1.0, lastresults="kept"), ["a"], None)

        class CutEveryCapture(RecordingProxy):
            def frame(self, connection):
                data = RecordingProxy.frame(self, connection)
                # every read (a snapshot of a client, or a ledger verb run by the server) loses its connection
                return None if data and (b"DbCapture" in data or b"LedgerCall" in data) else data

        proxy = CutEveryCapture(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        os.environ[input_ack.LEDGER_SOCKET] = str(proxy.path)
        with self.assertRaises(input_ack.LedgerUnavailable):
            input_ack.working_set()
        with self.assertRaises(input_ack.LedgerUnavailable):
            store.working_checkpoint()
        with self.assertRaises(input_ack.LedgerUnavailable):
            input_ack.committed(["a"])
        os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
        self.assertEqual(store.working_checkpoint(), working(1.0, lastresults="kept"))

    def test_a_working_set_of_megabytes_is_stored_and_read_back_byte_for_byte(self):
        # The ledger's server and worker take values of input_ack.LEDGER_VALUE_BYTES; the engine's default is 1 MiB.
        self.session()
        store = Ledger(self.directory / "gate.db")
        store.accept("input:one", "m", kind="message")
        big = working(2.0, lastresults="z\u00e9" * 1500000)  # about 4.5 MB
        store.checkpoint(big, ["big"], "input:one")
        self.assertEqual(store.working_checkpoint(), big)
        self.assertEqual(input_ack.committed(["big"]), 1)

    def test_a_working_set_beyond_the_servers_allowance_is_refused_loudly_atomically_and_the_store_goes_on(self):
        # A server started with the engine's default allowance (1 MiB) refuses a larger value; nothing is written.
        self.server.stop()
        self.server = DbServer(self.directory / "small", value_bytes=1048576)
        (self.directory / "small").mkdir()
        self.server.start()
        self.session()
        store = Ledger(self.directory / "gate.db")
        store.accept("input:one", "m", kind="message")
        store.checkpoint(working(1.0, lastresults="small"), ["kept"], None)
        overhead = len(encoded(working(2.0, lastresults="")).encode("utf-8"))
        for size in (1048500, 1100000, 1300000):  # just over (an engine Error), and over (no answer at all)
            huge = working(2.0, lastresults="x" * (size - overhead))
            self.assertEqual(len(encoded(huge).encode("utf-8")), size)
            with self.assertRaises(input_ack.LedgerUnavailable) as raised:
                store.checkpoint(huge, ["lost"], "input:one")
            self.assertIn("beyond what the native store's server accepts", str(raised.exception))
            self.assertIn("nothing was written", str(raised.exception))
            self.assertEqual(store.working_checkpoint(), working(1.0, lastresults="small"))
            self.assertEqual(input_ack.committed(["kept"]), 1)
            self.assertEqual(input_ack.committed(["lost"]), 0)
        notices, ids, stored, requests, atoms = parse_native(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual((set(ids), requests), ({"kept"}, {}))
        self.assertEqual(len(self.worker_pids), 1, "the worker had to be replaced")
        store.checkpoint(working(3.0, lastresults="y" * 900000), ["after"], "input:one")  # under the allowance
        self.assertEqual(store.working_checkpoint()["lastresults"], "y" * 900000)
        self.assertEqual(input_ack.committed(["after"]), 1)

if __name__ == "__main__":
    unittest.main()
