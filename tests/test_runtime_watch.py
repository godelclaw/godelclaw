import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_watch  # noqa: E402


class MemoryObservationTest(unittest.TestCase):
    def sample(self, at, ram, swap=0, invocation="first"):
        return {"at": at, "invocation": invocation, "pid": 42,
                "ram_bytes": ram * 2**20, "swap_bytes": swap * 2**20,
                "total_bytes": (ram + swap) * 2**20}

    def test_swap_does_not_hide_growth(self):
        before = {"history": [self.sample(100, 200)]}
        facts = runtime_watch.memory_trend(before, self.sample(280, 100, 200), 180, 16, 32)
        self.assertTrue(facts["alarm"])
        self.assertAlmostEqual(facts["growth_mib_per_minute"], 100 / 3)

    def test_restarted_service_does_not_inherit_old_trend(self):
        before = {"history": [self.sample(100, 30)]}
        facts = runtime_watch.memory_trend(before, self.sample(400, 300, invocation="new"), 180, 16, 32)
        self.assertFalse(facts["alarm"])
        self.assertEqual(len(facts["history"]), 1)

    def test_recovery_and_disabled_warning(self):
        before = {"history": [self.sample(100, 200)]}
        self.assertFalse(runtime_watch.memory_trend(before, self.sample(280, 100), 180, 16, 32)["alarm"])
        self.assertFalse(runtime_watch.memory_trend(before, self.sample(280, 400), 180, 0, 32)["alarm"])

    def test_memory_command_only_reads_service_properties(self):
        with tempfile.TemporaryDirectory() as folder:
            state = pathlib.Path(folder) / "observations.json"
            runtime_watch._write_json(state, {"services": {"agent.service": {
                "history": [self.sample(100, 30)]}}})
            args = runtime_watch.memory_parser().parse_args([
                "--units", "agent.service", "--state", str(state)])
            props = "InvocationID=first\nMainPID=42\nActiveState=active\nMemoryCurrent=314572800\nMemorySwapCurrent=0\n"
            with mock.patch.object(runtime_watch, "_run", return_value=mock.Mock(stdout=props)) as run:
                self.assertEqual(runtime_watch.observe_memory(args, now=280), 0)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:4], ["systemctl", "--user", "show", "agent.service"])
            self.assertEqual(json.loads(state.read_text())["alarms"], ["agent.service"])

    def test_active_process_cannot_hide_a_missing_installed_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            state = pathlib.Path(folder) / "observations.json"
            args = runtime_watch.memory_parser().parse_args([
                "--units", "gateway.service", "--state", str(state)])
            props = ("InvocationID=first\nMainPID=42\nActiveState=active\n"
                     "LoadState=not-found\nMemoryCurrent=31457280\nMemorySwapCurrent=0\n")
            with mock.patch.object(runtime_watch, "_run", return_value=mock.Mock(stdout=props)) as run:
                self.assertEqual(runtime_watch.observe_memory(args, now=280), 0)
            value = json.loads(state.read_text())
            self.assertEqual(value["restart_unavailable"], ["gateway.service"])
            self.assertEqual(value["alarms"], [])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:3], ["systemctl", "--user", "show"])

    def test_missing_unit_warning_does_not_require_memory_accounting(self):
        with tempfile.TemporaryDirectory() as folder:
            state = pathlib.Path(folder) / "observations.json"
            args = runtime_watch.memory_parser().parse_args([
                "--units", "gateway.service", "--state", str(state)])
            props = "MainPID=42\nActiveState=active\nLoadState=not-found\n"
            with mock.patch.object(runtime_watch, "_run", return_value=mock.Mock(stdout=props)):
                runtime_watch.observe_memory(args, now=280)
            self.assertEqual(json.loads(state.read_text())["restart_unavailable"], ["gateway.service"])


class RuntimeWatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = pathlib.Path(self.temporary.name)
        self.args = mock.Mock(
            root=root, service="agent.service", health=root / "health.json",
            working_set=root / "working.json",
            canonical_memory=root / "memory.jsonl", chroma=root / "chroma",
            deployment=root / "deployment.json", log=root / "watch.jsonl",
            failures=3,
        )
        self.args.canonical_memory.write_bytes(b"preserved")
        self.deployment = {
            "status": "probation", "candidate": "candidate",
            "previous": "previous", "baseline_canonical_bytes": 9,
            "baseline_memories": 100, "probation_until": 2000,
        }

    def test_legitimate_rest_is_healthy(self):
        facts = {"state": "ok", "problems": [], "memories": 100}
        with mock.patch.object(runtime_watch.runtime_health, "status",
                               return_value=facts), \
             mock.patch.object(runtime_watch, "_service_active",
                               return_value=True), \
             mock.patch.object(runtime_watch, "_head",
                               return_value="candidate"):
            value = runtime_watch.observe(self.args, self.deployment, now=1000)
        self.assertEqual(value["problems"], [])

    def test_memory_regression_is_a_failure(self):
        facts = {"state": "ok", "problems": [], "memories": 99}
        with mock.patch.object(runtime_watch.runtime_health, "status",
                               return_value=facts), \
             mock.patch.object(runtime_watch, "_service_active",
                               return_value=True), \
             mock.patch.object(runtime_watch, "_head",
                               return_value="candidate"):
            value = runtime_watch.observe(self.args, self.deployment, now=1000)
        self.assertIn("memory-count-regressed", value["problems"])

    def test_third_probation_failure_invokes_rollback(self):
        state = dict(self.deployment, consecutive_failures=2)
        self.args.deployment.write_text(json.dumps(state), encoding="utf-8")
        observation = {"problems": ["telegram-poll-stale"], "active": True,
                       "head": "candidate", "runtime": {},
                       "observed_at": 1000, "canonical_memory_bytes": 9}
        with mock.patch.object(runtime_watch, "observe",
                               return_value=observation), \
             mock.patch.object(runtime_watch, "_rollback",
                               return_value=(True, "rolled-back")) as rollback:
            code = runtime_watch.manage(self.args, now=1000)
        self.assertEqual(code, 1)
        rollback.assert_called_once()
        saved = json.loads(self.args.deployment.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "rolled_back")

    def test_rollback_refuses_target_without_lifecycle_authority(self):
        observation = {"head": "candidate"}
        with mock.patch.object(
                runtime_watch, "_generation_has_lifecycle_authority",
                return_value=False), \
             mock.patch.object(runtime_watch, "_run") as run:
            success, action = runtime_watch._rollback(
                self.args, self.deployment, observation)
        self.assertFalse(success)
        self.assertEqual(action, "rollback-refused-lifecycle-regression")
        run.assert_not_called()

    def test_current_lifecycle_generation_is_admissible(self):
        if not (ROOT := pathlib.Path(__file__).resolve().parents[1]).is_dir():
            self.skipTest("repository unavailable")
        head = runtime_watch._head(ROOT)
        self.assertTrue(
            runtime_watch._generation_has_lifecycle_authority(ROOT, head))


if __name__ == "__main__":
    unittest.main(verbosity=2)
