"""Provider failure feedback does not require inference or lose retry identity."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cognitive_health as health
import input_ack
import mode_channel

class ProviderNotices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.root / "health.json"),
            "METTACLAW_STRATEGY_PATH": str(self.root / "ledger.db"),
            "METTACLAW_ENGINE_STATE_PATH": str(self.root / "engine"),
            "METTACLAW_MODE_STATE_ROOT": str(self.root / "modes"),
            "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42",
            "METTACLAW_OBSERVED_STRATEGY": "1"})
        env.start(); self.addCleanup(env.stop)

    def fail(self, now):
        health.turn_started("fixture", now=now, model="chosen-model")
        self.assertEqual(health.turn_failed("provider-rejected-429", now=now + 1), 1)

    def intents(self):
        return list((self.root / "modes" / "channel-outbox").glob("*.json"))

    def test_repeated_failures_restart_and_controls_need_one_actionable_notice(self):
        self.fail(100)
        episode = health.snapshot()["failure_episode"]["id"]
        self.assertEqual(health.notify_failure(), 1)
        original = self.intents()[0].read_bytes()
        for n in range(101, 130):
            self.fail(n)
            self.assertEqual(health.snapshot()["failure_episode"]["id"], episode)
            self.assertEqual(health.notify_failure(), 0)
        self.assertEqual(self.intents()[0].read_bytes(), original)
        text = json.loads(original)["commands"][0][1]
        self.assertIn("HTTP 429", text); self.assertIn("/models", text)
        self.assertIn("retained", text); self.assertIn("chosen-model", text)
        self.assertEqual(input_ack.key("t", "42.0", "message", "operator"), "")
        # Delivery consumed the outbox file; restart must not publish it again.
        self.intents()[0].unlink()
        self.assertEqual(health.notify_failure(), 0)
        self.assertFalse(self.intents())

    def test_reservation_and_body_survive_a_crash_during_enqueue(self):
        self.fail(100)
        with mock.patch.object(mode_channel, "store", side_effect=OSError("disk full")):
            self.assertEqual(health.notify_failure(), 0)
        import agent_checkpoint
        store = agent_checkpoint.ledger()
        with store.transaction() as db:
            reserved = db.execute("SELECT intent,queued FROM provider_notices").fetchone()
        self.assertEqual(reserved[1], 0)
        self.fail(150)
        self.assertEqual(health.notify_failure(), 1)
        self.assertEqual(json.loads(self.intents()[0].read_text()), json.loads(reserved[0]))

    def test_recovery_ends_episode_and_next_outage_can_notify_again(self):
        self.fail(100); health.notify_failure()
        health.turn_started("fixture", now=200, model="chosen-model")
        health.turn_completed(10, now=201)
        self.assertFalse(health.failure_pending()); self.assertEqual(health.notify_failure(), 0)
        self.fail(300); self.assertEqual(health.notify_failure(), 1)
        self.assertEqual(len(self.intents()), 2)
        self.assertEqual(health.snapshot()["model"], "chosen-model")

if __name__ == "__main__":
    unittest.main()
