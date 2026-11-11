"""Native working-state transitions, restart safety, and ledger integration."""
import hermetic_env  # noqa: F401,E402  (first, before any src module)
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]

import helper
import input_ack
import native_ledger
from observed_strategy import Ledger


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


class NativeWorkingStateFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.working = self.directory / "working.json"
        self.env = {
            **isolated_environ(),
            "PYTHONHOME": sys.prefix,
            "PYTHONNOUSERSITE": "1",
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": os.pathsep.join(str(ROOT / name) for name in ("tests", "src", "channels")),
            "METTACLAW_ENGINE": "cetta",
            "METTACLAW_ACTIVE_ENGINE": "cetta",
            "METTACLAW_OBSERVED_STRATEGY": "0",
            "METTACLAW_WORKING_SET_PATH": str(self.working),
            "TMPDIR": str(self.directory),
        }

    def program(self, forms, sentinel=True):
        lines = [
            "!(import! &self (library lib_import))",
            "!(import! &self ./src/helper.py)",
            "!(import! &self ./src/engine_modes.metta)",
            "!(import! &self ./src/working_state.metta)",
            "!(import! &self ./tests/working_state_probe.py)",
        ]
        if sentinel:
            lines.append("!(py-call (working_state_probe.forbid))")
        return "\n".join(lines + list(forms)) + "\n"

    def start_native(self, forms, *, sentinel=True, env=None):
        handle = tempfile.NamedTemporaryFile(
            "w", dir=ROOT, prefix="working-state-native-", suffix=".metta",
            encoding="utf-8", delete=False)
        with handle:
            handle.write(self.program(forms, sentinel))
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        environment = dict(self.env)
        if env:
            environment.update(env)
        return subprocess.Popen(
            [os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", handle.name],
            cwd=self.directory, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def run_native(self, forms, *, sentinel=True, env=None, check=True):
        process = self.start_native(forms, sentinel=sentinel, env=env)
        stdout, stderr = process.communicate(timeout=120)
        if os.environ.get("WORKING_STATE_TEST_VERBOSE"):
            print("NATIVE STDOUT\n%s\nNATIVE STDERR\n%s" % (stdout, stderr))
        if check and (process.returncode or "uncaught" in stderr or "ModuleParseFailed" in stdout + stderr):
            self.fail((process.returncode, stdout[-4000:], stderr[-4000:]))
        return process, stdout, stderr

    def test_pure_transition_and_boot_match_python_at_fixed_times(self):
        state_out = self.directory / "native-state.json"
        cache_out = self.directory / "native-cache.json"
        persisted_out = self.directory / "native-persisted.json"
        due_early = self.directory / "native-due-early"
        due_late = self.directory / "native-due-late"
        wait = self.directory / "native-wait"
        stamp = "2026-10-25 12:00:00"
        self.run_native([
            "!(let* (($state (working-state:transition (JsonObject ()) "
            "(WorkingSaveV1 0 60 \"earlier evidence\" 90 1 17 0) 100.0)) "
            "($timer (working-state:transition $state (WorkingRestScheduleV1 60 (shell \"printf scheduled\")) 100.0)) "
            "($with-intent (working-state:transition $timer (WorkingIntentV1 \"waiting on build\") 100.0)) "
            "($boot (working-state:boot-transition $with-intent 120.0 %s)) "
            "($_ (py-call (working_state_probe.record %s (working-state:encoded $with-intent)))) "
            "($_ (py-call (working_state_probe.record %s (repr (working-state:rest-due-at $with-intent 1 120.0))))) "
            "($_ (py-call (working_state_probe.record %s (repr (working-state:rest-due-at $with-intent 0 160.0))))) "
            "($_ (py-call (working_state_probe.record %s (repr (working-state:wait-at $with-intent 5000 90 30 100.0)))))) "
            "(let (WorkingBootV1 $persisted $cache $write) $boot "
            "(let* (($_ (py-call (working_state_probe.record %s (working-state:encoded $persisted)))) "
            "($_ (py-call (working_state_probe.record %s (working-state:encoded $cache))))) 1)))" % (
                q(stamp), q(state_out), q(due_early), q(due_late), q(wait),
                q(persisted_out), q(cache_out)),
        ])
        python_path = self.directory / "python.json"
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_OBSERVED_STRATEGY": "0",
            "METTACLAW_WORKING_SET_PATH": str(python_path),
        })
        with environment, mock.patch.object(helper.time, "time", return_value=100.0):
            helper.working_set_save(0, 60, "earlier evidence", 90, 1, 17)
            helper.rest_schedule(60, ["shell", "printf scheduled"])
            helper.intent_save("waiting on build")
            expected_wait = helper.wait_seconds(5000, 90, 30)
        expected_state = json.loads(python_path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(state_out.read_text(encoding="utf-8")), expected_state)
        self.assertEqual(due_early.read_text(), "0")
        self.assertEqual(due_late.read_text(), "1")
        self.assertEqual(wait.read_text(), str(expected_wait))
        with environment, mock.patch.object(helper.time, "time", return_value=120.0), \
                mock.patch.object(helper.time, "strftime", return_value=stamp):
            helper.working_boot()
            expected_cache = dict(helper._working_boot_cache)
        self.assertEqual(json.loads(cache_out.read_text(encoding="utf-8")), expected_cache)
        self.assertEqual(json.loads(persisted_out.read_text(encoding="utf-8")),
                         json.loads(python_path.read_text(encoding="utf-8")))
        self.assertEqual(expected_cache["sleepInterval"], 40)
        self.assertEqual(expected_cache["banked_loops"], 17)
        self.assertIn("WOKE_FROM_REST: waiting on build", expected_cache["lastresults"])

    def test_missing_file_snapshot_has_one_boot_and_pending_answer(self):
        self.run_native([
            "!(assert (== (collapse (working-state:read-file)) ((JsonObject ()))))",
            '!(assert (== (collapse (working:rest-pending)) ("no-continuation")))',
            "!(assert (== (collapse (working:boot)) (1)))",
        ])

    def test_working_set_and_workspace_stores_compose_in_either_import_order(self):
        workspace = self.directory / 'workspaces.metta'
        for order in [('working_state', 'workspaces_cetta'),
                      ('workspaces_cetta', 'working_state')]:
            with self.subTest(order=order):
                self.working.unlink(missing_ok=True)
                workspace.unlink(missing_ok=True)
                source = self.directory / ('compose-' + order[0] + '.metta')
                forms = [
                    '!(import! &self (library lib_import))',
                    '!(import! &self ' + q(ROOT / 'src/helper.py') + ')',
                    *['!(import! &self ' + q(ROOT / ('src/' + name + '.metta')) + ')' for name in order],
                    '!(assert (== (collapse (ws:path)) (%s)))' % q(workspace),
                    '!(assert (== (collapse (working-state:path)) (%s)))' % q(self.working),
                    '!(assert (== (collapse (working:save 0 60 "feedback" 90 1 17 0)) (1)))',
                    '!(let $before (fs:read-text %s)\n'
                    '   (let $_ (workspace "focus" ("all") () "")\n'
                    '     (assert (== $before (fs:read-text %s)))))' % (q(self.working), q(self.working)),
                    '!(assert (== (size-atom (collapse (workspace))) 1))',
                    '!(println! "WORKING_WORKSPACE_COMPOSE_OK")',
                ]
                source.write_text('\n'.join(forms) + '\n')
                result = subprocess.run(
                    [os.environ['CETTA_BIN'], '--lang', 'petta', '--import-mode', 'ancestor-walk', str(source)],
                    cwd=ROOT, env=dict(self.env, METTACLAW_WORKSPACES_PATH=str(workspace)),
                    capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
                self.assertIn('WORKING_WORKSPACE_COMPOSE_OK', result.stdout)
                self.assertEqual(json.loads(self.working.read_text())['banked_loops'], 17)
                self.assertTrue(workspace.read_text().startswith('(WorkspaceSnapshot 1 '))

    def test_interrupted_boot_transition_reports_unknown_and_removes_timer(self):
        cache_out = self.directory / "interrupted-cache.json"
        persisted_out = self.directory / "interrupted-persisted.json"
        stamp = "2026-10-25 12:00:00"
        self.run_native([
            "!(let* (($timer (working-state:transition (JsonObject ()) "
            "(WorkingRestScheduleV1 1 (send \"hello\")) 100.0)) "
            "($running (working-state:transition $timer WorkingRestClaimV1 100.0)) "
            "($boot (working-state:boot-transition $running 101.0 %s))) "
            "(let (WorkingBootV1 $persisted $cache $write) $boot "
            "(let* (($_ (py-call (working_state_probe.record %s (working-state:encoded $persisted)))) "
            "($_ (py-call (working_state_probe.record %s (working-state:encoded $cache))))) 1)))" % (
                q(stamp), q(persisted_out), q(cache_out)),
        ])
        cache = json.loads(cache_out.read_text(encoding="utf-8"))
        persisted = json.loads(persisted_out.read_text(encoding="utf-8"))
        self.assertNotIn("rest_timer", persisted)
        self.assertIn("outcome unknown; not replayed", cache["lastresults"])
        self.assertIn("hello", cache["lastresults"])

    def test_real_restart_and_crash_after_claim_never_replay_command(self):
        first = self.directory / "first-save"
        self.run_native([
            "!(py-call (working_state_probe.record %s (working:save 0 60 \"earlier evidence\" 90 1 17 0)))" % q(first),
            '!(working:rest-schedule 120 (shell "printf scheduled"))',
            "!(working:rest-hold 120)",
            '!(working:intent-save "restart rest")',
        ])
        self.assertEqual(first.read_text(), "1")
        stored = json.loads(self.working.read_text(encoding="utf-8"))
        self.assertEqual((stored["banked_loops"], stored["rest_timer"]["status"]), (17, "waiting"))

        boot = self.directory / "restart-boot.json"
        pending = self.directory / "restart-pending.json"
        due = self.directory / "restart-due"
        second = self.start_native([
            "!(working:boot)",
            "!(py-call (working_state_probe.record %s (working-state:encoded (get-state &working-state-boot))))" % q(boot),
            "!(let $pending (working:rest-pending) (case $pending "
            "(((\"continuation-value\" $command) "
            "(py-call (working_state_probe.record %s (working-state:encoded (JsonArray $command))))) "
            "($other (Error unexpected-pending $other)))))" % q(pending),
            "!(py-call (working_state_probe.record %s (repr (working:rest-due 1))))" % q(due),
            "!(working:rest-claim)",
            "!(py-call (working_state_probe.crash))",
        ])
        stdout, stderr = second.communicate(timeout=120)
        self.assertEqual(second.returncode, -signal.SIGKILL, (stdout, stderr))
        restored = json.loads(boot.read_text(encoding="utf-8"))
        self.assertEqual(restored["banked_loops"], 17)
        self.assertIn("WOKE_FROM_REST: restart rest", restored["lastresults"])
        self.assertLessEqual(restored["sleepInterval"], 120)
        self.assertEqual(json.loads(pending.read_text(encoding="utf-8")), ["shell", "printf scheduled"])
        self.assertEqual(due.read_text(), "0", "a heartbeat must not fire the timer early")

        final_boot = self.directory / "final-boot.json"
        final_pending = self.directory / "final-pending"
        self.run_native([
            "!(working:boot)",
            "!(py-call (working_state_probe.record %s (working-state:encoded (get-state &working-state-boot))))" % q(final_boot),
            "!(py-call (working_state_probe.record %s (repr (working:rest-pending))))" % q(final_pending),
        ])
        final = json.loads(final_boot.read_text(encoding="utf-8"))
        self.assertIn("SCHEDULED_COMMAND_INTERRUPTED: outcome unknown; not replayed", final["lastresults"])
        self.assertNotIn("WOKE_FROM_REST", final["lastresults"])
        self.assertEqual(final_pending.read_text(), '"no-continuation"')
        self.assertNotIn("rest_timer", json.loads(self.working.read_text(encoding="utf-8")))


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to the Python-enabled CeTTa bundle")
class NativeWorkingStateTests(NativeWorkingStateFixture, unittest.TestCase):
    pass


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to the Python-enabled CeTTa bundle")
class NativeWorkingLedgerTests(NativeWorkingStateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        native_ledger.start(self, self.directory)
        self.env["METTACLAW_OBSERVED_STRATEGY"] = "1"
        self.env[input_ack.LEDGER_SOCKET] = str(self.ledger_server.socket)
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1"}):
            self.store = Ledger(self.directory / "strategy")
            self.store.accept("input:one", "message", kind="message")

    def committed(self, ids):
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1"}):
            return [input_ack.already_committed(identity) for identity in ids]

    def test_scope_snapshot_commit_and_projection_failure(self):
        saved = self.directory / "managed-save"
        scope_shape = self.directory / "scope-shape"
        read_shape = self.directory / "read-shape"
        staged = self.directory / "staged"
        self.run_native([
            '!(py-call (working_state_probe.record %s (repr (py-call (working_state_host.ledger_read)))))' % q(read_shape),
            '!(py-call (working_state_probe.scope ("runtime:101" "102") "input:one"))',
            '!(py-call (working_state_probe.record %s (repr (py-call (working_state_host.commit_scope 1)))))' % q(scope_shape),
            '!(py-call (working_state_probe.record %s (working:turn-commit-stage 1 1 1)))' % q(staged),
            '!(py-call (working_state_probe.scope ("wrong") "wrong-request"))',
            '!(py-call (working_state_probe.record %s (working:save 7 3 "committed" 12 0 4 0)))' % q(saved),
        ])
        self.assertEqual(staged.read_text(), "1", (read_shape.read_text(), scope_shape.read_text()))
        self.assertEqual(saved.read_text(), "1")
        self.assertEqual(self.committed(["runtime:101", "102", "wrong"]), [1, 1, 0])
        self.assertEqual(self.store.request_record("input:one")["processed"], 1)
        self.assertEqual(self.store.working_checkpoint()["lastresults"], "committed")

        # A committed native checkpoint remains successful when its operator
        # projection cannot be replaced; no false file authority is introduced.
        projection = self.directory / "projection-directory"
        projection.mkdir()
        projected = self.directory / "projected-save"
        self.run_native([
            "!(working:turn-commit-reset)",
            '!(py-call (working_state_probe.record %s (working:save 8 3 "after projection fault" 13 0 4 0)))' % q(projected),
        ], env={"METTACLAW_WORKING_SET_PATH": str(projection)})
        self.assertEqual(projected.read_text(), "1")
        self.assertEqual(self.store.working_checkpoint()["loops"], 8)
        self.assertTrue(projection.is_dir())

    def test_lost_reply_replays_native_checkpoint_without_double_settlement(self):
        proxy, faults = native_ledger.faulty(
            self, self.directory, self.ledger_server.socket,
            marker=b"checkpoint", lose="reply", once=True)
        saved = self.directory / "lost-reply-save"
        self.run_native([
            '!(py-call (working_state_probe.scope ("lost:101") "input:one"))',
            "!(working:turn-commit-stage 1 1 1)",
            '!(py-call (working_state_probe.record %s (working:save 9 3 "lost reply" 14 0 4 0)))' % q(saved),
        ], env={input_ack.LEDGER_SOCKET: proxy})
        self.assertEqual(saved.read_text(), "1")
        self.assertEqual(faults, ["reply"])
        self.assertEqual(self.committed(["lost:101"]), [1])
        self.assertEqual(self.store.request_record("input:one")["processed"], 1)
        self.assertEqual(self.store.working_checkpoint()["lastresults"], "lost reply")

    def test_competing_native_checkpoints_commit_once_each(self):
        processes = []
        reports = []
        for name, identity in (("a", "race:a"), ("b", "race:b")):
            report = self.directory / ("race-" + name)
            reports.append(report)
            processes.append(self.start_native([
                '!(py-call (working_state_probe.scope ("%s") "input:one"))' % identity,
                "!(working:turn-commit-stage 1 1 1)",
                '!(py-call (working_state_probe.record %s (working:save 10 3 "%s" 15 0 4 0)))' % (
                    q(report), name),
            ]))
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, (stdout, stderr))
        self.assertEqual([path.read_text() for path in reports], ["1", "1"])
        self.assertEqual(self.committed(["race:a", "race:b"]), [1, 1])
        self.assertIn(self.store.working_checkpoint()["lastresults"], {"a", "b"})
        self.assertEqual(self.store.request_record("input:one")["processed"], 1)


if __name__ == "__main__":
    unittest.main()
