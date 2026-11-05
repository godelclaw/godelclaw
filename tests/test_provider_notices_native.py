"""Provider failure notices on the native ledger: the same reservation, publication and retry identity.

tests/test_provider_notices.py checks notify_failure on the SQLite checkpoint. Here the reservation is the
ledger's provider-notice record (provider-notice-reserve, provider-notice-queued), and the SQLite file is
never given a provider_notices table.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]
from observed_strategy import Ledger
import cognitive_health as health
import input_ack
import mode_channel
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import available


@unittest.skipUnless(available(), SKIP)
class ProviderNoticesNative(NativeCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.root / "health.json"),
            "METTACLAW_STRATEGY_PATH": str(self.root / "ledger.db"),
            "METTACLAW_ENGINE_STATE_PATH": str(self.root / "engine"),
            "METTACLAW_MODE_STATE_ROOT": str(self.root / "modes"),
            "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42",
            "METTACLAW_OBSERVED_STRATEGY": "1",
            input_ack.LEDGER_SOCKET: str(self.server.socket)})
        env.start(); self.addCleanup(env.stop)
        Ledger(self.root / "ledger.db")
        input_ack.close_ledger()

    def fail(self, now):
        health.turn_started("fixture", now=now, model="chosen-model")
        self.assertEqual(health.turn_failed("provider-rejected-429", now=now + 1), 1)

    def intents(self):
        return list((self.root / "modes" / "channel-outbox").glob("*.json"))

    def stored(self):
        episode = health.snapshot()["failure_episode"]["id"]
        row = input_ack._ledger_call("provider-notice", (episode,))
        return tuple(row) if isinstance(row, tuple) else None

    def no_sqlite_table(self):
        with closing(sqlite3.connect(self.root / "ledger.db")) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_notices'").fetchone())

    def test_repeated_failures_need_one_notice_with_one_identity(self):
        self.fail(100)
        episode = health.snapshot()["failure_episode"]["id"]
        self.assertEqual(health.notify_failure(), 1)
        original = self.intents()[0].read_bytes()
        self.assertEqual(self.stored()[1], 1)
        for n in range(101, 110):
            self.fail(n)
            self.assertEqual(health.snapshot()["failure_episode"]["id"], episode)
            self.assertEqual(health.notify_failure(), 0)
        self.assertEqual(self.intents()[0].read_bytes(), original)
        self.assertIn("HTTP 429", json.loads(original)["commands"][0][1])
        # Delivery consumed the outbox file; a restart must not publish it again.
        self.intents()[0].unlink()
        self.assertEqual(health.notify_failure(), 0)
        self.assertFalse(self.intents())
        self.no_sqlite_table()

    def test_the_reservation_and_body_survive_a_failed_enqueue(self):
        self.fail(100)
        with mock.patch.object(mode_channel, "store", side_effect=OSError("disk full")):
            self.assertEqual(health.notify_failure(), 0)
        intent, queued = self.stored()
        self.assertEqual(queued, 0)
        self.fail(150)
        self.assertEqual(health.notify_failure(), 1)
        self.assertEqual(json.loads(self.intents()[0].read_text()), json.loads(intent))
        self.no_sqlite_table()

    def test_an_unavailable_ledger_answers_zero_and_blocks_nothing(self):
        self.fail(100)
        with mock.patch.object(input_ack, "_ledger_call", side_effect=input_ack.LedgerUnavailable("down")):
            self.assertEqual(health.notify_failure(), 0)
        self.assertFalse(self.intents())
        self.assertEqual(health.notify_failure(), 1)


if __name__ == "__main__":
    unittest.main()
