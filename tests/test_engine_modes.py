"""The CeTTa process: its command, its last failure receipt, and the recycle request."""

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
            "METTACLAW_ENGINE_FAILURE_PATH": os.path.join(self.tmp.name, "state", "engine-failure"),
            "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(self.tmp.name, "state", "recycle.requested"),
            "CETTA_BIN": os.path.join(self.tmp.name, "bundle", "cetta"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("METTACLAW_ACTIVE_LOOP_MODE",):
            saved = os.environ.pop(name, None)
            if saved is not None:
                self.addCleanup(os.environ.__setitem__, name, saved)

    def write_failure(self, text):
        path = os.environ["METTACLAW_ENGINE_FAILURE_PATH"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)

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

    def test_last_failure_is_shown_when_recorded(self):
        self.assertIsNone(engine_modes.last_failure())
        self.assertEqual(engine_modes.failure_view(), "none")
        self.write_failure("schema=1\nengine=cetta\nstatus=7\nreason=CeTTa process exited\n")
        self.assertEqual(engine_modes.last_failure()["reason"], "CeTTa process exited")
        self.assertEqual(engine_modes.failure_view(), "CeTTa process exited (status 7)")

    def test_a_receipt_without_a_reason_is_no_failure(self):
        self.write_failure("schema=1\nengine=cetta\nstatus=7\n")
        self.assertIsNone(engine_modes.last_failure())


if __name__ == "__main__":
    unittest.main(verbosity=2)
