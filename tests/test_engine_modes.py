"""The CeTTa process: its command and the recycle request. The failure receipt's
view is native: tests/unit/runtime_health.metta."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import engine_modes  # noqa: E402


class EngineModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(self.tmp.name, "state", "recycle.requested"),
            "CETTA_BIN": os.path.join(self.tmp.name, "bundle", "cetta"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("METTACLAW_ACTIVE_LOOP_MODE",):
            saved = os.environ.pop(name, None)
            if saved is not None:
                self.addCleanup(os.environ.__setitem__, name, saved)

    def test_the_command_is_the_configured_cetta(self):
        self.assertEqual(engine_modes.engine_command(), (os.environ["CETTA_BIN"],))
        self.assertEqual(engine_modes.engine_command("cetta"), (os.environ["CETTA_BIN"],))
        self.assertEqual(engine_modes.engine_command("petta"), ())

    def test_recycle_request_is_atomic_and_private(self):
        self.assertTrue(engine_modes.request_recycle("mode-switch"))
        path = os.environ["METTACLAW_RECYCLE_REQUEST_PATH"]
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, encoding="utf-8") as stream:
            self.assertIn("reason=mode-switch", stream.read())
        self.assertTrue(engine_modes.recycle_requested())

    def test_without_a_flag_path_nothing_is_requested(self):
        with mock.patch.dict(os.environ, {"METTACLAW_RECYCLE_REQUEST_PATH": ""}):
            self.assertFalse(engine_modes.request_recycle())
            self.assertFalse(engine_modes.recycle_requested())


if __name__ == "__main__":
    unittest.main(verbosity=2)
