"""scripts/observe_shown_history.py marks the receipts carried before the conversation window.

Four batches on the native ledger, each of two calls and a receipt; the second batch's last call was
interrupted, and the window starts after the second batch's entries. A dry run marks nothing; --apply marks
the first batch, whose calls all lie before the start, and not the interrupted one (the rule an answered
prompt uses) nor the two after the start. A second run finds nothing left.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "scripts")]
import input_ack
from observed_strategy import Ledger
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import available, oracle_environment
from test_turn_effect_native import ROUTER, record_attempt
import task_context
import observe_shown_history


@unittest.skipUnless(available(), SKIP)
class ObserveShownHistoryTest(NativeCase):
    def run_script(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(observe_shown_history.main(["--socket", str(self.server.socket), *flags]), 0)
        return json.loads(out.getvalue())

    def test_the_calls_before_the_window_count_as_carried(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            store.accept("r1", "work")
            task_context.begin(store, "r1", "operator-event", None, "chat")
            tickets = []
            for number in range(4):
                snap = store.capture("r1", "b%d" % number, ROUTER, 0)
                commands = [["shell", "ls %d" % number], ["shell", "cat %d" % number]]
                ticket = store.decide(snap["serial"], {"kind": "command", "tool": "batch",
                                                       "command": {"commands": commands, "limit": 2}}, 0)
                task_context.proposal(store, snap["serial"], "r1", commands, "admitted")
                record_attempt(store, True, ticket, 0, commands[0], {"disposition": "returned", "result": "a"})
                second = None if number == 1 else {"disposition": "returned", "result": "b"}
                record_attempt(store, True, ticket, 1, commands[1], second)   # batch 1 was interrupted
                store.report(ticket, "batch %d" % number)
                tickets.append(ticket)
            owner = store.projection()["meta"]["owner"]
            # Entries: the input, then two calls per batch: the window starts at the third batch.
            key = "task_window:conversation:" + owner
            input_ack._ledger_call("meta-put", (key, json.dumps({"version": 1, "start": 5, "summary": ""}),
                                                "window-1"))
            input_ack.close_ledger()
            dry = self.run_script()
            self.assertEqual((dry["window"], dry["start"], dry["applied"], dry["tickets"]),
                             (key, 5, False, [tickets[0]]))
            observed = {a["ticket"]: a["observed"] for a in store.projection()["actions"]}
            self.assertEqual(sorted(observed.values()), [False] * 4)
            applied = self.run_script("--apply")
            self.assertEqual(applied["tickets"], [tickets[0]])
            observed = {a["ticket"]: a["observed"] for a in store.projection()["actions"]}
            self.assertEqual(observed, {tickets[0]: True, tickets[1]: False, tickets[2]: False, tickets[3]: False})
            self.assertEqual(self.run_script("--apply")["tickets"], [])   # nothing left to mark
            input_ack.close_ledger()


if __name__ == "__main__":
    unittest.main()
