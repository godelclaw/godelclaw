"""Retry envelopes preserve occurrences and independent work across restarts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger
import task_context

class InputEnvelopes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.db"
        self.store = Ledger(self.path)

    def batch(self, key, ids, text=None, chat="private", kind="message"):
        self.store.accept(key, text or (key + ": complete input"), kind=kind)
        self.store.link_inputs(key, ids)
        task_context.begin(self.store, key, "operator-event", chat=chat)

    def test_nested_retries_keep_all_sources_progress_and_stable_slots(self):
        self.batch("input:a", range(128))
        task_context.note(self.store, "input:a", "Still need to inspect the original contract")
        before = task_context.conversation_projection(self.store, "input:a")
        self.batch("input:b", range(146))
        self.batch("input:c", range(147))
        self.store.processed("input:c")
        fresh = Ledger(self.path)
        self.assertEqual({r["id"] for r in fresh.active()}, set())
        view = task_context.conversation_projection(fresh, "input:c")
        self.assertEqual(len(view["transcript"]), 3)
        self.assertEqual(before["transcript"][0]["id"], view["transcript"][0]["id"])
        self.assertIn("covered by input:b", view["transcript"][0]["result"])
        self.assertEqual(view["transcript"][-1]["result"], "input:c: complete input")
        family = task_context.projection(fresh, "input:c")
        self.assertEqual(len(family["requests"]), 3)
        self.assertIn("Still need", family["notes"][0]["text"])
        self.assertEqual(json.loads(task_context.read(fresh, "task-input:input:a")["text"])["result"], "input:a: complete input")
        with fresh.transaction() as db:
            self.assertEqual(db.execute("SELECT count(DISTINCT input) FROM request_inputs").fetchone()[0], 147)
            self.assertFalse(db.execute("SELECT 1 FROM requests WHERE status='achieved'").fetchone())
        self.assertEqual(task_context.resume(fresh, "input:a"), "input:c")

    def test_partial_overlap_duplicate_text_scope_and_explicit_task_do_not_cover(self):
        self.batch("input:a", [1, 2], "same text")
        self.batch("input:other-occurrences", [3, 4], "same text")
        self.batch("input:partial", [2, 5])
        self.batch("input:other-chat", [1, 2, 6], chat="group")
        self.batch("input:other-kind", [1, 2, 7], kind="notification")
        self.store.disposition("input:a", "keep", "An independent continuing task")
        self.batch("input:superset", [1, 2, 8])
        self.assertEqual(len(self.store.active()), 6)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM input_supersessions").fetchone()[0], 0)

    def test_retries_are_idempotent_and_input_membership_immutable(self):
        self.batch("input:a", [1, 2]); self.batch("input:b", [1, 2, 3])
        self.batch("input:b", [1, 2, 3])
        with self.assertRaisesRegex(ValueError, "different occurrences"):
            self.store.link_inputs("input:a", [1, 2, 4])
        self.assertTrue(self.store.disposition("input:a", "reopen", "Independent work remains"))
        self.batch("input:c", [1, 2, 3, 4])
        self.assertIn("input:a", {r["id"] for r in self.store.active()})

    def test_upgrade_reconciles_older_batches_under_an_already_processed_cover(self):
        self.batch("input:a", range(128)); self.batch("input:b", range(147))
        self.store.processed("input:b")
        with self.store.transaction() as db:
            db.execute("DELETE FROM input_supersessions")
            db.execute("UPDATE requests SET status='active' WHERE id='input:a'")
            db.execute("DELETE FROM meta WHERE key='input_supersessions_v1'")
        fresh = Ledger(self.path)
        self.assertEqual(fresh.request_record("input:a")["status"], "covered")
        self.assertEqual(fresh.request_record("input:b")["status"], "processed")

if __name__ == "__main__":
    unittest.main()
