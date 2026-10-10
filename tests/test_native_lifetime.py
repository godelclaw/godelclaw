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

    def test_killed_sent_call_records_uncertainty_and_keeps_reservation(self):
        plan = self.plan(); native_lifetime.claim(plan); native_lifetime.submitted(plan, 3)
        with mock.patch.object(native_lifetime, "process_start", return_value=None):
            native_lifetime.recover()
        self.assertEqual(native_lifetime.status()[0]["state"], "uncertain")
        self.assertIn("remote-outcome-unknown", native_lifetime.result(plan.correlation))
        self.assertEqual(llm_policy.snapshot()["accounted_output_tokens"], 10)

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


if __name__ == "__main__":
    unittest.main()
