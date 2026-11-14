"""Rest remains agent-directed while Telegram can report and interrupt it. Who
wakes her is the access policy's (channels/access_policy.metta)."""

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


class RestControlTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        health = mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_HEALTH_PATH": os.path.join(directory.name, "health.json")})
        health.start()
        self.addCleanup(health.stop)
        telegram._client_seen = None

    def tearDown(self):
        shutil.rmtree(telegram.runtime_spool(), ignore_errors=True)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
