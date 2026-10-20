import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import cognitive_health  # noqa: E402


class CognitiveHealthTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = pathlib.Path(self.temporary.name) / "cognitive.json"
        self.environment = mock.patch.dict(os.environ, {
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.path),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        cognitive_health._turn_outcome = "none"

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_repeated_expectations_preserve_original_pending_time(self):
        cognitive_health.expect_turn("claw23", now=100)
        cognitive_health.expect_turn("claw23", now=200)
        value = self.state()
        self.assertEqual(value["obligation_since"], 100)
        self.assertEqual(value["last_expected_at"], 200)
        self.assertEqual(value["expected_count"], 1)

    def test_expectation_records_the_budget_visible_at_turn_start(self):
        cognitive_health.expect_turn("agent", budget=50, now=100)
        value = self.state()
        self.assertEqual(value["budget_at_start"], 50)
        self.assertEqual(value["mode"], "agent")

    def test_provider_start_replaces_an_abandoned_obligation_age(self):
        cognitive_health.expect_turn("agent", budget=50, now=100)
        cognitive_health.turn_started("synthetic", now=250)
        value = self.state()
        self.assertEqual(value["pending_since"], 250)
        self.assertEqual(value["in_flight_since"], 250)
        self.assertEqual(value["obligation_since"], 100)
        self.assertEqual(value["last_started_at"], 250)

    def test_completion_ends_request_then_settlement_discharges_obligation(self):
        cognitive_health.expect_turn("generic", now=100)
        cognitive_health.turn_started("synthetic", now=101)
        cognitive_health.turn_completed(37, now=102)
        value = self.state()
        self.assertEqual(value["pending_since"], 0)
        self.assertEqual(value["in_flight_since"], 0)
        self.assertEqual(value["obligation_since"], 100)
        self.assertEqual(value["last_completed_at"], 102)
        self.assertEqual(value["last_response_bytes"], 37)
        self.assertEqual(cognitive_health.turn_succeeded(), 1)
        cognitive_health.turn_settled(now=103)
        value = self.state()
        self.assertEqual(value["obligation_since"], 0)
        self.assertEqual(value["last_settled_at"], 103)
        self.assertNotIn("prompt", value)
        self.assertNotIn("response", value)

    def test_failure_ends_request_but_leaves_obligation_for_retry(self):
        cognitive_health.expect_turn("generic", now=100)
        cognitive_health.turn_started("synthetic", now=125)
        cognitive_health.turn_failed("ReadTimeout", now=150)
        value = self.state()
        self.assertEqual(value["pending_since"], 0)
        self.assertEqual(value["in_flight_since"], 0)
        self.assertEqual(value["obligation_since"], 100)
        self.assertEqual(value["last_failure_type"], "readtimeout")
        self.assertEqual(value["last_outcome"], "failed")
        self.assertEqual(value["failed_count"], 1)
        self.assertEqual(value["failure_streak"], 1)
        self.assertEqual(cognitive_health.turn_succeeded(), 0)

    def test_local_retries_do_not_count_as_admitted_provider_failures(self):
        cognitive_health.expect_turn("generic", now=100)
        cognitive_health.turn_failed("duplicate-plan", now=101)
        cognitive_health.turn_failed("duplicate-plan", now=102)
        value = self.state()
        self.assertEqual(value.get("failed_count", 0), 0)
        self.assertEqual(value["local_failure_count"], 2)
        self.assertEqual(value["failure_streak"], 2)
        self.assertEqual(value["expected_count"], 1)

    def test_settlement_is_idempotent_without_a_new_obligation(self):
        cognitive_health.expect_turn("generic", now=100)
        cognitive_health.turn_settled(now=101)
        cognitive_health.turn_settled(now=102)
        value = self.state()
        self.assertEqual(value["settled_count"], 1)

    def test_retry_wait_ignores_queued_input_but_obeys_operator_stop(self):
        cognitive_health.turn_failed("provider-error", now=100,
                                     retry_after=3)
        with mock.patch.object(cognitive_health.time, "monotonic",
                               side_effect=[10, 10, 11, 12, 13]), \
             mock.patch.object(cognitive_health.time, "sleep") as sleep, \
             mock.patch("lifecycle.cognition_enabled", return_value=1), \
             mock.patch("helper.recycle_requested", return_value=0), \
             mock.patch("process_supervision.progress") as progress:
            self.assertEqual(cognitive_health.wait_for_retry(3),
                             "rested 3s provider retry")
        self.assertEqual(sleep.call_count, 3)
        progress.assert_called_once_with("provider-backoff", 3.0)

        with mock.patch.object(cognitive_health.time, "monotonic",
                               return_value=20), \
             mock.patch("lifecycle.cognition_enabled", return_value=0), \
             mock.patch.object(cognitive_health.time, "sleep") as sleep:
            self.assertEqual(cognitive_health.wait_for_retry(30),
                             "operator stopped during provider retry")
        sleep.assert_not_called()

    def test_receipt_failure_never_interrupts_cognition(self):
        with mock.patch.object(cognitive_health, "_write",
                               side_effect=OSError("read-only")):
            self.assertEqual(cognitive_health.expect_turn(now=100), 0)
            self.assertEqual(cognitive_health.turn_started(now=101), 0)
            self.assertEqual(cognitive_health.turn_completed(now=102), 0)
            self.assertEqual(cognitive_health.turn_failed(now=103), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
