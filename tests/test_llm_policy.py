import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm_policy


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"METTACLAW_LLM_LEDGER_PATH": self.tmp.name + "/ledger.sqlite3",
            "METTACLAW_LLM_POLICY": "managed", "METTACLAW_LLM_REQUESTS_PER_HOUR": "0",
            "METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY": "0", "METTACLAW_LLM_FAILURES_PER_WINDOW": "0"})
        self.env.start()
        os.environ.pop("METTACLAW_LLM_POLICY_PATH", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_concurrent_modes_cannot_overbook_the_same_instance(self):
        from concurrent.futures import ThreadPoolExecutor
        os.environ["METTACLAW_LLM_REQUESTS_PER_HOUR"] = "1"
        def attempt(mode):
            try:
                return llm_policy.admit("fixture", 42, mode=mode)
            except llm_policy.AdmissionDenied:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            tickets = list(pool.map(attempt, ("iter", "omega")))
        self.assertEqual(sum(ticket is not None for ticket in tickets), 1)

    def test_defaults_do_not_invent_limits_or_change_upstream_policy(self):
        os.environ.pop("METTACLAW_LLM_POLICY")
        self.assertEqual(llm_policy.configuration("iter")["profile"], "upstream")
        self.assertEqual(llm_policy.configuration("omega")["profile"], "upstream")
        self.assertEqual(llm_policy.configuration("godelclaw")["requests_per_hour"], 0)
        self.assertEqual(llm_policy.configuration("godelclaw")["output_tokens_per_day"], 0)

    def test_hourly_limit_survives_mode_switch_and_restart(self):
        os.environ["METTACLAW_LLM_REQUESTS_PER_HOUR"] = "1"
        llm_policy.admit("a", 20, now=100, mode="iter")
        with self.assertRaises(llm_policy.AdmissionDenied) as caught:
            llm_policy.admit("b", 20, now=110, mode="omega")
        self.assertEqual(caught.exception.reason, "request-budget")
        self.assertEqual(caught.exception.retry_after, 3590)
        llm_policy.admit("b", 20, now=3701, mode="omega")

    def test_reservation_blocks_parallel_calls_then_refunds_known_usage(self):
        os.environ["METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY"] = "100"
        ticket = llm_policy.admit("a", 80, now=100)
        with self.assertRaises(llm_policy.AdmissionDenied):
            llm_policy.admit("a", 30, now=101)
        result = {"choices": [{"finish_reason": "stop"}], "usage": {"completion_tokens": 10}}
        self.assertTrue(llm_policy.complete(ticket, result))
        self.assertFalse(llm_policy.complete(ticket, {**result, "usage": {"completion_tokens": 0}}))
        llm_policy.admit("a", 90, now=102)
        with self.assertRaises(llm_policy.AdmissionDenied):
            llm_policy.admit("a", 1, now=103)

    def test_timeout_cannot_claim_a_refund(self):
        os.environ["METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY"] = "100"
        ticket = llm_policy.admit("a", 100, now=100)
        llm_policy.fail(ticket, now=100)
        with self.assertRaises(llm_policy.AdmissionDenied):
            llm_policy.admit("a", 1, now=101)

    def test_background_admission_preserves_interactive_token_and_request_budget(self):
        os.environ["METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY"] = "100"
        os.environ["METTACLAW_LLM_REQUESTS_PER_HOUR"] = "2"
        llm_policy.admit("a", 60, now=100, reserved_output=40, reserved_requests=1)
        with self.assertRaises(llm_policy.AdmissionDenied):
            llm_policy.admit("a", 1, now=101, reserved_output=40, reserved_requests=1)
        llm_policy.admit("a", 40, now=102)

    def test_fully_reserved_hour_has_no_background_admission(self):
        os.environ["METTACLAW_LLM_REQUESTS_PER_HOUR"] = "1"
        with self.assertRaises(llm_policy.AdmissionDenied):
            llm_policy.admit("a", 1, now=100, reserved_requests=1)
        llm_policy.admit("a", 1, now=101)

    def test_dead_owner_is_unknown_and_identical_request_is_not_reissued(self):
        key = llm_policy.fingerprint({"messages": ["same request"]})
        with patch.object(llm_policy, "_owner", "0:0"):
            ticket = llm_policy.admit("a", 50, now=100, request_fingerprint=key)
        with self.assertRaises(llm_policy.AdmissionDenied) as caught:
            llm_policy.admit("a", 50, now=101, request_fingerprint=key)
        self.assertEqual(caught.exception.reason, "provider-outcome-unknown")
        self.assertEqual(llm_policy.snapshot()["requests"], {"unknown": 1})
        self.assertEqual(llm_policy.snapshot()["accounted_output_tokens"], 50)
        self.assertIn(ticket, llm_policy.unknown_context())
        llm_policy.admit("a", 50, now=102, request_fingerprint=llm_policy.fingerprint({"messages": ["reconsider"]}))

    def test_live_owner_is_not_mistaken_for_a_crashed_concurrent_call(self):
        ticket = llm_policy.admit("a", 50, now=100)
        with patch.object(llm_policy, "_owner", "different-session"):
            llm_policy.recover_interrupted()
        self.assertEqual(llm_policy.snapshot()["requests"], {"pending": 1})
        self.assertTrue(llm_policy.complete(ticket, {"choices": [{"finish_reason": "stop"}]}))

    def test_quota_cooldown_and_upstream_bypass(self):
        ticket = llm_policy.admit("a", 10, now=100)
        llm_policy.fail(ticket, status=429, retry_after=600, now=100)
        with self.assertRaises(llm_policy.AdmissionDenied) as caught:
            llm_policy.admit("a", 10, now=200)
        self.assertEqual(caught.exception.retry_after, 500)
        os.environ["METTACLAW_LLM_POLICY"] = "upstream"
        llm_policy.admit("a", 10, now=200)
        os.environ["METTACLAW_LLM_POLICY"] = "managed"
        llm_policy.admit("a", 10, now=701)

    def test_retry_window_is_explicit_and_bounded(self):
        os.environ["METTACLAW_LLM_FAILURES_PER_WINDOW"] = "2"
        for now in (100, 110):
            ticket = llm_policy.admit("a", 10, now=now)
            llm_policy.fail(ticket, now=now)
        with self.assertRaises(llm_policy.AdmissionDenied) as caught:
            llm_policy.admit("a", 10, now=120)
        self.assertEqual(caught.exception.reason, "retry-budget")
        llm_policy.admit("a", 10, now=401)

    def test_invalid_explicit_policy_does_not_remove_protection(self):
        path = Path(self.tmp.name) / "policy.json"
        path.write_text("broken")
        os.environ["METTACLAW_LLM_POLICY_PATH"] = str(path)
        with self.assertRaises(ValueError):
            llm_policy.admit("a", 10, now=100)


if __name__ == "__main__":
    unittest.main()
