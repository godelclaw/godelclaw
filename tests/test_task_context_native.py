"""The task context in the native ledger (METTACLAW_INPUT_LEDGER_SOCKET).

task_context's frames, coverage of input batches, proposals and notes, the request inputs of
observed_strategy.Ledger and every task_context reader (current, resume, projection, the
conversation, read, history, compact) keep their records in the same native collection as the
requests and the turns. The oracle is the same sequence of calls on the SQLite path: every answer,
refusal and projection is compared with the owner normalized.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
from pathlib import Path
import sqlite3
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
from test_input_ledger_remote import available, oracle_environment
from test_turn_effect_native import NativeSides, ROUTER, record_attempt, presented


def raises(function, *arguments, **options):
    try:
        return ("returned", function(*arguments, **options))
    except (ValueError, IdentityConflict) as error:
        return ("raised", type(error).__name__, str(error))


def contexts(store, native, note):
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
    record_attempt(store, native, ticket, 0, ["ls"], {"disposition": "returned", "result": "a b"})
    record_attempt(store, native, ticket, 1, ["cat", "f"], None)
    store.report(ticket, "batch done")
    later = store.capture("input:b", "b2", ROUTER, 0)
    task_context.proposal(store, later["serial"], "input:b", [["echo", "hi"]], "deferred")
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
    def test_every_task_context_reader_and_writer_matches_sqlite(self):
        oracle, native = self.compare(contexts)
        steps = dict(oracle)
        self.assertEqual(steps["covered"][:2], ["covered", "active"], "the same-chat batch is covered, the other is not")
        self.assertEqual(steps["proposal-conflict"][0], "raised")

    def test_reads_and_updates_touch_their_own_records_and_not_the_store(self):
        """Every pattern with a symbol head can use its family's head index; a lookup by id must still use
        the id index, and replacing a record (here the focus) must not scan the collection (the engine's
        indexed removal). A candidate read through the server is one DbCandidateNextV1 request; the 60
        requests hold about 300 atoms."""
        proxy = CountingProxy(self.directory / "count.sock", self.server.socket)
        with oracle_environment() as directory:
            NativeRunner.create(directory, proxy.path)
            store = Ledger(directory / "x.db")
            for number in range(60):
                store.accept("r%d" % number, "work %d" % number)
            task_context.begin(store, "r1", "tick", None, "")
            proxy.steps = 0
            self.assertEqual(store.status_of("r7"), "active")
            self.assertLessEqual(proxy.steps, 4, "one request's record, not the 60 requests'")
            proxy.steps = 0
            task_context.begin(store, "r7", "tick", None, "")
            self.assertEqual(task_context.resume(store, "r7"), "r7")
            self.assertLessEqual(proxy.steps, 30, "a frame, the focus and a resume touch their own records")
            input_ack.close_ledger()


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
