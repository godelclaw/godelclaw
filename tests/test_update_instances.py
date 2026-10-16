import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location("update_instances", Path(__file__).resolve().parents[1] / "scripts/update_instances.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class ReleasePermissionsTest(unittest.TestCase):
    def test_content_override_preserves_launcher_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "source"
            source.mkdir()
            (source / "run.sh").write_text("#!/bin/sh\nexit 0\n")
            (source / "run.sh").chmod(0o750)
            override = work / "override"
            override.write_text("#!/bin/sh\nexit 1\n")
            override.chmod(0o640)
            selection = work / "selection.json"
            selection.write_text(json.dumps(["run.sh"]))
            overrides = work / "overrides.json"
            overrides.write_text(json.dumps({"run.sh": str(override)}))
            script = Path(__file__).resolve().parents[1] / "scripts/build_runtime_release.py"
            subprocess.run([sys.executable, str(script), "--source", str(source),
                "--selection", str(selection), "--overrides", str(overrides),
                "--output", str(work / "release")], check=True, capture_output=True)
            launcher = work / "release/files/run.sh"
            self.assertEqual(launcher.read_bytes(), override.read_bytes())
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o750)

    def test_sync_status_compares_only_the_common_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work/'source'; source.mkdir()
            (source/'core.metta').write_text('current')
            selection = work/'files.json'; selection.write_text('["core.metta"]')
            instances = []
            for name, content in (('first', 'old'), ('second', 'current')):
                root = work/name; root.mkdir()
                (root/'core.metta').write_text(content)
                (root/'private.txt').write_text('private sentinel')
                instances.append(dict(name=name, root=str(root)))
            config = work/'instances.json'
            config.write_text(json.dumps(dict(selection='files.json', instances=instances)))
            script = Path(__file__).resolve().parents[1]/'scripts/sync_instances.py'
            result = subprocess.run([sys.executable, str(script), '--source', str(source),
                                     '--config', str(config), '--status'],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)['instances'],
                             [dict(name='first', different=['core.metta']),
                              dict(name='second', different=[])])
            self.assertNotIn('private sentinel', result.stdout)

    def test_current_code_does_not_hide_an_old_running_engine(self):
        path = Path(__file__).resolve().parents[1]/'scripts/sync_instances.py'
        spec = importlib.util.spec_from_file_location('sync_instances_test', path)
        sync = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'update_instances': update}):
            spec.loader.exec_module(sync)
        config = dict(instances=[dict(service='fixture', required_runtime=dict(CETTA_BIN='new'))])
        with mock.patch.object(update, 'running_settings', return_value=dict(CETTA_BIN='old')):
            self.assertFalse(sync.runtime_current(config))
        with mock.patch.object(update, 'running_settings', return_value=dict(CETTA_BIN='new')):
            self.assertTrue(sync.runtime_current(config))


