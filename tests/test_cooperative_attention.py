"""Arrival, correction and owned receipt cleanup across failure and restart."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import attention
import native_ledger
import telegram


class CooperativeAttentionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        native_ledger.start(self, temporary.name)
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_STRATEGY_PATH": str(Path(temporary.name) / "strategy"),
            "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_INPUT_POLICY": "cooperative",
            "METTACLAW_INPUT_ACK_SECONDS": "1", "METTACLAW_INSTANCE": "fixture",
            "METTACLAW_TELEGRAM_BOT_USERNAME": "fixture_bot"})
        environment.start(); self.addCleanup(environment.stop)
        state = mock.patch.multiple(telegram, _effect_turn=None, _effect_activity_epoch=None,
            _activity_epoch=0, _context_activity_epoch=0, _reply_chat_id="42", create=True)
        state.start(); self.addCleanup(state.stop)
        running = mock.patch("lifecycle.cognition_enabled", return_value=1)
        running.start(); self.addCleanup(running.stop)

    def update(self, text, identity=1):
        return {"update_id": identity, "message": {"text": text}}

    def test_steer_is_ordinary_input_without_invented_control_authority(self):
        telegram.begin_effect_turn(1)
        self.assertEqual(attention.ingress('input-1', 'operator', self.update('/steer correction')), 0)
        self.assertEqual(attention.revision(), 0)

    def test_peer_or_other_bot_cannot_steer(self):
        self.assertEqual(attention.ingress("p", "peer", self.update("/steer")), 0)
        self.assertEqual(attention.ingress("other", "operator", self.update("/steer@other_bot")), 0)
        self.assertEqual(attention.revision(), 0)


if __name__ == "__main__":
    unittest.main()
