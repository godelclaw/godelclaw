"""The task context in the native ledger (METTACLAW_INPUT_LEDGER_SOCKET).

task_context's frames, coverage of input batches, proposals and notes, the request inputs of
observed_strategy.Ledger and every task_context reader (current, resume, projection, the
conversation, read, history, compact) keep their records in the same native collection as the
requests and the turns. The scenario asserts its answers, refusals and projections, with the
owner normalized.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from observed_strategy import IdentityConflict, Ledger
import task_context
from native_ledger_client import NativeRunner
from test_input_ack_native import RecordingProxy, SKIP
from test_input_ledger_remote import PRELUDE, available, oracle_environment
from test_input_ledger import literal
from test_turn_effect_native import NativeSides, ROUTER, record_attempt, presented


def raises(function, *arguments, **options):
    try:
        return ("returned", function(*arguments, **options))
    except (ValueError, IdentityConflict) as error:
        return ("raised", type(error).__name__, str(error))


def contexts(store, note):
    """Frames, coverage, resumption, proposals, notes and every projection."""
    note("accept", [store.accept("input:a", "first message", kind="message"),
                    store.accept("input:b", "first and second", kind="message"),
                    store.accept("input:c", "another chat", kind="message"),
                    store.accept("task-1", "child work"),
                    store.accept("waiting", "needs a reply")])
    note("links", [raises(store.link_inputs, "input:a", ["u1"]), raises(store.link_inputs, "input:b", ["u2", "u1"]),
                   raises(store.link_inputs, "input:c", ["u1"]), raises(store.link_inputs, "input:b", ["u3"]),
                   raises(store.link_inputs, "task-1", ["u9"]), raises(store.link_inputs, "task-1", ["u8"])])
    note("linked", [store.requests_with_input("u1"), store.requests_with_input("u9"), store.requests_with_input("none")])
    note("current-0", task_context.current(store))
    task_context.begin(store, "input:a", "operator-event", None, "chat-1")
    task_context.begin(store, "input:c", "operator-event", "input:a", "chat-2")
    task_context.begin(store, "input:b", "operator-event", "input:c", "chat-1")
    note("covered", [store.status_of("input:a"), store.status_of("input:c"), task_context.current(store)])
    note("resume", [task_context.resume(store, "input:a"), task_context.resume(store, "nothing"),
                    task_context.resume(store, None)])
    store.disposition("waiting", "waiting-user", "asked")
    task_context.begin(store, "waiting", "operator-event", None, "")
    task_context.begin(store, "task-1", "continuation-tick", "waiting", "")
    note("parents", [raises(task_context.begin, store, "task-2", "x", None, "", "ghost"),
                     task_context.resume(store, "task-1")])
    task_context.focus(store, "task-1")
    note("current-1", task_context.current(store))
    snap = store.capture("task-1", "b1", ROUTER, 0)
    ticket = store.decide(snap["serial"], {"kind": "command", "tool": "batch",
                                           "command": {"commands": [["ls"], ["cat", "f"]], "limit": 2}}, 0)
    task_context.proposal(store, snap["serial"], "task-1", [["ls"], ["cat", "f"]], "admitted")
    task_context.proposal(store, snap["serial"], "task-1", [["ls"], ["cat", "f"]], "admitted")
    note("proposal-conflict", raises(task_context.proposal, store, snap["serial"], "task-1", [["rm"]], "admitted"))
    record_attempt(store, ticket, 0, ["ls"], {"disposition": "returned", "result": "a b"})
    record_attempt(store, ticket, 1, ["cat", "f"], None)
    store.report(ticket, "batch done")
    later = store.capture("input:b", "b2", ROUTER, 0)
    task_context.proposal(store, later["serial"], "input:b", [["echo", "hi"]], "deferred")
    # An input that arrives after the calls: the conversation orders the calls before it by their captures.
    note("late", store.accept("input:late", "after the calls", kind="message"))
    owner = store.projection()["meta"]["owner"]
    first_call = "%s:%s:0" % (owner, snap["serial"])
    note("note", [raises(task_context.note, store, "task-1", "it listed", '["%s"]' % first_call),
                  raises(task_context.note, store, "ghost", "x"),
                  raises(task_context.note, store, "task-1", "  "),
                  raises(task_context.note, store, "task-1", "bad ref", '["%s:999:0"]' % owner),
                  raises(task_context.note, store, "waiting", "parent note")])
    note("projection-task", task_context.projection(store, "task-1"))
    note("projection-batch", task_context.projection(store, "input:a"))
    note("conversation", task_context.projection(store, "conversation:" + owner))
    note("conversation-other", raises(task_context.projection, store, "conversation:someone-else"))
    note("read", [task_context.read(store, first_call), task_context.read(store, "%s:%s:1" % (owner, snap["serial"])),
                  task_context.read(store, "%s:%s:0" % (owner, later["serial"])),
                  task_context.read(store, "task-input:input:a"), task_context.read(store, "task-frame:task-1", 0, 300),
                  raises(task_context.read, store, "other:%s:0" % snap["serial"]),
                  raises(task_context.read, store, "%s:%s:5" % (owner, snap["serial"]))])
    note("history", [task_context.history(store, "task-1"), task_context.history(store, "waiting", 1, 1)])
    note("compact", task_context.compact(task_context.conversation_projection(store, "task-1"), 1200))


@unittest.skipUnless(available(), SKIP)
class TaskContextNativeTest(NativeSides):
    def test_every_task_context_reader_and_writer(self):
        steps = dict(self.steps(contexts))
        self.assertEqual(steps["covered"][:2], ["covered", "active"], "the same-chat batch is covered, the other is not")
        self.assertEqual(steps["proposal-conflict"][0], "raised")

    def test_reads_and_updates_touch_their_own_records_and_not_the_store(self):
        """The store answers a pattern from one index, and the index keys only strings and symbols (a number at an
        indexed position would make every lookup read every record of its arity). So every field the ledger looks
        up by is a string or a symbol, and a lookup or an update reads its own records whatever else the store
        holds. The verbs run here as a client over db:connect, so that each candidate the store reads crosses the
        proxy as one DbCandidateNextV1; the server's own calls choose the same indexes."""
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            store = Ledger(directory / "x.db")
            for number in range(60):
                key = "r%d" % number
                store.accept(key, "work %d" % number)
                snap = store.capture(key, "b", ROUTER, 0)
                ticket = store.decide(snap["serial"], {"kind": "command", "tool": "t", "command": [number]}, 0)
                task_context.proposal(store, snap["serial"], key, [[number]], "admitted")
                store.report(ticket, "out %d" % number)
                store.observe([ticket], presented(store, ticket))
            task_context.begin(store, "r1", "tick", None, "")
            owner = store.projection()["meta"]["owner"]
            input_ack.close_ledger()
        self.assertGreater(input_ack_census(self), 500, "the store holds the records of 60 turns")
        proxy = CountingProxy(self.directory / "count.sock", self.server.socket)
        self.addCleanup(proxy.close)
        ticket = '"%s:7"' % owner
        for verb, arguments, bound in (("request-get", '("r7" none)', 4),
                                       ("actions", "((ticket %s))" % ticket, 8),
                                       ("task-family", '("r7")', 22),
                                       ("task-resume", '("r7")', 14),
                                       ("task-call", "(7 %s)" % ticket, 6),
                                       ("task-view", '(("r7"))', 66),
                                       ("task-begin", '("r7" "tick" none "" none "rq1.cost")', 30),
                                       ("decide", '(999 "text" "" "" "rq1.cost2")', 4)):
            proxy.steps = 0
            out = self.run_client(verb, PRELUDE + '!(let* (($db (db:connect %s)) ($a (input-ledger:perform $db %s %s))'
                                  ' ($_ (db:close $db))) (println! (got $a)))\n' % (literal(str(proxy.path)), literal(verb), arguments))
            self.assertIn("(got ", out, verb)
            self.assertNotIn("(got (Error", out, verb)
            # The same counts hold at 20 turns as at 60: about twice each is allowed.
            self.assertLessEqual(proxy.steps, bound, "%s read %d candidates" % (verb, proxy.steps))


def input_ack_census(case):
    """The number of atoms of any kind in the case's store."""
    case.session(case.server.socket)
    census = input_ack._ledger_call("census", ())
    return census[1] + census[2] + census[3] + census[4]


class CountingProxy(RecordingProxy):
    """RecordingProxy that counts the candidate reads (DbCandidateNextV1 requests) it relays."""
    def __init__(self, path, upstream):
        self.steps = 0
        super().__init__(path, upstream)

    def frame(self, connection):
        data = super().frame(connection)
        if data and b"DbCandidateNextV1" in data:
            self.steps += 1
        return data

if __name__ == "__main__":
    unittest.main()
