"""runtime_health.status, which the deployment watcher reads. The responder's
/health and /activity are native: tests/unit/runtime_health.metta."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import runtime_health  # noqa: E402


class RuntimeHealthTest(unittest.TestCase):
    def test_external_control_and_upstream_checkpoint_replace_legacy_client_health(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            receipt = root / "modes/control-status.json"
            receipt.parent.mkdir()
            self.write(receipt, {"observed_at": 995, "channel_ready": True})
            self.write(self.channel, {"last_poll_ok_at": 1})
            self.write(self.working, {"saved_at": 1})
            self.write(self.mode, {"mode": "iter"})
            self.write(self.cognitive, {"schema": 2, "last_completed_at": 997, "last_settled_at": 998})
            with mock.patch.dict(os.environ, {"METTACLAW_ENGINE_STATE_PATH": str(root / "engine")}):
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
        # The deployed memory: the native service, reachable.
        for name, value in (("backend", {"mode": "native", "name": "memories-test", "socket": "memory.sock"}),
                            ("native_reachable", True)):
            patch = mock.patch.object(runtime_health.memory_health, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.lifecycle.stop)
        self.addCleanup(self.lifecycle_view.stop)

    def test_stopped_lifecycle_needs_channel_but_not_cognition(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 1, "loops": 50})
        self.write(self.cognitive, {"pending_since": 100})
        self.write(self.mode, {"mode": "godelclaw"})
        with mock.patch("lifecycle.cognition_enabled", return_value=0), \
             mock.patch("lifecycle.view", return_value="stopped"):
            value = runtime_health.status(now=1000)
        self.assertFalse(value["cognition_required"])
        self.assertNotIn("cognitive-boundary-stale", value["problems"])
        self.assertNotIn("model-turn-overdue", value["problems"])

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_healthy_wait_is_not_mistaken_for_a_hang(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 1})
        with mock.patch.object(runtime_health, "_generation",
                               return_value="abcdef012345"):
            value = runtime_health.status(now=1000)
        self.assertEqual(value["state"], "ok")
        self.assertEqual(value["generation"], "abcdef012345")

    def test_stale_poll_and_awake_loop_fail_independently(self):
        self.write(self.channel, {
            "last_poll_ok_at": 100,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 1})
        value = runtime_health.status(now=1000)
        self.assertIn("telegram-poll-stale", value["problems"])
        self.assertIn("cognitive-boundary-stale", value["problems"])

    def test_fresh_completed_model_turn_is_healthy_when_armed(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 999, "loops": 5})
        self.write(self.cognitive, {
            "last_completed_at": 995, "completed_count": 4,
            "pending_since": 0, "last_outcome": "completed",
        })
        value = runtime_health.status(now=1000)
        self.assertNotIn("model-turn-stale", value["problems"])
        self.assertEqual(value["model_turn_age_seconds"], 5)

    def test_old_pending_turn_detects_empty_spinning(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
        })
        self.write(self.working, {"saved_at": 999, "loops": 42})
        self.write(self.cognitive, {
            "pending_since": 600, "last_expected_at": 999,
            "expected_count": 300, "completed_count": 0,
        })
        value = runtime_health.status(now=1000)
        self.assertIn("model-turn-overdue", value["problems"])
        self.assertEqual(value["model_turn_pending_age_seconds"], 400)

    def test_new_pending_turn_gets_grace_despite_old_completion(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "awake", "waiting_until": 0,
        })
        self.write(self.working, {"saved_at": 999, "loops": 5})
        self.write(self.cognitive, {
            "pending_since": 995, "last_completed_at": 100,
            "expected_count": 2, "completed_count": 1,
        })
        value = runtime_health.status(now=1000)
        self.assertNotIn("model-turn-stale", value["problems"])
        self.assertNotIn("model-turn-overdue", value["problems"])

    def test_intentional_default_idle_needs_no_model_receipt(self):
        self.write(self.channel, {
            "last_poll_ok_at": 999,
            "loop_status": "waiting", "waiting_until": 1200,
            "started_at": 1,
        })
        self.write(self.working, {"saved_at": 999, "loops": 0})
        self.write(self.mode, {"mode": "default", "autonomy_paused": False})
        value = runtime_health.status(now=1000)
        self.assertFalse(value["cognition_required"])
        self.assertNotIn("model-turn-receipt-missing", value["problems"])

    def test_a_long_rest_under_autonomous_presets_is_not_a_stale_model_turn(self):
        """The watcher can roll back on problems, so a legitimate rest must
        not look like a hung mind just because the mode is autonomous."""
        for mode in ("godelclaw", "coding"):
            with self.subTest(mode=mode):
                self.write(self.channel, {
                    "last_poll_ok_at": 999,
                    "loop_status": "waiting", "waiting_until": 1200,
                })
                self.write(self.working, {"saved_at": 999, "loops": 0})
                self.write(self.cognitive, {
                    "last_completed_at": 1, "pending_since": 0,
                    "last_outcome": "completed",
                })
                self.write(self.mode, {"mode": mode})
                value = runtime_health.status(now=1000)
                self.assertFalse(value["cognition_required"])
                self.assertNotIn("model-turn-stale", value["problems"])
                self.assertEqual(value["state"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
