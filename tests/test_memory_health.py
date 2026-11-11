"""Memory health states the configured backend and whether its native service is reachable."""

import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import memory_health  # noqa: E402


class MemoryHealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="mh-", dir="/tmp"))  # a unix socket path must be short
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.config = self.directory / "memory_backend.json"
        environment = mock.patch.dict(os.environ, {"METTACLAW_MEMORY_BACKEND_CONFIG": str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)

    def configure(self, **fields):
        self.config.write_text(json.dumps(fields), encoding="utf-8")

    def test_no_configuration_is_reported_as_such(self):
        self.assertEqual(memory_health.backend(), {"mode": "unconfigured"})
        self.assertIn("no native memory is configured (backend unconfigured)", memory_health.report())

    def test_an_unreadable_configuration_counts_as_none(self):
        self.config.write_text("{not json", encoding="utf-8")
        self.assertEqual(memory_health.backend(), {"mode": "unconfigured"})

    def test_native_backend_with_its_service_listening(self):
        path = self.directory / "memory.sock"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.listen(1)
        self.addCleanup(listener.close)
        self.configure(mode="native", name="memories-test", socket=str(path))
        self.assertEqual(memory_health.backend(), {"mode": "native", "name": "memories-test", "socket": str(path)})
        self.assertTrue(memory_health.native_reachable(path))
        self.assertEqual(memory_health.report(), "memory-health: native backend memories-test — service reachable")

    def test_native_backend_without_its_service(self):
        self.configure(mode="native", name="memories-test", socket=str(self.directory / "absent.sock"))
        self.assertFalse(memory_health.native_reachable(self.directory / "absent.sock"))
        self.assertIn("service UNREACHABLE", memory_health.report())

    def test_a_missing_socket_setting_is_unreachable_not_an_error(self):
        self.configure(mode="native", name="memories-test")
        self.assertFalse(memory_health.native_reachable(None))
        self.assertIn("service UNREACHABLE", memory_health.report())

    def test_boot_check_prints_the_report(self):
        with mock.patch("builtins.print") as printed:
            line = memory_health.boot_check()
        printed.assert_called_once_with("[memory_health] " + line, flush=True)


if __name__ == "__main__":
    unittest.main()