class PairedUpdateTest(unittest.TestCase):
    def exercise(self, failed=False, runtime_missing=False, controls=False, control_failure=False,
                 shared_environment=False, single=False):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            release = work / "release"
            (release / "files/src").mkdir(parents=True)
            (release / "files/run.sh").write_text("#!/bin/sh\n# new launcher\n")
            (release / "files/src/core.py").write_text("# new core\n")
            hashes = {n: update.sha(release / "files" / n) for n in ("run.sh", "src/core.py")}
            hashes["src/obsolete.py"] = None
            (release / "manifest.json").write_text(json.dumps({"release": "checked", "files": hashes}))
            instances = []
            for name in ("alpha", "beta"):
                root, state = work / name, work / (name + "-state")
                (root / "src").mkdir(parents=True); state.mkdir()
                (root / "run.sh").write_text("# old launcher\n")
                (root / "src/core.py").write_text("# old core\n")
                (root / "src/obsolete.py").write_text("# removed feature\n")
                (state / "identity").write_text(name)
                (root / "memory").mkdir()
                (root / "memory/persistent.metta").write_text('(active-model '+name+')\n')
                instances.append({"name": name, "root": str(root), "state_dir": str(state),
                    "service": name, "checks": [["offline", name]],
                    "required_runtime": {"METTACLAW_OBSERVED_STRATEGY": "1"},
                    "expected": {n: update.sha(root / n) for n in hashes}})
                if controls:
                    instances[-1]["control_services"] = [name + "-control"]
            plan = work / "plan.json"
            selected = instances[:1] if single else instances
            plan.write_text(json.dumps({"instances": selected,
                                        "boot_timeout": -1 if runtime_missing else 60}))
            if shared_environment:
                old_env, new_env = work/'common.env', work/'candidate.env'
                old_env.write_text('CETTA_BIN=old\n'); new_env.write_text('CETTA_BIN=new\n')
                value = json.loads(plan.read_text())
                value['shared_environment'] = dict(path=str(old_env), source=str(new_env))
                plan.write_text(json.dumps(value))
            pids = {"alpha": "1", "beta": "1"}
            control_restarts = []
            qualified = []
            def service(name, operation):
                if name.endswith("-control"):
                    if operation == "restart":
                        self.assertEqual(len(qualified), len(selected))
                        control_restarts.append(name)
                        if control_failure and len(control_restarts) == 1:
                            raise RuntimeError("control startup failed")
                    return "9"
                item = next(i for i in instances if i["name"] == name)
                state = Path(item["state_dir"])
                if operation == "restart":
                    pids[name] = str(int(pids[name]) + 1)
                    (state / "active-loop.json").write_text(json.dumps({"pid": int(pids[name])}))
                elif (state / "recycle.requested").exists() and pids[name] == "1":
                    pids[name] = "2"
                elif ((state / "recycle.requested").exists()
                      and (state / "recycle.requested").read_text().strip() == "rollback"):
                    self.assertEqual((Path(item["root"]) / "src/core.py").read_text(), "# new core\n")
                    self.assertTrue((Path(item["root"]) / ".runtime-update-hold").exists())
                    pids[name] = str(int(pids[name]) + 1)
                    (state / "recycle.requested").unlink()
                return pids[name]
            def check(command, **kwargs):
                qualified.append(command[-1])
                return subprocess.CompletedProcess(command, int(failed and command[-1] == selected[-1]["name"]), "CHECK_OK", "")
            def settings(name, extra_keys=()):
                state = next(i["state_dir"] for i in instances if i["name"] == name)
                return {"METTACLAW_ACTIVE_LOOP_MODE": "godelclaw",
                        "METTACLAW_OBSERVED_STRATEGY": "0" if runtime_missing and name == selected[-1]["name"] else "1",
                        "METTACLAW_ENGINE_STATE_PATH": state + "/engine",
                        "METTACLAW_RECYCLE_REQUEST_PATH": state + "/recycle.requested",
                        "METTACLAW_WAKE_REQUEST_PATH": state + "/wake.requested"}
            args = ["update_instances", "--plan", str(plan), "--release", str(release),
                    "--receipt-dir", str(work / "receipt"), "--apply"]
            with mock.patch.object(sys, "argv", args), mock.patch.object(update, "service", service), mock.patch.object(update, "running_settings", settings), mock.patch.object(update.subprocess, "run", check):
                if runtime_missing:
                    with self.assertRaises(TimeoutError): update.main()
                elif failed or control_failure:
                    with self.assertRaises(RuntimeError): update.main()
                else:
                    self.assertEqual(update.main(), 0)
            receipt = json.loads((work / "receipt/receipt.json").read_text())
            rollback = failed or runtime_missing or control_failure
            self.assertEqual(receipt["status"], "rolled-back" if rollback else "activated")
            self.assertEqual([item['name'] for item in receipt['instances']],
                             [item['name'] for item in selected])
            self.assertEqual(qualified, [item['name'] for item in selected])
            if shared_environment:
                self.assertEqual(old_env.read_text(), 'CETTA_BIN=old\n' if rollback else 'CETTA_BIN=new\n')
            if controls:
                self.assertEqual(control_restarts, [] if failed else
                    ["alpha-control", "alpha-control"] if control_failure else
                    [item['name'] + '-control' for item in selected] * (2 if runtime_missing else 1))
            for item in instances:
                root = Path(item["root"])
                unchanged = rollback or item not in selected
                self.assertEqual((root / "src/obsolete.py").exists(), unchanged)
                if unchanged:
                    self.assertEqual((root / "src/obsolete.py").read_text(), "# removed feature\n")
                self.assertFalse((root / ".runtime-update-hold").exists())
                self.assertEqual((Path(item["state_dir"]) / "identity").read_text(), item["name"])
                self.assertEqual((root / "memory/persistent.metta").read_text(),
                                 '(active-model '+item['name']+')\n')
                self.assertEqual((root / "src/core.py").read_text(), "# old core\n" if unchanged else "# new core\n")
                self.assertEqual((root / "run.sh").read_text(), "# old launcher\n" if unchanged else "#!/bin/sh\n# new launcher\n")
                if item not in selected:
                    self.assertEqual(pids[item['name']], '1')
                    self.assertFalse((Path(item['state_dir']) / 'recycle.requested').exists())
                    self.assertFalse((Path(item['state_dir']) / 'runtime-release.json').exists())

    def test_single_instance_updates_without_touching_its_peer(self):
        self.exercise(single=True, controls=True)

    def test_single_instance_failed_check_rolls_back_without_touching_its_peer(self):
        self.exercise(single=True, failed=True, controls=True)

    def test_single_instance_failed_boot_rolls_back_without_touching_its_peer(self):
        self.exercise(single=True, runtime_missing=True, controls=True)

    def test_empty_selection_does_not_observe_or_change_a_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'manifest.json').write_text(json.dumps({'release': 'empty', 'files': {}}))
            (work / 'plan.json').write_text(json.dumps({'instances': []}))
            args = ['update_instances', '--plan', str(work / 'plan.json'), '--release', str(work),
                    '--receipt-dir', str(work / 'receipt'), '--apply']
            with mock.patch.object(sys, 'argv', args), mock.patch.object(update, 'service') as service:
                with self.assertRaisesRegex(ValueError, 'at least one instance'):
                    update.main()
                service.assert_not_called()
            self.assertFalse((work / 'receipt').exists())

    def test_both_receive_one_release_with_separate_state(self):
        self.exercise()

    def test_second_failure_rolls_back_both(self):
        self.exercise(failed=True)

    def test_inactive_runtime_configuration_cannot_pass_activation(self):
        self.exercise(runtime_missing=True)

    def test_controls_restart_only_after_the_whole_group_qualifies(self):
        self.exercise(controls=True)

    def test_failed_qualification_never_restarts_controls(self):
        self.exercise(failed=True, controls=True)

    def test_control_failure_restores_the_old_group_and_responder(self):
        self.exercise(controls=True, control_failure=True)

    def test_shared_engine_environment_activates_with_the_group(self):
        self.exercise(shared_environment=True)

    def test_shared_engine_environment_rolls_back_with_the_group(self):
        self.exercise(shared_environment=True, runtime_missing=True)


if __name__ == "__main__": unittest.main()
