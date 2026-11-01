"""The wait mode is retained as data, independently of the available budget."""
import sys
from pathlib import Path
from unittest import mock
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper


class ActivityWaitPersistence(unittest.TestCase):
    def test_wait_mode_survives_a_working_snapshot_and_boot(self):
        saved = {}
        def write(data, **kwargs):
            saved.clear(); saved.update(data)
            return True
        with mock.patch.object(helper, "_working_read", side_effect=lambda: dict(saved)), \
                mock.patch.object(helper, "_working_write", side_effect=write), \
                mock.patch.dict(sys.modules, {"process_supervision": types.SimpleNamespace(progress=lambda *args: None)}), \
                mock.patch.object(helper, "_working_boot_cache", None):
            self.assertEqual(helper.working_set_save(0, 60, "waiting", 100, 0, 43, 1), 1)
            self.assertTrue(saved["rest_await_input"])
            helper.working_boot()
            self.assertEqual(helper.boot_int("rest_await_input", 0), 1)
            self.assertEqual(helper.boot_int("banked_loops", 0), 43)
            self.assertEqual(helper.boot_int("loops", -1), 0)

    def test_legacy_callers_keep_the_previous_default(self):
        captured = {}
        with mock.patch.object(helper, "_working_read", return_value={}), \
                mock.patch.object(helper, "_working_write", side_effect=lambda data, **kwargs: captured.update(data)), \
                mock.patch.dict(sys.modules, {"process_supervision": types.SimpleNamespace(progress=lambda *args: None)}):
            self.assertEqual(helper.working_set_save(3, 1, "working", 100), 1)
            self.assertFalse(captured["rest_await_input"])

    def test_retained_intent_does_not_erase_a_scope_qualifier(self):
        captured = {}
        text = "context " * 100 + " applies only to this task and pane; never a global restriction"
        with mock.patch.object(helper, "_working_read", return_value={}), \
                mock.patch.object(helper, "_working_write", side_effect=lambda data: captured.update(data)):
            self.assertEqual(helper.intent_save(text), 1)
        self.assertEqual(captured["intent"], text)


if __name__ == "__main__": unittest.main()
