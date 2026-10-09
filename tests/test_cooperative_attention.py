"""Arrival, correction and owned receipt cleanup across failure and restart."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import attention
import input_ack
import durable_telegram as wire
import telegram


class ReceiptPeer:
    def __init__(self):
        self.receipts, self.submissions = {}, {}
    def pending(self, limit):
        return [(key, json.dumps(["delivery", key, 0, 1, value[0]])) for key, value in list(self.receipts.items())[:limit]]
    def acknowledge(self, key):
        self.receipts.pop(key, None)
    def rpc(self, code, key, body=""):
        if code == wire.RECEIPT:
            return (wire.STORED, key, json.dumps(self.receipts[key])) if key in self.receipts else (wire.PENDING, key, "")
        if code == wire.SUBMIT:
            old = self.submissions.setdefault(key, body)
            if old != body:
                return wire.CONFLICT, key, ""
            self.receipts[key] = [["delivered", json.loads(body)[0][1]]]
            return wire.STORED, key, ""
        raise AssertionError("unexpected operation")


class CooperativeAttentionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_STRATEGY_PATH": str(Path(temporary.name) / "strategy.db"),
            "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_INPUT_POLICY": "cooperative",
            "METTACLAW_INPUT_ACK_SECONDS": "1", "METTACLAW_INSTANCE": "fixture",
            "METTACLAW_TELEGRAM_BOT_USERNAME": "fixture_bot"})
        environment.start(); self.addCleanup(environment.stop)
        state = mock.patch.multiple(telegram, _effect_turn=None, _effect_activity_epoch=None,
            _effect_attention_revision=None, _activity_epoch=0, _context_activity_epoch=0,
            _pending_messages=[], _prepared_messages=[], _reply_chat_id="42")
        state.start(); self.addCleanup(state.stop)
        native = mock.patch.object(telegram, "metta_client", return_value=False)
        native.start(); self.addCleanup(native.stop)
        running = mock.patch("lifecycle.cognition_enabled", return_value=1)
        running.start(); self.addCleanup(running.stop)
        notices = mock.patch.multiple(input_ack, _pending={}, _last_notice={})
        notices.start(); self.addCleanup(notices.stop)
        self.clock = 100.
        clock = mock.patch.object(input_ack.time, "monotonic", side_effect=lambda: self.clock)
        clock.start(); self.addCleanup(clock.stop)

    def update(self, text, identity=1):
        return {"update_id": identity, "message": {"text": text}}

    def notice(self):
        input_ack.key("input-1", "42.0", "message", "operator", self.update("hello"))
        self.clock += 1
        key = input_ack.key("input-1", "42.0", "message", "operator", self.update("hello"))
        input_ack.submitted("input-1")
        peer = ReceiptPeer()
        peer.receipts[key] = [["delivered", 500]]
        return peer, key

    def test_ordinary_arrival_keeps_decision_but_rest_yields_to_input(self):
        telegram.begin_effect_turn(1)
        telegram._set_last("42", "later ordinary message", update_id=1)
        self.assertEqual(telegram.effect_turn_stimulus_free(1), 1)
        self.assertEqual(telegram.effect_command_unaffected(1, ["send", "drafted answer"]), 1)
        self.assertEqual(telegram.effect_command_unaffected(1, ["rest"]), 0)
        self.assertEqual(len(telegram._pending_messages), 1)

    def test_explicit_authenticated_correction_blocks_unexecuted_work(self):
        telegram.begin_effect_turn(1)
        self.assertEqual(attention.ingress("input-1", "operator", self.update("/steer correction")), 1)
        self.assertEqual(telegram.effect_turn_stimulus_free(1), 0)
        self.assertEqual(telegram.effect_command_unaffected(1, ["send", "stale"]), 0)
        self.assertEqual(attention.ingress("input-1", "operator", self.update("/steer correction")), 0)
        telegram.begin_effect_turn(2)
        self.assertEqual(telegram.effect_turn_stimulus_free(2), 1)

    def test_peer_or_other_bot_cannot_steer(self):
        self.assertEqual(attention.ingress("p", "peer", self.update("/steer")), 0)
        self.assertEqual(attention.ingress("other", "operator", self.update("/steer@other_bot")), 0)
        self.assertEqual(attention.revision(), 0)

    def test_notice_disappearance_requires_matching_processing_receipt(self):
        peer, _ = self.notice()
        input_ack.begin_scan([])  # absence from a bounded scan proves nothing
        self.assertEqual(input_ack.pump(peer), 0)
        self.assertEqual(peer.submissions, {})
        input_ack.processed_update_ids(["other-input"])
        self.assertEqual(input_ack.pump(peer), 0)
        input_ack.processed_update_ids(["1"])
        self.assertEqual(input_ack.pump(peer), 1)
        self.assertEqual([json.loads(value) for value in peer.submissions.values()], [[["delete", 500]]])
        self.assertEqual(input_ack.pump(peer), 0)

    def test_processing_before_notice_registration_prevents_late_notice(self):
        input_ack.processed_update_ids(["1"])
        self.assertEqual(input_ack.key("late", "42.0", "message", "operator", self.update("hello")), "")

    def test_no_confirmed_message_id_means_no_delete_even_after_processing(self):
        peer, key = self.notice()
        peer.receipts.pop(key)
        input_ack.processed_update_ids(["1"])
        self.assertEqual(input_ack.pump(peer), 0)
        self.assertEqual(peer.submissions, {})

    def test_restart_keeps_notice_identity_and_processing_without_resend(self):
        peer, _ = self.notice()
        input_ack.processed_update_ids(["1"])
        input_ack._pending.clear(); input_ack._last_notice.clear()
        self.assertEqual(input_ack.key("input-1", "42.0", "message", "operator", self.update("hello")), "")
        self.assertEqual(input_ack.pump(peer), 1)


if __name__ == "__main__":
    unittest.main()
