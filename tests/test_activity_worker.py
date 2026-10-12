"""Owned start/results, uncertainty recovery and bounded worker retention."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from activity_worker import Cache, Controller, WorkerError, packed


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.cache = Cache(self.path, "version", retained=2)
        self.addCleanup(lambda: self.cache.close())

    def test_started_job_recovers_unknown_without_becoming_runnable(self):
        self.cache.add("call", "captured", "background"); self.cache.start("call")
        self.cache.close(); self.cache = Cache(self.path, "version")
        self.assertEqual(self.cache.rows("queued"), [])
        self.assertEqual(self.cache.rows("started"), [])
        self.assertEqual(json.loads(self.cache.rows("result")[0]["result"])["status"], "unknown")

    def test_recorded_result_recovers_without_provider_replay(self):
        self.cache.add("call", "captured", "background"); self.cache.start("call")
        directory = self.path / "call"; directory.mkdir(mode=0o700)
        result = directory / "result.json"
        result.write_text(packed({"status": "completed", "value": '(shell "inert")'})); result.chmod(0o600)
        self.cache.close(); self.cache = Cache(self.path, "version")
        self.assertEqual(json.loads(self.cache.rows("result")[0]["result"])["value"], '(shell "inert")')
        self.assertEqual(self.cache.rows("queued"), [])

    def test_same_id_with_different_observation_is_a_conflict(self):
        self.cache.add("call", "captured", "background")
        self.cache.add("call", "captured", "background")
        with self.assertRaises(WorkerError): self.cache.add("call", "changed", "background")

    def test_recovery_finishes_cleanup_after_the_stored_receipt(self):
        self.cache.add("call", "captured", "background"); self.cache.start("call")
        self.cache.result("call", packed({"status": "completed", "value": "stored"}))
        directory = self.path / "call"; directory.mkdir(mode=0o700)
        (directory / "buffer").write_text("retire only after receipt")
        self.cache.write("UPDATE jobs SET state='reported' WHERE id=?", ("call",))
        self.cache.close(); self.cache = Cache(self.path, "version")
        self.assertFalse(directory.exists())
        self.assertEqual(len(self.cache.rows("reported")), 1)

    def test_changed_adapter_is_rejected_before_process_or_provider_start(self):
        self.cache.add("call", "captured", "background")
        controller = Controller.__new__(Controller)
        controller.cache = self.cache; controller.args = SimpleNamespace()
        with mock.patch("activity_worker.fingerprint", return_value="changed"), mock.patch("subprocess.Popen") as start:
            controller.launch(self.cache.rows("queued")[0])
            start.assert_not_called()
        self.assertEqual(json.loads(self.cache.rows("result")[0]["result"])["reason"], "captured-adapter-changed")

    def test_retention_releases_only_reported_work_and_keeps_receipts(self):
        self.cache.add("pending", "owned", "interactive"); self.cache.start("pending")
        for index in range(5):
            identity = f"r{index}"
            self.cache.add(identity, "capture", "background"); self.cache.start(identity)
            self.cache.result(identity, packed({"status": "completed", "value": "result"}))
            self.cache.reported(identity)
        self.assertEqual(len(self.cache.rows("reported")), 2)
        self.assertEqual([r["id"] for r in self.cache.rows("started")], ["pending"])
        with self.assertRaises(WorkerError): self.cache.add("r0", "capture", "background")

    def test_second_controller_cannot_acquire_cache(self):
        with self.assertRaises(BlockingIOError): Cache(self.path, "version")

    def test_saved_result_is_never_overwritten_by_a_different_result(self):
        self.cache.add("call", "capture", "interactive")
        self.cache.result("call", packed({"status": "completed", "value": "first"}))
        with self.assertRaises(WorkerError):
            self.cache.result("call", packed({"status": "completed", "value": "different"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
