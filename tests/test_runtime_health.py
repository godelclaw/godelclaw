import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "channels"))
import runtime_health  # noqa: E402


class RuntimeHealthTest(unittest.TestCase):
    def test_completed_provider_turn_cannot_hide_a_stalled_command(self):
        self.write(self.working, {"saved_at": 100, "loops": 50})
        self.write(self.cognitive, {"schema": 2, "last_outcome": "completed", "last_completed_at": 110})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("engine_modes.active_engine", return_value="cetta"), \
             mock.patch("synthetic_llm.current_model", return_value="fixture"), \
             mock.patch("process_supervision.observation", return_value={"state": "stalled"}):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: stalled", report)
        self.assertIn("turn=completed:890s-ago", report)
        self.assertNotIn("activity: working", report)

    def test_failed_retry_with_checkpoint_fuel_is_not_working(self):
        self.write(self.working, {"saved_at": 999, "loops": 50})
        self.write(self.cognitive, {"schema": 2, "last_outcome": "failed",
            "last_failed_at": 980, "in_flight_since": 0, "retry_at": 1100})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="cetta"), \
             mock.patch("synthetic_llm.current_model", return_value="fixture"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: retrying", report)
        self.assertIn("wake=human-or-100s", report)
        self.assertNotIn("activity: working", report)

    def test_external_control_and_upstream_checkpoint_replace_legacy_client_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            receipt = root / "modes/control-status.json"
            receipt.parent.mkdir()
            self.write(receipt, {"observed_at": 995, "channel_ready": True})
            self.write(self.channel, {"last_poll_ok_at": 1, "menu_status": "unknown"})
            self.write(self.working, {"saved_at": 1})
            self.write(self.mode, {"mode": "iter"})
            self.write(self.cognitive, {"schema": 2, "last_completed_at": 997, "last_settled_at": 998})
            with mock.patch.dict(os.environ, {"METTACLAW_ENGINE_STATE_PATH": str(root / "engine")}), \
                 mock.patch.object(runtime_health.memory_health, "drift", return_value=(0, 0, 0)):
                value = runtime_health.status(now=1000)
                self.assertEqual(value["problems"], [])
                self.assertEqual(value["operator_control_age_seconds"], 5)
                self.assertEqual(value["working_set_age_seconds"], 2)
                self.write(receipt, {"observed_at": 950, "channel_ready": False})
                value = runtime_health.status(now=1000)
                self.assertIn("operator-control-stale", value["problems"])
                self.assertIn("channel-service-unavailable", value["problems"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = pathlib.Path(self.temporary.name)
        self.channel = root / "channel.json"
        self.working = root / "working.json"
        self.cognitive = root / "cognitive.json"
        self.mode = root / "mode.json"
        self.environment = mock.patch.dict(os.environ, {
            "METTACLAW_TELEGRAM_HEALTH_PATH": str(self.channel),
            "METTACLAW_WORKING_SET_PATH": str(self.working),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.cognitive),
            "METTACLAW_LOOP_MODE_PATH": str(self.mode),
            "METTACLAW_COGNITIVE_STALE_SECONDS": "300",
            "METTACLAW_DEPLOYED_COMMIT": "abcdef0123456789",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.lifecycle = mock.patch("lifecycle.cognition_enabled",
                                    return_value=1)
        self.lifecycle_view = mock.patch("lifecycle.view",
                                         return_value="running")
        self.lifecycle.start()
        self.lifecycle_view.start()
        self.addCleanup(self.lifecycle.stop)
        self.addCleanup(self.lifecycle_view.stop)

    def test_stopped_lifecycle_needs_channel_but_not_cognition(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 1, "loops": 50})
        self.write(self.cognitive, {"pending_since": 100})
        self.write(self.mode, {"mode": "godelclaw"})
        with mock.patch("lifecycle.cognition_enabled", return_value=0), \
             mock.patch("lifecycle.view", return_value="stopped"), \
             mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertFalse(value["cognition_required"])
        self.assertNotIn("cognitive-boundary-stale", value["problems"])
        self.assertNotIn("model-turn-overdue", value["problems"])

    def test_activity_reports_operator_stop_explicitly(self):
        self.write(self.working, {"saved_at": 999, "loops": 50})
        self.write(self.cognitive, {"pending_since": 0})
        with mock.patch("lifecycle.cognition_enabled", return_value=0), \
             mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: stopped", report)
        self.assertIn("steps=0@operator-latch", report)
        self.assertIn("wake=/start", report)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_healthy_wait_is_not_mistaken_for_a_hang(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 1})
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 95, 5)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20), \
             mock.patch.object(runtime_health, "_generation",
                               return_value="abcdef012345"):
            value = runtime_health.status(now=1000)
        self.assertEqual(value["state"], "ok")
        self.assertEqual(value["generation"], "abcdef012345")

    def test_stale_poll_and_awake_loop_fail_independently(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 100,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 1})
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertIn("telegram-poll-stale", value["problems"])
        self.assertIn("cognitive-boundary-stale", value["problems"])

    def test_fresh_completed_model_turn_is_healthy_when_armed(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 999, "loops": 5})
        self.write(self.cognitive, {
            "last_completed_at": 995, "completed_count": 4,
            "pending_since": 0, "last_outcome": "completed",
        })
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertNotIn("model-turn-stale", value["problems"])
        self.assertEqual(value["model_turn_age_seconds"], 5)

    def test_old_pending_turn_detects_empty_spinning(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 999, "loops": 42})
        self.write(self.cognitive, {
            "pending_since": 600, "last_expected_at": 999,
            "expected_count": 300, "completed_count": 0,
        })
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertIn("model-turn-overdue", value["problems"])
        self.assertEqual(value["model_turn_pending_age_seconds"], 400)

    def test_new_pending_turn_gets_grace_despite_old_completion(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 999, "loops": 5})
        self.write(self.cognitive, {
            "pending_since": 995, "last_completed_at": 100,
            "expected_count": 2, "completed_count": 1,
        })
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertNotIn("model-turn-stale", value["problems"])
        self.assertNotIn("model-turn-overdue", value["problems"])

    def test_intentional_default_idle_needs_no_model_receipt(self):
        self.write(self.channel, {
            "menu_status": "ok", "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
            "started_at": 1,
        })
        self.write(self.working, {"saved_at": 999, "loops": 0})
        self.write(self.mode, {"mode": "default", "autonomy_paused": False})
        with mock.patch.object(runtime_health.memory_health, "drift",
                               return_value=(100, 100, 0)), \
             mock.patch.object(runtime_health.memory_health, "threshold",
                               return_value=20):
            value = runtime_health.status(now=1000)
        self.assertFalse(value["cognition_required"])
        self.assertNotIn("model-turn-receipt-missing", value["problems"])

    def test_activity_reports_existing_receipts_without_control(self):
        self.write(self.working, {
            "saved_at": 990, "loops": 12, "continuation_pending": True,
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="cetta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: working", report)
        self.assertIn("steps=12@checkpoint", report)
        self.assertIn("continuation=pending", report)
        self.assertIn("turn=completed:5s-ago", report)

    def test_activity_does_not_call_one_second_poll_a_rest(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": False})
        # A mode that does not renew itself.
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: idle", report)
        self.assertIn("wake=human-or-heartbeat", report)
        self.assertNotIn("rest-left", report)
        self.assertIn("rest=none", report)

    def test_pending_turn_reports_armed_budget_not_stale_checkpoint(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "last_completed_at": 900, "pending_since": 998,
            "budget_at_start": 50, "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": False})
        # A mode that does not renew itself.
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: working", report)
        self.assertIn("steps=50@turn-start", report)
        self.assertIn("turn=pending:2s", report)

    def test_failed_request_is_not_reported_as_still_in_flight(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "schema": 2,
            "last_completed_at": 900,
            "last_failed_at": 980,
            "pending_since": 600,
            "in_flight_since": 0,
            "obligation_since": 600,
            "budget_at_start": 50,
            "last_outcome": "failed",
        })
        self.write(self.mode, {"mode": "godelclaw"})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertNotIn("turn=pending", report)
        self.assertIn("turn=failed:20s-ago", report)
        self.assertIn("obligation=retry:400s", report)

    def test_legacy_failed_request_migrates_to_retry_not_in_flight(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "last_completed_at": 900,
            "last_failed_at": 980,
            "pending_since": 600,
            "budget_at_start": 50,
            "last_outcome": "failed",
        })
        self.write(self.mode, {"mode": "godelclaw"})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertNotIn("turn=pending", report)
        self.assertIn("turn=failed:20s-ago", report)
        self.assertIn("obligation=retry:400s", report)

    def test_activity_reports_pending_input_separately_from_provider(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "schema": 2,
            "last_completed_at": 900,
            "last_failed_at": 980,
            "in_flight_since": 0,
            "obligation_since": 600,
            "last_outcome": "failed",
        })
        self.write(self.mode, {"mode": "godelclaw"})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"), \
             mock.patch("telegram.pendingActivityCount", return_value=2), \
             mock.patch("telegram.preparedActivityCount", return_value=0):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("input=pending:2", report)
        self.assertIn("turn=failed:20s-ago", report)
        self.assertNotIn("turn=pending", report)

    def test_a_long_rest_under_autonomous_presets_is_not_a_stale_model_turn(self):
        """The watcher can roll back on problems, so a legitimate rest must
        not look like a hung mind just because the mode is autonomous."""
        for mode in ("godelclaw", "coding"):
            with self.subTest(mode=mode):
                self.write(self.channel, {
                    "menu_status": "ok", "last_poll_ok_at": 999,
                    "loop_status": "waiting", "waiting_until": 1200,
                })
                self.write(self.working, {"saved_at": 999, "loops": 0})
                self.write(self.cognitive, {
                    "last_completed_at": 1, "pending_since": 0,
                    "last_outcome": "completed",
                })
                self.write(self.mode, {"mode": mode})
                with mock.patch.object(runtime_health.memory_health, "drift",
                                       return_value=(100, 95, 5)), \
                     mock.patch.object(runtime_health.memory_health,
                                       "threshold", return_value=20):
                    value = runtime_health.status(now=1000)
                self.assertFalse(value["cognition_required"])
                self.assertNotIn("model-turn-stale", value["problems"])
                self.assertEqual(value["state"], "ok")

    def test_pending_continuation_is_scheduled_not_working(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": True,
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": False})
        # A mode that does not renew itself.
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: scheduled", report)
        self.assertIn("wake=timer-or-human", report)
        self.assertIn("continuation=pending", report)

    def test_a_stale_pause_flag_no_longer_fakes_a_rest(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": True})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"), \
             mock.patch("telegram.rest_status", return_value=(False, 0)):
            report = runtime_health.activity_report(now=1000)
        # No sleep in flight: an exhausted budget is idle, not resting.
        self.assertIn("activity: idle", report)
        self.assertIn("rest=none", report)
        self.assertNotIn("until-human", report)

    def test_active_timed_rest_reports_real_remaining_time(self):
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
            "intent": "quiet integration",
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": True})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"), \
             mock.patch("telegram.rest_status", return_value=(True, 417)):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: resting", report)
        self.assertIn("wake=timer-or-human", report)
        self.assertIn("rest=417s-left:quiet integration", report)

    def test_resting_reports_the_banked_budget_not_zero(self):
        """Rest banks the budget, so the sleeping agent still has energy."""
        self.write(self.working, {
            "saved_at": 999, "loops": 0, "continuation_pending": False,
            "banked_loops": 43,
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": True})
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"), \
             mock.patch("telegram.rest_status", return_value=(True, 20)):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("activity: resting", report)
        self.assertIn("steps=43@banked", report)
        self.assertNotIn("steps=0", report)

    def test_finished_rest_does_not_keep_reporting_its_intention(self):
        """The intention is consumed at boot, so it outlives a rest that
        ended by waking; it must not trail a rest=none line."""
        self.write(self.working, {
            "saved_at": 999, "loops": 12, "continuation_pending": False,
            "intent": "peek w7 for a reply",
        })
        self.write(self.cognitive, {
            "last_completed_at": 995, "pending_since": 0,
            "last_outcome": "completed",
        })
        self.write(self.mode, {"mode": "godelclaw", "autonomy_paused": False})
        # A mode that does not renew itself.
        with mock.patch("loop_modes.current_mode", return_value="godelclaw"), \
             mock.patch("loop_modes.autonomous", return_value=False), \
             mock.patch("engine_modes.active_engine", return_value="petta"), \
             mock.patch("synthetic_llm.current_model", return_value="glm"), \
             mock.patch("telegram.rest_status", return_value=(False, 0)):
            report = runtime_health.activity_report(now=1000)
        self.assertIn("rest=none", report)
        self.assertNotIn("peek w7 for a reply", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
