"""Owned start/results, uncertainty recovery and bounded worker retention."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import hashlib
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from activity_worker import Cache, Client, Controller, WorkerError, packed


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.cache = Cache(self.path, "version", retained=2)
        self.addCleanup(lambda: self.cache.close())

    def test_model_process_keeps_its_configured_ledger_and_controller_engine(self):
        controller = Controller.__new__(Controller)
        controller.args = SimpleNamespace(engine=self.path / 'bundle/cetta', budget=self.path / 'budget',
                                         parallel=3, reserve_interactive=1, max_tokens=64)
        with mock.patch.dict(os.environ, {'METTACLAW_INPUT_LEDGER_SOCKET': '/fixture/policy.sock',
                                         'CETTA_BIN': '/unrelated/cetta',
                                         'CETTA_VALUE_WORKER_BIN': '/unrelated/cetta-value-worker',
                                         'METTACLAW_ENGINE_STATE_PATH': '/unrelated/state'}):
            env = controller.model_environment('owned-call', self.path, dict(lane='background', model='hf:fixture'))
        self.assertEqual(env['METTACLAW_INPUT_LEDGER_SOCKET'], '/fixture/policy.sock')
        self.assertEqual(env['CETTA_BIN'], str(controller.args.engine))
        self.assertNotIn('CETTA_VALUE_WORKER_BIN', env)
        self.assertEqual(env['METTACLAW_ENGINE_STATE_PATH'], str(self.path / 'engine_state.json'))

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

    def test_recovery_preserves_tool_artifacts_after_the_stored_receipt(self):
        self.cache.add("call", "captured", "background"); self.cache.start("call")
        self.cache.result("call", packed({"status": "completed", "value": "stored"}))
        directory = self.path / "call"; directory.mkdir(mode=0o700)
        (directory / "buffer").write_text("retire only after receipt")
        self.cache.write("UPDATE jobs SET state='reported' WHERE id=?", ("call",))
        self.cache.close(); self.cache = Cache(self.path, "version")
        self.assertTrue(directory.exists())
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

    def test_default_retention_keeps_every_completed_identity(self):
        self.cache.close(); self.cache = Cache(self.path, "version")
        for i in range(300):
            identity = "retained-%03d" % i
            self.cache.add(identity, "captured", "background")
            self.cache.result(identity, packed({"status": "completed", "value": "evidence"}))
            self.cache.reported(identity)
        self.assertEqual(len(self.cache.rows("reported")), 300)
        self.assertEqual(self.cache.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 300)
        self.cache.close(); self.cache = Cache(self.path, "version")
        with self.assertRaises(WorkerError): self.cache.add("retained-000", "different", "background")

    def test_pending_worker_stream_continues_past_128(self):
        client = Client("unused")
        client.rpc = mock.Mock(side_effect=[(65, "t%d" % n, "body") for n in range(1024)] + [(64, "", "")])
        self.assertEqual(len(list(client.pending())), 1024)
        self.assertEqual(client.rpc.call_args, mock.call(1, "t1023"))

    def test_tool_output_is_spooled_and_artifacts_survive_reporting(self):
        args = SimpleNamespace(state=self.path, tools={"fixture": Path(sys.executable)})
        script = "import sys; from pathlib import Path; Path('produced.txt').write_text('retained'); sys.stdout.write('x'*2000000); sys.stderr.write('e'*100000)"
        body = packed({"kind": "tool", "lane": "interactive", "tool": "fixture", "arguments": ["-c", script] + ["arg"] * 32})
        self.cache.add("large-tool", body, "interactive")
        controller = Controller.__new__(Controller)
        controller.cache, controller.args, controller.running = self.cache, args, {}
        with mock.patch("activity_worker.fingerprint", return_value="version"):
            controller.launch(self.cache.rows("queued")[0])
        deadline = time.monotonic() + 10
        while controller.running and time.monotonic() < deadline:
            controller.collect(); time.sleep(.01)
        self.assertFalse(controller.running)
        result = json.loads(self.cache.rows("result")[0]["result"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output_bytes"], 2000000)
        raw = Path(result["output_artifact"]["path"]).read_bytes()
        self.assertEqual(raw, b"x" * 2000000)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["output_digest"])
        self.assertEqual(Path(result["stderr_artifact"]["path"]).read_bytes(), b"e" * 100000)
        self.cache.reported("large-tool")
        self.assertEqual((self.path / "large-tool/produced.txt").read_text(), "retained")

    def test_storage_exhaustion_cancels_a_tool_without_claiming_no_effect(self):
        args = SimpleNamespace(state=self.path, tools={"fixture": Path(sys.executable)})
        script = "import sys; sys.stdout.write('x'*2000000)"
        body = packed({"kind": "tool", "lane": "interactive", "tool": "fixture", "arguments": ["-c", script]})
        self.cache.add("full-disk-tool", body, "interactive")
        controller = Controller.__new__(Controller)
        controller.cache, controller.args, controller.running = self.cache, args, {}
        with mock.patch("activity_worker.fingerprint", return_value="version"), \
             mock.patch.dict(os.environ, {"METTACLAW_TOOL_STORAGE_RESERVE_BYTES": str(2**63)}):
            controller.launch(self.cache.rows("queued")[0])
        deadline = time.monotonic() + 5
        while controller.running and time.monotonic() < deadline:
            controller.collect(); time.sleep(.01)
        self.assertFalse(controller.running)
        value = json.loads(self.cache.rows("result")[0]["result"])
        self.assertEqual(value["status"], "unknown")
        self.assertEqual(value["reason"], "storage-exhausted")
        self.assertEqual(value["effects"], "unknown")
        self.assertFalse(value["output_artifact"]["complete"])

    def test_retained_job_history_can_grow_beyond_the_old_database_ceiling(self):
        # Many individually small records; no oversized channel frame is needed.
        self.cache.db.execute("BEGIN IMMEDIATE")
        self.cache.db.executemany("INSERT INTO jobs VALUES(?,?,?,'reported','background',NULL,0)",
                                 (("history-%d" % n, "x" * 4096, "version") for n in range(9000)))
        self.cache.db.execute("COMMIT")
        self.assertGreater(self.cache.db.execute("PRAGMA page_count").fetchone()[0], 8192)
        self.assertEqual(self.cache.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 9000)

    def test_reporting_yields_without_losing_results_or_blocking_future_steps(self):
        for n in range(300):
            identity = "report-%d" % n
            self.cache.add(identity, "captured", "interactive")
            self.cache.result(identity, packed({"status": "completed", "value": "evidence"}))
        controller = Controller.__new__(Controller)
        controller.cache, controller.running, controller.after = self.cache, {}, ""
        controller.args = SimpleNamespace(intake_ms=.01, parallel=2, reserve_interactive=1)
        controller.client = SimpleNamespace(rpc=mock.Mock(return_value=(66, "", "")), pending=lambda after: iter(()))
        for _ in range(310):
            controller.step()
            if not self.cache.db.execute("SELECT 1 FROM jobs WHERE state='result' LIMIT 1").fetchone(): break
        self.assertFalse(self.cache.rows("result"))
        self.assertEqual(self.cache.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 300)

    def test_second_controller_cannot_acquire_cache(self):
        with self.assertRaises(BlockingIOError): Cache(self.path, "version")

    def test_saved_result_is_never_overwritten_by_a_different_result(self):
        self.cache.add("call", "capture", "interactive")
        self.cache.result("call", packed({"status": "completed", "value": "first"}))
        with self.assertRaises(WorkerError):
            self.cache.result("call", packed({"status": "completed", "value": "different"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
