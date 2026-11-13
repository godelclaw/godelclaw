"""Only real progress renews the external watchdog; owned waits stay bounded."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import process_supervision as supervision
import durable_telegram


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch.dict(os.environ, METTACLAW_PROGRESS_PATH=str(self.root / "progress"),
                                NOTIFY_SOCKET=str(self.root / "socket"), WATCHDOG_USEC="1000000")
        patch.start()
        self.addCleanup(patch.stop)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(self.socket.close)
        self.socket.bind(str(self.root / "socket"))
        self.socket.settimeout(.05)
        self.clock = 100.
        clock = mock.patch.object(supervision.time, "monotonic", side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.account = {"bytes": 10, "high": 100, "max": 120}
        account = mock.patch.object(supervision, "memory", side_effect=lambda *args: self.account)
        account.start()
        self.addCleanup(account.stop)

    def message(self):
        return dict(line.split("=", 1) for line in self.socket.recv(2048).decode().splitlines())

    # A submitted call's receipts (its provider wait and its data) are native:
    # tests/unit/progress_receipt.metta.
    def test_owned_wait_extends_the_watchdog_until_it_ends(self):
        supervision.provider_wait(20)
        self.assertEqual(self.message()["WATCHDOG_USEC"], "21000000")
        self.assertEqual(supervision.observation()["state"], "waiting")
        self.clock += 25
        supervision.progress("provider-result")
        self.assertEqual(self.message()["WATCHDOG_USEC"], "1000000")
        self.assertEqual(supervision.observation()["state"], "fresh")
        self.clock += 181
        self.assertEqual(supervision.observation()["state"], "stalled")

    def test_return_to_computation_restores_base_watchdog(self):
        supervision.provider_wait(20)
        self.message()
        supervision.progress("command-receipt")
        self.assertEqual(self.message()["WATCHDOG_USEC"], "1000000")
        self.account["bytes"] = 101
        self.assertEqual(supervision.observation()["state"], "memory-pressure")

    def test_missing_projection_cannot_revoke_a_completed_transition(self):
        with mock.patch.object(supervision.os, "replace", side_effect=OSError("disk fault")):
            self.assertEqual(supervision.progress("turn-checkpoint"), 1)
        self.assertEqual(self.message()["WATCHDOG"], "1")

    def test_memory_trigger_is_independent_of_background_jobs(self):
        self.account["bytes"] = 70
        self.assertTrue(supervision.recycle_due())
        with mock.patch.dict(os.environ, METTACLAW_RECYCLE_BYTES="80"):
            self.assertFalse(supervision.recycle_due())


class ChangesTests(unittest.TestCase):
    def test_local_pump_lock_does_not_wake_its_own_responder(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary, "pump.lock")
            lock.touch()
            changes = durable_telegram.Changes([temporary], ignore_names=("pump.lock",))
            self.addCleanup(changes.close)
            if changes.fd < 0:
                self.skipTest("inotify unavailable")
            changes.arm()
            with lock.open("a"):
                pass
            self.assertEqual(changes.wait(.02), 0)

    def test_filtered_event_preserves_a_later_durable_arrival(self):
        with tempfile.TemporaryDirectory() as temporary:
            changes = durable_telegram.Changes([temporary], ignore_names=("pump.lock",))
            self.addCleanup(changes.close)
            if changes.fd < 0:
                self.skipTest("inotify unavailable")
            changes.arm()
            Path(temporary, "pump.lock").write_text("local bookkeeping")
            arrived = threading.Timer(.02, lambda: Path(temporary, "journal").write_text("new task"))
            arrived.start()
            self.addCleanup(arrived.join)
            self.assertEqual(changes.wait(1), 1)

    def test_arrival_during_snapshot_is_not_a_lost_wakeup(self):
        with tempfile.TemporaryDirectory() as temporary:
            changes = durable_telegram.Changes([temporary])
            self.addCleanup(changes.close)
            if changes.fd < 0:
                self.skipTest("inotify unavailable")
            changes.arm()
            self.assertEqual(changes.wait(.01), 0)
            Path(temporary, "journal").write_text("an arrival during the snapshot")
            self.assertEqual(changes.wait(.1), 1)
            changes.arm()
            self.assertEqual(changes.wait(.01), 0)


if __name__ == "__main__":
    unittest.main()
