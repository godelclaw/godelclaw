import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_watch  # noqa: E402


OPERATOR_COMMANDS = """\
def dispatch(cmd):
    if cmd in ("/start", "/stop"):
        pass
"""
NATIVE_LOOP = """\
(= (turnCandidate $p)
   (case (lifecycle:enabled) (($x $x))))
(= (waitCandidate $p)
   (case (lifecycle:enabled) (($x $x))))
"""
NATIVE_LIFECYCLE = """\
(= (lifecycle-native:enabled) 1)
(= (lifecycle-native:start) "started")
(= (lifecycle-native:stop) "stopped")
"""
NATIVE_FACADE = """\
(add-atom &self (= (lifecycle:enabled) (lifecycle-native:enabled)))
(add-atom &self (= (lifecycle:start) (lifecycle-native:start)))
(add-atom &self (= (lifecycle:stop) (lifecycle-native:stop)))
"""
LEGACY_LOOP = """\
(= (turnCandidate $p)
   (case (py-call (lifecycle.cognition_enabled)) (($x $x))))
(= (waitCandidate $p)
   (case (py-call (lifecycle.cognition_enabled)) (($x $x))))
"""
LEGACY_LIFECYCLE = """\
def cognition_enabled():
    return 1
def start():
    return "started"
def stop():
    return "stopped"
"""


def lifecycle_revision(native):
    if native:
        return {
            "channels/telegram.py": OPERATOR_COMMANDS,
            "src/lifecycle_cetta.metta": NATIVE_LIFECYCLE,
            "src/loop.metta": NATIVE_LOOP,
            "src/mode_state.metta": NATIVE_FACADE,
        }
    return {
        "channels/telegram.py": OPERATOR_COMMANDS,
        "src/lifecycle.py": LEGACY_LIFECYCLE,
        "src/loop.metta": LEGACY_LOOP,
    }


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
            "probation_until": 2000,
        }

    def test_legitimate_rest_is_healthy(self):
        facts = {"state": "ok", "problems": []}
        with mock.patch.object(runtime_watch.runtime_health, "status",
                               return_value=facts), \
             mock.patch.object(runtime_watch, "_service_active",
                               return_value=True), \
             mock.patch.object(runtime_watch, "_head",
                               return_value="candidate"):
            value = runtime_watch.observe(self.args, self.deployment, now=1000)
        self.assertEqual(value["problems"], [])

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

    def assert_revision_authority(self, files, expected, revision):
        def revision_file(command, cwd=None, check=False):
            self.assertEqual(cwd, self.args.root)
            self.assertEqual(command[:2], ["git", "show"])
            self.assertFalse(check)
            target, relative = command[2].split(":", 1)
            self.assertEqual(target, revision)
            content = files.get(relative)
            return mock.Mock(returncode=int(content is None),
                             stdout=content or "")

        with mock.patch.object(runtime_watch, "_run",
                               side_effect=revision_file) as run:
            self.assertIs(
                runtime_watch._generation_has_lifecycle_authority(
                    self.args.root, revision), expected)
        self.assertGreater(run.call_count, 0)

    def test_old_and_current_revision_fixtures_are_admissible(self):
        for revision, native in (("legacy-fixture", False),
                                 ("current-fixture", True)):
            with self.subTest(revision=revision):
                self.assert_revision_authority(
                    lifecycle_revision(native), True, revision)

    def test_revision_fence_rejects_missing_or_inert_authority(self):
        missing_native_definition = lifecycle_revision(True)
        missing_native_definition["src/lifecycle_cetta.metta"] = (
            NATIVE_LIFECYCLE.replace(
                '(= (lifecycle-native:stop) "stopped")', ""))
        missing_native_facade = lifecycle_revision(True)
        missing_native_facade["src/mode_state.metta"] = ""
        missing_native_gate = lifecycle_revision(True)
        missing_native_gate["src/loop.metta"] = NATIVE_LOOP.replace(
            "(case (lifecycle:enabled)", "(case (lifecycle:other)", 1)
        commented_native_gates = lifecycle_revision(True)
        commented_native_gates["src/loop.metta"] = NATIVE_LOOP.replace(
            "(case (lifecycle:enabled)", "; (case (lifecycle:enabled)")
        quoted_native_gates = lifecycle_revision(True)
        quoted_native_gates["src/loop.metta"] = NATIVE_LOOP.replace(
            "(case (lifecycle:enabled)", '"(case (lifecycle:enabled)"')
        missing_legacy_definition = lifecycle_revision(False)
        missing_legacy_definition["src/lifecycle.py"] = (
            LEGACY_LIFECYCLE.replace("def stop():", "def retired_stop():"))
        missing_legacy_gate = lifecycle_revision(False)
        missing_legacy_gate["src/loop.metta"] = LEGACY_LOOP.replace(
            "(lifecycle.cognition_enabled)", "(lifecycle.retired)", 1)
        commented_commands = lifecycle_revision(True)
        commented_commands["channels/telegram.py"] = (
            "# if cmd in (\"/start\", \"/stop\"): pass\n")
        missing_loop = lifecycle_revision(True)
        del missing_loop["src/loop.metta"]
        adjacent_gate = lifecycle_revision(True)
        adjacent_gate["src/loop.metta"] = (
            "(= (turnCandidate $p) $p)\n!(lifecycle:enabled)\n" +
            NATIVE_LOOP[NATIVE_LOOP.index("(= (waitCandidate"):])
        wrong_comparison = lifecycle_revision(True)
        wrong_comparison["channels/telegram.py"] = OPERATOR_COMMANDS.replace(
            'cmd in', 'cmd !=')
        cases = {
            "adjacent-gate": adjacent_gate,
            "wrong-command-comparison": wrong_comparison,
            "missing-native-definition": missing_native_definition,
            "missing-native-facade": missing_native_facade,
            "missing-native-gate": missing_native_gate,
            "commented-native-gates": commented_native_gates,
            "quoted-native-gates": quoted_native_gates,
            "missing-legacy-definition": missing_legacy_definition,
            "missing-legacy-gate": missing_legacy_gate,
            "commented-commands": commented_commands,
            "missing-loop": missing_loop,
        }
        for revision, files in cases.items():
            with self.subTest(revision=revision):
                self.assert_revision_authority(files, False, revision)

    def test_current_lifecycle_generation_is_admissible(self):
        if not (ROOT := pathlib.Path(__file__).resolve().parents[1]).is_dir():
            self.skipTest("repository unavailable")
        head = runtime_watch._head(ROOT)
        self.assertTrue(
            runtime_watch._generation_has_lifecycle_authority(ROOT, head))


if __name__ == "__main__":
    unittest.main(verbosity=2)
