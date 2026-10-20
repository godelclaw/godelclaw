import json
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import loop_modes  # noqa: E402


class LoopModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("METTACLAW_LOOP_MODE_PATH")
        self.path = pathlib.Path(self.tmp.name) / "loop_mode.json"
        os.environ["METTACLAW_LOOP_MODE_PATH"] = str(self.path)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("METTACLAW_LOOP_MODE_PATH", None)
        else:
            os.environ["METTACLAW_LOOP_MODE_PATH"] = self.previous
        self.tmp.cleanup()

    def test_default_is_godelclaw(self):
        self.assertEqual(loop_modes.current_mode(), "godelclaw")

    def test_historical_names_are_compatibility_aliases(self):
        for historical, current in (("default", "godelclaw"),
                                    ("generic", "godelclaw"),
                                    ("agent", "godelclaw"),
                                    ("claw23", "godelclaw"),
                                    ("iter-coding", "coding")):
            self.path.write_text(json.dumps({"mode": historical}) + "\n")
            self.assertEqual(loop_modes.current_mode(), current)
            self.assertIn("alias", loop_modes.set_mode(historical))
            self.assertEqual(json.loads(self.path.read_text())["mode"], current)
            self.assertNotIn(historical, loop_modes.MODES)

    def test_godelclaw_persists(self):
        self.assertIn("persists", loop_modes.set_mode("godelclaw"))
        self.assertEqual(loop_modes.current_mode(), "godelclaw")
        self.assertEqual(json.loads(self.path.read_text())["mode"], "godelclaw")

    def test_mode_state_carries_only_the_mode(self):
        """Rest suspends renewal by being in progress, not by leaving a flag
        behind that something else has to remember to clear."""
        loop_modes.set_mode("godelclaw")
        self.assertEqual(json.loads(self.path.read_text()), {"mode": "godelclaw"})
        self.assertFalse(hasattr(loop_modes, "pause_autonomy"))
        self.assertFalse(hasattr(loop_modes, "autonomy_paused"))

    def test_a_stale_pause_flag_on_disk_is_ignored(self):
        self.path.write_text(
            json.dumps({"mode": "godelclaw", "autonomy_paused": True}),
            encoding="utf-8")
        self.assertEqual(loop_modes.current_mode(), "godelclaw")

    def test_coding_is_selectable_and_persistent(self):
        self.assertIn("persists", loop_modes.set_mode("coding"))
        self.assertEqual(loop_modes.current_mode(), "coding")
        self.assertEqual(json.loads(self.path.read_text())["mode"], "coding")

    def test_qualified_upstream_modes_are_selectable(self):
        for mode in ("iter", "omega"):
            self.assertNotIn(mode, loop_modes.ALIASES)
            self.assertIn("persists", loop_modes.set_mode(mode))
            self.assertEqual(loop_modes.current_mode(), mode)

    def test_autonomy_follows_the_mode_through_its_aliases(self):
        for name in ("godelclaw", "coding", "iter", "iter-coding", "agent"):
            self.assertTrue(loop_modes.autonomous(name), name)
        self.assertFalse(loop_modes.autonomous("unknown"))

    def test_wait_result_normalization_only_reports_adapter_fact(self):
        self.assertEqual(loop_modes.wait_timed_out("rested 60s"), 1)
        self.assertEqual(loop_modes.wait_timed_out("woken by operator message"), 0)

    def test_unknown_mode_does_not_change_state(self):
        loop_modes.set_mode("godelclaw")
        self.assertIn("failed", loop_modes.set_mode("unknown"))
        self.assertEqual(loop_modes.current_mode(), "godelclaw")


if __name__ == "__main__":
    unittest.main()
