"""Main-call capacity, dead-process uncertainty and retained stale evidence."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm_policy
import native_lifetime


class NativeLifetimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        patch = mock.patch.dict(os.environ, {"METTACLAW_LLM_LEDGER_PATH": str(Path(temporary.name) / "budget.db"),
                                "METTACLAW_LLM_POLICY": "upstream", "METTACLAW_LLM_POLICY_PATH": ""})
        patch.start(); self.addCleanup(patch.stop)

    def plan(self):
        return SimpleNamespace(correlation=llm_policy.admit("synthetic", 10), epoch="session", guard="revision", cap=1024)

    def test_soft_wait_does_not_allow_second_main_call(self):
        plan = self.plan()
        self.assertTrue(native_lifetime.claim(plan))
        native_lifetime.submitted(plan, 3)
        native_lifetime.progress(plan, 10, 100, True)
        other = self.plan()
        self.assertFalse(native_lifetime.claim(other))
        self.assertTrue(native_lifetime.settle(plan, "completed", "evidence"))
        self.assertTrue(native_lifetime.claim(other))

    def test_settlement_records_elapsed_soft_wait_even_between_polls(self):
        for elapsed, already_observed, expected in ((.02, 0, 0), (.2, 0, 1), (.02, 1, 1)):
            with self.subTest(elapsed=elapsed, already_observed=already_observed):
                plan = self.plan()
                plan.submitted_at, plan.soft = 100.0, 50
                self.assertTrue(native_lifetime.claim(plan))
                native_lifetime.submitted(plan, 3)
                if already_observed:
                    native_lifetime.progress(plan, 0, 0, 1)
                with mock.patch.object(native_lifetime.time, "monotonic", return_value=100.0 + elapsed):
                    self.assertTrue(native_lifetime.settle(plan, "completed", "reply"))
                with native_lifetime.transaction() as db:
                    self.assertEqual(db.execute("SELECT soft_elapsed FROM native_calls WHERE id=?", (plan.correlation,)).fetchone()[0], expected)

    def test_killed_sent_call_records_uncertainty_and_keeps_reservation(self):
        plan = self.plan(); native_lifetime.claim(plan); native_lifetime.submitted(plan, 3)
        with mock.patch.object(native_lifetime, "process_start", return_value=None):
            native_lifetime.recover()
        self.assertEqual(native_lifetime.status()[0]["state"], "uncertain")
        self.assertIn("remote-outcome-unknown", native_lifetime.result(plan.correlation))
        self.assertEqual(llm_policy.snapshot()["accounted_output_tokens"], 10)
        with llm_policy.transaction() as db:
            self.assertEqual(db.execute("SELECT status FROM requests WHERE id=?", (plan.correlation,)).fetchone()[0], "unknown")

    def test_failed_submitted_request_cannot_keep_live_process_lease(self):
        plan = self.plan()
        self.assertTrue(native_lifetime.claim(plan))
        native_lifetime.submitted(plan, 7)
        self.assertTrue(llm_policy.fail(plan.correlation, sent=True))
        with native_lifetime.transaction() as db:
            db.execute("UPDATE native_calls SET updated=0 WHERE id=?",
                       (plan.correlation,))
        native_lifetime.recover()
        row = native_lifetime.status()[0]
        self.assertEqual(row["state"], "uncertain")
        self.assertEqual(row["reason"],
                         "terminal-request-remote-outcome-unknown")
        with llm_policy.transaction() as db:
            request = db.execute(
                "SELECT status,finish FROM requests WHERE id=?",
                (plan.correlation,),
            ).fetchone()
        self.assertEqual(request, ("unknown", "transport-unknown"))
        self.assertTrue(native_lifetime.claim(self.plan()))

    def test_failed_unsubmitted_request_releases_claim_as_failed(self):
        plan = self.plan()
        self.assertTrue(native_lifetime.claim(plan))
        self.assertTrue(llm_policy.fail(plan.correlation, sent=False))
        with native_lifetime.transaction() as db:
            db.execute("UPDATE native_calls SET updated=0 WHERE id=?",
                       (plan.correlation,))
        native_lifetime.recover()
        row = native_lifetime.status()[0]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["reason"], "request-failed-before-submission")

    def test_completed_request_releases_active_lease_after_grace(self):
        plan = self.plan()
        self.assertTrue(native_lifetime.claim(plan))
        native_lifetime.submitted(plan, 9)
        result = {"choices": [{"finish_reason": "stop"}],
                  "usage": {"output_tokens": 1}}
        self.assertTrue(llm_policy.complete(plan.correlation, result))
        with native_lifetime.transaction() as db:
            db.execute("UPDATE native_calls SET updated=0 WHERE id=?",
                       (plan.correlation,))
        native_lifetime.recover()
        self.assertEqual(native_lifetime.status()[0]["state"], "completed")

    def test_scoped_calls_reserve_interactive_capacity(self):
        first = self.plan(); first.activity = "long-work"; first.lane = "background"
        second = self.plan(); second.activity = "more-work"; second.lane = "background"
        reply = self.plan(); reply.activity = "conversation"; reply.lane = "interactive"
        with mock.patch.dict(os.environ, {"METTACLAW_ACTIVITY_CALL_CAPACITY": "2",
                                         "METTACLAW_ACTIVITY_INTERACTIVE_RESERVE": "1"}):
            self.assertTrue(native_lifetime.claim(first))
            self.assertFalse(native_lifetime.claim(second))
            self.assertTrue(native_lifetime.claim(reply))
            self.assertFalse(native_lifetime.claim(self.plan()))
            duplicate = self.plan(); duplicate.activity = "conversation"; duplicate.lane = "interactive"
            self.assertFalse(native_lifetime.claim(duplicate))
            self.assertTrue(native_lifetime.settle(reply, "completed", "owned reply"))
            self.assertTrue(native_lifetime.claim(duplicate))

    def test_transport_uncertainty_does_not_block_fresh_request(self):
        with mock.patch.dict(os.environ, {"METTACLAW_LLM_POLICY": "managed"}):
            plan = self.plan()
            with llm_policy.transaction() as db:
                db.execute("UPDATE requests SET fingerprint='captured-request' WHERE id=?", (plan.correlation,))
            self.assertTrue(native_lifetime.claim(plan)); native_lifetime.submitted(plan, 3)
            self.assertTrue(native_lifetime.settle(plan, "uncertain", reason="transport-error"))
            new=llm_policy.admit("synthetic",10,request_fingerprint="captured-request")
            self.assertNotEqual(new, plan.correlation)
            self.assertEqual(llm_policy.snapshot()["accounted_output_tokens"],20)

    def test_zombie_process_cannot_keep_a_native_call_claim_alive(self):
        fields = ["Z"] + ["0"] * 18 + ["process-birth"]
        with mock.patch.object(Path, "read_text", return_value="42 (fixture) " + " ".join(fields)):
            self.assertIsNone(native_lifetime.process_start(42))
        fields[0] = "S"
        with mock.patch.object(Path, "read_text", return_value="42 (fixture) " + " ".join(fields)):
            self.assertEqual(native_lifetime.process_start(42), "process-birth")

    def test_correlated_late_reply_is_retained_and_settles_once(self):
        plan = self.plan(); native_lifetime.claim(plan)
        self.assertTrue(native_lifetime.settle(plan, "late", "old evidence", "captured-authority-changed"))
        self.assertFalse(native_lifetime.settle(plan, "completed", "replacement"))
        self.assertIn("old evidence", native_lifetime.result(plan.correlation))
        foreign = SimpleNamespace(**{**vars(plan), "epoch": "other"})
        self.assertFalse(native_lifetime.settle(foreign, "completed", "foreign"))

    def test_terminal_retention_is_bounded_and_never_prunes_pending(self):
        for _ in range(native_lifetime.EVIDENCE_LIMIT + 3):
            plan = self.plan(); native_lifetime.claim(plan)
            native_lifetime.settle(plan, "completed", "fixture evidence")
        pending = self.plan(); native_lifetime.claim(pending)
        with native_lifetime.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_calls WHERE result IS NOT NULL").fetchone()[0],
                             native_lifetime.EVIDENCE_LIMIT)
            db.execute("UPDATE native_calls SET updated=0 WHERE state='completed'")
            db.execute("UPDATE native_calls SET updated=0 WHERE id=?", (pending.correlation,))
        with native_lifetime.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_calls").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT state FROM native_calls").fetchone()[0], "claimed")

    def test_late_evidence_has_bounded_view_and_one_durable_wake(self):
        plan = self.plan(); native_lifetime.claim(plan)
        full = "quoted evidence " * 200
        native_lifetime.settle(plan, "late", full)
        import json
        value = json.loads(native_lifetime.result(plan.correlation))
        self.assertLessEqual(len(native_lifetime.result(plan.correlation)), 2400)
        self.assertTrue(value["truncated"])
        self.assertFalse(value["action_authority"])
        self.assertIn(plan.correlation, native_lifetime.evidence_context())
        failing = mock.Mock(side_effect=RuntimeError("channel unavailable"))
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(enqueue_runtime_event=failing)}):
            native_lifetime.notify()  # a missed wake is tried again
        enqueue = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(enqueue_runtime_event=enqueue)}):
            native_lifetime.notify(); native_lifetime.notify()
            enqueue.assert_called_once()
        # A restart does not announce it again: the same notice would name an
        # input that was already addressed. The context listing stays.
        self.assertIn(plan.correlation, native_lifetime.evidence_context())

    def test_outcomes_from_before_the_notified_column_count_as_announced(self):
        late = self.plan(); native_lifetime.claim(late)
        native_lifetime.settle(late, "late", "evidence")
        pending = self.plan()
        with native_lifetime.transaction() as db:
            db.execute("ALTER TABLE native_calls DROP COLUMN notified")
            db.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state) "
                       "VALUES (?,?,0,'gone',1,1,'submitted')", (pending.correlation, "old"))
        enqueue = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(enqueue_runtime_event=enqueue)}):
            native_lifetime.notify()
        # Only the call interrupted by the dead process is announced.
        enqueue.assert_called_once()
        self.assertIn(pending.correlation, enqueue.call_args[0][0])
        self.assertIn("uncertain", enqueue.call_args[0][0])

    def test_compaction_purpose_is_durable_but_not_agent_evidence(self):
        plan = self.plan(); plan.purpose = "compaction"
        self.assertTrue(native_lifetime.claim(plan))
        native_lifetime.submitted(plan, 3)
        with mock.patch.object(native_lifetime, "process_start", return_value=None):
            native_lifetime.recover()
        self.assertEqual(native_lifetime.status(), [])
        self.assertEqual(native_lifetime.evidence_context(), "")
        receipt = __import__("json").loads(native_lifetime.result(plan.correlation))
        self.assertEqual((receipt["state"], receipt["purpose"], receipt["result"]),
                         ("abandoned", "compaction", None))
        with native_lifetime.transaction() as db:
            self.assertEqual(db.execute("SELECT status FROM requests WHERE id=?", (plan.correlation,)).fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT count(*) FROM requests WHERE id=?", (plan.correlation,)).fetchone()[0], 1)
        enqueue = mock.Mock()
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(enqueue_runtime_event=enqueue)}):
            native_lifetime.notify()
        enqueue.assert_not_called()

    def test_internal_calls_do_not_hide_old_turn_evidence_at_limit(self):
        turn = self.plan(); self.assertTrue(native_lifetime.claim(turn))
        native_lifetime.settle(turn, "uncertain", reason="fixture")
        for _ in range(15):
            internal = self.plan(); internal.purpose = "coding"
            self.assertTrue(native_lifetime.claim(internal))
            native_lifetime.settle(internal, "completed", "internal private output")
        self.assertEqual([r["id"] for r in native_lifetime.status()], [turn.correlation])
        self.assertIn(turn.correlation, native_lifetime.evidence_context())
        self.assertNotIn("internal private output", native_lifetime.result(internal.correlation))

    def test_internal_active_calls_still_own_admission(self):
        internal = self.plan(); internal.purpose = "compaction"
        self.assertTrue(native_lifetime.claim(internal))
        self.assertFalse(native_lifetime.claim(self.plan()))
        self.assertTrue(native_lifetime.settle(internal, "completed", "handoff"))
        self.assertTrue(native_lifetime.claim(self.plan()))

    def test_rejected_public_answer_is_recoverable_and_feedback_clears_after_acceptance(self):
        import json
        plan = self.plan(); self.assertTrue(native_lifetime.claim(plan))
        plan.cap = 16384
        text = "Recovered review λ " * 700
        boundary = dict(items=[dict(kind="message", role="assistant", parts=[
            dict(kind="reasoning", text="PRIVATE_REASONING_MUST_NOT_APPEAR"),
            dict(kind="text", text=text)])])
        native_lifetime.settle(plan, "completed", json.dumps(boundary))
        self.assertTrue(native_lifetime.command_output_rejected(plan, "invalid-envelope"))
        notice = native_lifetime.evidence_context()
        self.assertIn("PREVIOUS_COMMAND_OUTPUT_REJECTED", notice)
        self.assertIn(plan.correlation, notice)
        self.assertIn("No commands", notice)
        chunks, offset = [], 0
        while True:
            raw = native_lifetime.result(plan.correlation, offset)
            self.assertLessEqual(len(raw), 2400)
            self.assertNotIn("PRIVATE_REASONING", raw)
            value = json.loads(raw)
            self.assertFalse(value["action_authority"])
            self.assertEqual(value["representation"], "assistant-text")
            chunks.append(value["result"])
            if value["next_offset"] is None:
                break
            self.assertGreater(value["next_offset"], offset)
            offset = value["next_offset"]
        self.assertEqual("".join(chunks), text)
        accepted = self.plan(); self.assertTrue(native_lifetime.claim(accepted))
        native_lifetime.settle(accepted, "completed", "accepted")
        self.assertNotIn("PREVIOUS_COMMAND_OUTPUT_REJECTED", native_lifetime.evidence_context())


if __name__ == "__main__":
    unittest.main()
