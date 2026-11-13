"""Rest remains agent-directed while Telegram can report and interrupt it."""

import shutil
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "channels"))

import telegram  # noqa: E402



def woken():
    """The reasons of the wakes queued for the MeTTa client."""
    return [event["wake_only"] for event in telegram.spooled_events() if "wake_only" in event]


class RestControlTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        health = mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_HEALTH_PATH": os.path.join(directory.name, "health.json")})
        health.start()
        self.addCleanup(health.stop)
        telegram._client_seen = None

    def tearDown(self):
        telegram._metta.pop("sleep_until", None)
        shutil.rmtree(telegram.runtime_spool(), ignore_errors=True)

    def test_operator_message_requests_wake(self):
        with mock.patch.dict(
                os.environ,
                {"METTACLAW_TELEGRAM_OPERATOR_IDS": "111000111"}):
            self.assertTrue(telegram._wake_for_operator_message(
                {"id": 111000111, "is_bot": False}))
        self.assertEqual(woken(), ["operator message"])

    def test_non_operator_message_does_not_request_wake(self):
        with mock.patch.dict(
                os.environ,
                {"METTACLAW_TELEGRAM_OPERATOR_IDS": "111000111"}):
            self.assertFalse(telegram._wake_for_operator_message(
                {"id": 222000222, "is_bot": False}))
        self.assertEqual(woken(), [])

    def test_wake_peer_message_requests_wake_without_operator_control(self):
        peer = {"id": 333000333, "is_bot": True}
        with mock.patch.dict(
                os.environ,
                {"METTACLAW_TELEGRAM_OPERATOR_IDS": "111000111",
                 "METTACLAW_TELEGRAM_WAKE_IDS": "333000333"}):
            self.assertTrue(telegram._wake_for_operator_message(peer))
            self.assertFalse(telegram._is_operator(peer))
            self.assertIsNone(telegram._peek_slash_command("/stop", peer))
        self.assertEqual(woken(), ["peer message"])

    def test_unlisted_bot_message_does_not_request_wake(self):
        with mock.patch.dict(
                os.environ,
                {"METTACLAW_TELEGRAM_OPERATOR_IDS": "111000111",
                 "METTACLAW_TELEGRAM_WAKE_IDS": "333000333"}):
            self.assertFalse(telegram._wake_for_operator_message(
                {"id": 444000444, "is_bot": True}))
        self.assertEqual(woken(), [])

    def test_agent_requested_rest_has_no_hidden_upper_clamp(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        start = source.index("(= (rest $seconds)")
        end = source.index("\n\n", start)
        rest_rule = source[start:end]
        self.assertNotIn("(min 1800", rest_rule)

    def test_rest_emits_policy_directives_instead_of_mutating_runner(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        start = source.index("(= (rest)")
        end = source.index("(= (mode)", start)
        rest_rules = source[start:end]
        self.assertIn("(loop-directive-set rest-request 1)", rest_rules)
        self.assertNotIn("&loops", rest_rules)
        self.assertNotIn("&sleepInterval", rest_rules)

    def test_every_rest_banks_the_budget_rather_than_spending_it(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        start = source.index("(= (rest)")
        end = source.index("(= (nop)", start)
        rest_rules = source[start:end]
        self.assertEqual(rest_rules.count("(loop-directive-set rest-request 1)"),
                         4, "all four rest arities must bank the budget")
        self.assertNotIn("(loop-directive-set loops 0)", rest_rules)

    def test_nop_ends_the_breath_and_touches_nothing_else(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        start = source.index("(= (nop)")
        end = source.index("(= (mode)", start)
        nop_rule = source[start:end]
        self.assertIn("(loop-directive-set loops 0)", nop_rule)
        self.assertNotIn("resume_autonomy", nop_rule)
        self.assertNotIn("pause_autonomy", nop_rule)

    def test_heartbeat_is_not_suppressed_by_a_paused_burst_renewal(self):
        """rest_is_a_nap (ClawArchitectures.lean): the life-rhythm renews
        from any reachable state, so rest must not gate the heartbeat."""
        source = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        start = source.index("(= (heartbeatDue")
        end = source.index("(= (applyHeartbeat", start)
        rule = source[start:end]
        self.assertNotIn("autonomy_paused", rule)

    def test_plain_and_timed_rest_clear_stale_continuations(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        start = source.index("(= (rest)")
        continuation = source.index("(= (rest-command $seconds", start)
        ordinary = source[start:continuation]
        self.assertEqual(
            ordinary.count("pending-continuation"), 2,
            "both (rest) and (rest seconds) must clear an older timer action",
        )

    def test_continuation_is_stored_as_opaque_data(self):
        source = (ROOT / "src" / "skills.metta").read_text(encoding="utf-8")
        self.assertIn("(continuation-value $continuation)", source)
        self.assertIn("(= (rest $seconds $why)", source)

    def test_positive_fractional_remainder_is_reported_as_one_second(self):
        telegram._metta["sleep_until"] = 100.1
        with mock.patch.object(telegram.time, "time", return_value=100.0):
            self.assertEqual(telegram.rest_status(), (True, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
