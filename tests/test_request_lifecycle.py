"""Delivery, retained commitments, workflow disposition and bounded context."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger

ROUTER = dict(advertised=["tool"], executable=["tool"], permitted=["tool"])


class RequestLifecycleTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "ledger.db"
        self.store = Ledger(self.path)

    def test_processing_drains_input_without_certifying_task(self):
        self.store.accept("input:1", "a question")
        self.store.accept("work", "unfinished task")
        self.assertTrue(self.store.processed("input:1"))
        self.assertFalse(self.store.processed("input:1"))
        self.assertFalse(self.store.processed("work"))
        self.assertEqual(self.store.request_record("input:1")["status"], "processed")
        self.assertEqual([r["id"] for r in self.store.active()], ["work"])
        self.assertFalse(any(r["status"] == "achieved" for r in self.store.projection()["requests"]))

    def test_processed_focus_can_continue_and_delayed_effect_is_retained(self):
        self.store.accept("input:1", "multi-step work")
        sample = self.store.capture("input:1", "context", ROUTER, 0)
        owner = sample["owner"]
        ticket = self.store.decide(sample["serial"], dict(kind="command", owner=owner,
            request="input:1", tool="tool", command=["tool"]), 0)
        self.store.processed("input:1")
        self.store.restore()
        self.assertEqual(self.store.projection()["actions"][0]["status"], "uncertain")
        self.assertIsNotNone(self.store.capture("input:1", "context", ROUTER, 0))
        self.store.report(ticket, "actual late receipt")
        receipt = self.store.protected("input:1")["results"][0]
        self.store.observe([ticket], {ticket: [receipt]})
        self.assertIsNotNone(self.store.capture("input:1", "context", ROUTER, 0))

    def test_retained_work_has_explicit_dispositions_and_reopens(self):
        self.store.accept("input:1", "needed repair")
        self.store.processed("input:1")
        self.assertTrue(self.store.disposition("input:1", "keep", "Close after repair receipt is checked"))
        self.assertEqual(self.store.request_record("input:1")["kind"], "task")
        self.store.disposition("input:1", "waiting-user", "awaiting a preference")
        self.assertFalse(self.store.active())
        self.store.disposition("input:1", "reopen", "preference supplied")
        self.store.disposition("input:1", "addressed", "reply and result reviewed")
        record = Ledger(self.path).request_record("input:1")
        self.assertEqual(record["status"], "addressed")
        self.assertNotEqual(record["status"], "achieved")
        self.assertEqual(record["content"], "needed repair")

    def test_retaining_work_before_input_ack_does_not_lose_the_commitment(self):
        self.store.accept("input:1", "multi-step commitment")
        self.store.disposition("input:1", "keep", "Close after reviewing the repair")
        self.assertTrue(self.store.processed("input:1"))
        record = self.store.request_record("input:1")
        self.assertEqual((record["kind"], record["status"], record["processed"]), ("task", "active", 1))

    def test_workflow_cannot_override_a_configured_checker_or_goal(self):
        self.store.accept("verified", "exact output", "sha256:expected")
        self.store.accept("goal:1", "a scheduled goal")
        self.assertFalse(self.store.disposition("verified", "addressed", "looks done"))
        self.assertFalse(self.store.disposition("goal:1", "addressed", "looks done"))
        with self.assertRaises(ValueError):
            self.store.disposition("verified", "achieved", "looks done")

    def test_waiting_and_bounded_views_do_not_erase_old_work(self):
        for i in range(200): self.store.accept("task:" + str(i), "unique:" + str(i) + "x" * 2000)
        view = self.store.protected("task:0")
        self.assertEqual(len(view["unfinished"]), 8)
        self.assertEqual(view["unfinished"][0]["id"], "task:0")
        self.assertEqual(view["omitted_active"], 192)
        self.assertLess(len(json.dumps(view)), 6000)
        seen, offset = set(), 0
        while offset is not None:
            page = self.store.request_page(offset)
            seen.update(r["id"] for r in page["records"]); offset = page["next_offset"]
        self.assertEqual(len(seen), 200)
        self.assertIn("unique:0", self.store.request_record("task:0")["content"])

    def test_processing_is_not_auto_applied_to_unproven_legacy_inputs(self):
        self.store.accept("input:legacy", "old unprocessed input")
        with sqlite3.connect(self.path) as db: db.execute("DELETE FROM request_context")
        reopened = Ledger(self.path)
        self.assertEqual(reopened.request_record("input:legacy")["status"], "active")
        self.assertEqual(reopened.request_record("input:legacy")["processed"], 0)


if __name__ == "__main__": unittest.main()


class RequestIdentityTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "observed-strategy.sqlite3"
        self.store = Ledger(self.path)

    def run_host(self, *args):
        import strategy_host as host
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1",
                                          "METTACLAW_STRATEGY_PATH": str(self.path),
                                          "METTACLAW_INSTANCE": "godel"}), \
                mock.patch.object(host, "_ledger", None), mock.patch.object(host, "_booted", False):
            return host.request_disposition(*args)

    def test_refusals_say_why(self):
        self.assertIn("unknown ID nope", self.run_host("nope", "keep", "x"))
        self.store.accept("goal:o:1:0", "Goal g, cycle 0.")
        self.assertIn("retire the goal with goal-drop", self.run_host("goal:o:1:0", "defer", "later"))
        self.store.accept("input:fresh", "hello", kind="message")
        self.assertIn("addressed only after a turn has processed it",
                      self.run_host("input:fresh", "addressed", "done"))
