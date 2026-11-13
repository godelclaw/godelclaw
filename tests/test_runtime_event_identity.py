"""Runtime occurrences survive replay without identifying events by their text."""
import shutil
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
import native_ledger
import telegram
from observed_strategy import Ledger


class RuntimeIdentityTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        native_ledger.start(self, directory.name)
        self.ledger = Ledger(Path(directory.name) / "state")
        env = mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY="1",
                              METTACLAW_STRATEGY_PATH=str(self.ledger.path),
                              METTACLAW_TELEGRAM_CLIENT="metta")
        env.start(); self.addCleanup(env.stop)
        spool = tempfile.TemporaryDirectory(); self.addCleanup(spool.cleanup)
        events = mock.patch.object(telegram, "runtime_spool", return_value=spool.name)
        events.start(); self.addCleanup(events.stop)
        wake = mock.patch.object(telegram, "_request_wake")
        self.wake = wake.start(); self.addCleanup(wake.stop)

    def test_identical_text_is_two_occurrences(self):
        telegram.enqueue_runtime_event("same text")
        telegram.enqueue_runtime_event("same text")
        ids = [event["update_id"] for event in telegram.spooled_events()]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(identity.startswith("runtime:") for identity in ids))

    def test_stable_replay_before_and_after_checkpoint(self):
        identity = "runtime:model-call:receipt-1:late"
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        self.assertEqual(len(telegram.spooled_events()), 1)
        self.assertEqual(telegram.spooled_events()[0]["wake"], "runtime event")
        self.assertEqual(telegram.activity_source_ack(identity), 0)
        self.ledger.checkpoint({"saved_at": 1}, [identity])
        with mock.patch.object(telegram, "handover_ack", return_value=1) as handover:
            self.assertEqual(telegram.activity_source_ack(identity), 1)
            handover.assert_not_called()
        # A new process's queue is empty, but its checkpoint retains the ID.
        shutil.rmtree(telegram.runtime_spool(), ignore_errors=True)
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        self.assertEqual(telegram.spooled_events(), [])
        self.assertFalse(self.wake.called)

    def test_complete_batch_is_validated_before_any_source_is_settled(self):
        import input_ack
        with mock.patch.object(telegram, "handover_ack") as handover, \
             mock.patch.object(input_ack, "committed") as committed:
            for batch in (["123", "runtime:bad,id"],
                          ["runtime:a"] * 257 + ["runtime:bad,id"],
                          ["123"] * 257 + ["invalid-update"]):
                with self.subTest(batch_tail=batch[-1]):
                    with self.assertRaises(ValueError):
                        telegram.activity_source_ack(batch)
            handover.assert_not_called()
            committed.assert_not_called()

    def test_complete_pages_have_no_legacy_256_item_ceiling(self):
        # Paging removed the old fixed ceiling in 40aae9607. All IDs must
        # still be checked before source settlement, including duplicate IDs.
        import input_ack
        for count in (256, 257, 513):
            for runtime in (["runtime:a"] * count,
                            ["runtime:%d" % n for n in range(count)]):
                updates = [str(n) for n in range(count)]
                with self.subTest(count=count, duplicates=len(set(runtime)) == 1), \
                     mock.patch.object(input_ack, "committed", return_value=True) as committed, \
                     mock.patch.object(telegram, "handover_ack", return_value=1) as handover:
                    self.assertEqual(telegram.activity_source_ack(runtime + updates), 1)
                    committed.assert_called_once_with(runtime)
                    handover.assert_called_once_with(updates)
                with mock.patch.object(input_ack, "committed", return_value=False), \
                     mock.patch.object(telegram, "handover_ack") as handover:
                    self.assertEqual(telegram.activity_source_ack(runtime + updates), 0)
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
