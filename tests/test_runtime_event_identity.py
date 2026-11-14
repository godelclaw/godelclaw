"""Runtime occurrences survive replay without identifying events by their text.

Settling a batch at its source is the native client's
(src/activity_source_cetta.metta, tests/unit/activity_source.metta)."""
import shutil
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import native_ledger
import runtime_spool
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

    def spooled(self):
        return runtime_spool.events(telegram.runtime_spool())

    def test_identical_text_is_two_occurrences(self):
        telegram.enqueue_runtime_event("same text")
        telegram.enqueue_runtime_event("same text")
        ids = [event["update_id"] for event in self.spooled()]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(identity.startswith("runtime:") for identity in ids))

    def test_stable_replay_before_and_after_checkpoint(self):
        identity = "runtime:model-call:receipt-1:late"
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        self.assertEqual(len(self.spooled()), 1)
        self.assertEqual(self.spooled()[0]["wake"], "runtime event")
        self.ledger.checkpoint({"saved_at": 1}, [identity])
        # A new process's queue is empty, but its checkpoint retains the ID.
        shutil.rmtree(telegram.runtime_spool(), ignore_errors=True)
        telegram.enqueue_runtime_event("retained receipt", identity=identity)
        # Nothing is spooled again, a wake request included.
        self.assertEqual(self.spooled(), [])


if __name__ == "__main__": unittest.main()
