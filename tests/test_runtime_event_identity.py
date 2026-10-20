"""Runtime occurrences survive replay without identifying events by their text."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import telegram
from observed_strategy import Ledger


class RuntimeIdentityTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "checkpoint.sqlite3")
        env = mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY="1",
                              METTACLAW_STRATEGY_PATH=str(self.ledger.path),
                              METTACLAW_TELEGRAM_CLIENT="metta")
        env.start(); self.addCleanup(env.stop)
        events = mock.patch.object(telegram, "_metta_events", [])
        events.start(); self.addCleanup(events.stop)
        wake = mock.patch.object(telegram, "_request_wake")
        self.wake = wake.start(); self.addCleanup(wake.stop)

    def test_identical_text_is_two_occurrences(self):
        telegram.enqueue_runtime_event("same text")
        telegram.enqueue_runtime_event("same text")
        ids = [event["update_id"] for event in telegram._metta_events]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(identity.startswith("runtime:") for identity in ids))

    def test_stable_replay_before_and_after_checkpoint(self):
        identity = "runtime:model-call:receipt-1:late"
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        self.assertEqual(len(telegram._metta_events), 1)
        self.assertEqual(telegram._metta_events[0]["wake"], "runtime event")
        self.assertEqual(telegram.activity_source_ack(identity), 0)
        self.ledger.checkpoint({"saved_at": 1}, [identity])
        with mock.patch.object(telegram, "handover_ack", return_value=1) as handover:
            self.assertEqual(telegram.activity_source_ack(identity), 1)
            handover.assert_not_called()
        # A new process's queue is empty, but its checkpoint retains the ID.
        telegram._metta_events.clear()
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        self.assertEqual(telegram._metta_events, [])
        self.assertFalse(self.wake.called)

    def test_complete_batch_is_validated_before_any_source_is_settled(self):
        with mock.patch.object(telegram, "handover_ack") as handover:
            with self.assertRaises(ValueError):
                telegram.activity_source_ack(["123", "runtime:bad,id"])
            with self.assertRaises(ValueError):
                telegram.activity_source_ack(["runtime:a"] * 257)
        handover.assert_not_called()

    def test_runtime_receipts_remain_separate_from_telegram_receipts(self):
        identity = "runtime:job:1"
        self.ledger.checkpoint({"saved_at": 1}, [identity])
        with mock.patch.object(telegram, "handover_ack", return_value=1) as handover:
            self.assertEqual(telegram.activity_source_ack([identity, "123"]), 1)
        handover.assert_called_once_with(["123"])

    def test_presented_batch_has_stable_bounded_identity(self):
        telegram._context_activity_epoch = 7
        telegram._current_batch_update_ids = {"102", "101"}
        first = telegram.current_batch_identity()
        telegram._current_batch_update_ids = {"101", "102"}
        self.assertEqual(telegram.current_batch_identity(), first)
        self.assertEqual(len(first), hashlib.sha256().digest_size * 2)
        telegram._current_batch_update_ids.add("103")
        self.assertNotEqual(telegram.current_batch_identity(), first)


if __name__ == "__main__": unittest.main()
