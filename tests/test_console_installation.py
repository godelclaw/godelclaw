"""Offline packaging tests: no agent, mail service, provider or live terminal."""
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_console_release as builder
import install_console as installer


class ConsoleInstallationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="console-suite-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.suite = Path(cls.temporary.name)
        cls.release = cls.suite / "built release"
        cls.manifest = builder.build(ROOT, cls.release)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=self.suite)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.prefix = self.base / "prefix with ' quote ; $dollar"
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        self.backup = self.base / "backup"

    def plan(self, **kwargs):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return installer.prepare(self.release, self.prefix, Path(sys.executable), **kwargs)

    def apply(self, plan):
        with installer.locked(self.prefix):
            installer.apply(plan, self.backup)

    def assert_before(self, plan):
        for entry in plan["entries"]:
            self.assertEqual(installer.snapshot(Path(entry["path"])), entry["before"])

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/install_console.py"), *map(str, args)],
                              env=self.env, capture_output=True, text=True)

    def test_reproducible_build_and_refusal_to_overwrite(self):
        other = self.base / "other"
        self.assertEqual(builder.build(ROOT, other), self.manifest)
        self.assertEqual((other / "manifest.json").read_bytes(), (self.release / "manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "overwrite"):
            builder.build(ROOT, other)

    def test_missing_dependency_and_wrong_version_leave_no_output(self):
        for distribution in (importlib.metadata.PackageNotFoundError("missing"),
                             mock.Mock(version="wrong")):
            output = self.base / "not-built"
            with mock.patch.object(builder.importlib.metadata, "distribution", side_effect=(
                    distribution if isinstance(distribution, Exception) else lambda name: distribution)):
                with self.assertRaises((ValueError, importlib.metadata.PackageNotFoundError)):
                    builder.build(ROOT, output)
            self.assertFalse(output.exists())

    def test_dependency_record_mismatch_is_rejected(self):
        item = mock.Mock(parts=("dependency.py",), suffix=".py", name="dependency.py")
        item.__str__ = lambda self: "dependency.py"
        item.hash.mode, item.hash.value = "sha256", "wrong"
        path = self.base / "dependency.py"
        path.write_text("changed dependency")
        distribution = mock.Mock(version="3.0.52", files=[item])
        distribution.locate_file.return_value = path
        with mock.patch.object(builder.importlib.metadata, "distribution", return_value=distribution):
            with self.assertRaisesRegex(ValueError, "RECORD mismatch"):
                builder.build(ROOT, self.base / "not-built")

    def test_inspect_is_nonmutating_and_apply_is_explicit(self):
        result = self.cli("--release", self.release, "--prefix", self.prefix, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["applied"])
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.backup.exists())
        result = self.cli("--release", self.release, "--prefix", self.prefix, "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.prefix.exists())

    def test_real_frontend_help_import_and_source_independence(self):
        source = self.base / "retired-source"
        names = [*json.loads((ROOT / "config/console-files.json").read_text()),
                 "config/console-files.json", "config/console-dependencies.json"]
        for name in names:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        release = self.base / "source-independent-release"
        builder.build(source, release)
        plan = installer.prepare(release, self.prefix, Path(sys.executable))
        self.apply(plan)
        shutil.rmtree(source)
        shutil.rmtree(release)
        for name in ("agent-console", "godel-cli"):
            result = subprocess.run([str(self.prefix / "bin" / name), "--help"], cwd=self.home,
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for flag in ("--agent", "--instances", "--root", "--state", "--client-state", "--mail-script"):
                self.assertIn(flag, result.stdout)
        entry = Path(plan["release_target"]) / "files/scripts/console_entry.py"
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(entry), "--check-console-imports"],
                                cwd=self.home, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "console imports OK")
        # Every installed import is a regular copied file, not a source/scratch link.
        builder.verify(Path(plan["release_target"]))
        self.assertFalse(list(Path(plan["release_target"]).rglob("*.pyc")))
        self.assertEqual(list(self.home.iterdir()), [])

    def stub_python(self):
        python = self.base / "stub interpreter ' ; $ with spaces"
        python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                          shlex.quote(str(self.base / "stub.py")) + ' "$@"\n')
        python.chmod(0o755)
        (self.base / "stub.py").write_text(
            "import json,sys\n"
            "if sys.argv[-1] == '--check-console-imports': print('console imports OK')\n"
            "elif '-c' in sys.argv: print(json.dumps(list(sys.version_info[:2])))\n"
            "else: print(json.dumps(sys.argv[1:]))\n")
        return python

    def test_argv_quoting_defaults_explicit_overrides_and_forwarding(self):
        python = self.stub_python()
        mail = self.base / "mail script ' ; $ with spaces.py"
        mail.write_text("# fixture, never executed\n")
        plan = installer.prepare(self.release, self.prefix, python, mail)
        self.apply(plan)
        explicit = ["--agent", "other agent", "--mail-script", "override.py", "--submit", "a ' ; $ *\nž"]
        for name, defaults in (("agent-console", []), ("godel-cli", ["--agent", "godel"])):
            result = subprocess.run([str(self.prefix / "bin" / name), *explicit],
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(result.stdout)
            self.assertEqual(argv[:3], ["-I", "-S", "-B"])
            self.assertEqual(argv[4:], [*defaults, "--mail-script", str(mail), *explicit])
            self.assertEqual(argv[3], str(Path(plan["release_target"]) / "files/scripts/console_entry.py"))
        # Check the real frontend parser, stopping before profile lookup or attachment.
        from ui.worklog import console
        for override in (["--agent", "other"], ["--agent=other"]):
            with mock.patch.object(console, "profile", side_effect=RuntimeError("stop before attach")) as profile, \
                 mock.patch.object(console.os, "umask"):
                with self.assertRaisesRegex(RuntimeError, "stop before attach"):
                    console.main(["--agent", "godel", *override])
                self.assertEqual(profile.call_args.args[0].agent, "other")

    def test_python_version_mismatch_and_modified_binding_are_rejected(self):
        python = self.stub_python()
        stub = self.base / "stub.py"
        stub.write_text(stub.read_text().replace("list(sys.version_info[:2])", "[0, 0]"))
        with self.assertRaisesRegex(ValueError, "major/minor"):
            installer.prepare(self.release, self.prefix, python)
        self.assertFalse(self.prefix.exists())
        plan = self.plan()
        self.apply(plan)
        binding = Path(plan["binding"]) / "agent-console"
        binding.chmod(0o755)
        binding.write_text("modified immutable binding")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.plan()
        self.assertEqual(binding.read_text(), "modified immutable binding")

    def test_custom_launcher_is_not_silently_destroyed_and_backup_is_exact(self):
        binary = self.prefix / "bin"
        binary.mkdir(parents=True)
        custom = binary / "agent-console"
        custom.write_bytes(b"#!/bin/sh\n# custom bytes\x00\xff\n")
        custom.chmod(0o751)
        (binary / "godel-cli").symlink_to("missing prior target with spaces")
        before = [installer.snapshot(binary / name) for name in ("agent-console", "godel-cli")]
        with self.assertRaisesRegex(ValueError, "replace-existing"):
            self.plan()
        self.assertEqual(installer.snapshot(custom), before[0])
        plan = self.plan(replace_existing=True)
        self.apply(plan)
        installer.rollback(self.backup, False)
        self.assertEqual(installer.snapshot(custom), plan["entries"][1]["after"])
        installer.rollback(self.backup, True)
        self.assert_before(plan)
        self.assertEqual([installer.snapshot(binary / name) for name in ("agent-console", "godel-cli")], before)
        installer.rollback(self.backup, True)  # Idempotent recovery.

    def test_failure_at_each_launcher_write_can_be_rolled_back(self):
        for fail_at in range(3):
            with self.subTest(fail_at=fail_at):
                self.prefix = self.base / f"prefix-{fail_at}"
                self.backup = self.base / f"backup-{fail_at}"
                plan = self.plan()
                real = installer.replace
                written = []

                def interrupted(path, value):
                    if str(path) in [e["path"] for e in plan["entries"]]:
                        if len(written) == fail_at:
                            raise KeyboardInterrupt("interrupted installation fixture")
                        written.append(path)
                    return real(path, value)

                with mock.patch.object(installer, "replace", side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        self.apply(plan)
                self.assertTrue((self.backup / "before.json").is_file())
                installer.rollback(self.backup, True)
                self.assert_before(plan)

    def test_abrupt_process_exit_has_a_usable_rollback_receipt(self):
        worker = self.base / "interrupted.py"
        worker.write_text(
            "import os,sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import install_console as i\n"
            f"prefix=Path({str(self.prefix)!r})\n"
            f"plan=i.prepare(Path({str(self.release)!r}), prefix, Path(sys.executable))\n"
            "real=i.replace\n"
            "def interrupted(path, value):\n"
            "    real(path, value)\n"
            "    if path.name == 'console-current': os._exit(77)\n"
            "i.replace=interrupted\n"
            f"with i.locked(prefix): i.apply(plan, Path({str(self.backup)!r}))\n")
        result = subprocess.run([sys.executable, "-B", str(worker)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertTrue((self.prefix / "share/godelclaw/console-current").is_symlink())
        result = self.cli("--rollback", self.backup, "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in installer.locations(self.prefix)[1]:
            self.assertEqual(installer.snapshot(path), {"kind": "absent"})

    def test_shared_selection_switch_and_rollback_preserve_both_entrypoints(self):
        plan = self.plan()
        self.apply(plan)
        first = installer.snapshot(self.prefix / "share/godelclaw/console-current")
        wrappers = {name: installer.snapshot(self.prefix / "bin" / name)
                    for name in ("agent-console", "godel-cli")}
        other = self.base / "next-release"
        shutil.copytree(self.release, other)
        license = other / "files/LICENSE"
        license.chmod(0o644)
        license.write_bytes(license.read_bytes() + b"\npackaging fixture\n")
        license.chmod(0o444)
        manifest = dict(self.manifest, files=builder.inventory(other))
        payload = {k: v for k, v in manifest.items() if k != "release"}
        manifest["release"] = builder.digest(builder.encoded(payload))
        path = other / "manifest.json"
        path.chmod(0o644)
        path.write_bytes(builder.encoded(manifest))
        path.chmod(0o444)
        # A second release changes only the common pointer, never either entry point.
        next_plan = installer.prepare(other, self.prefix, Path(sys.executable), replace_existing=True)
        self.backup = self.base / "next-backup"
        self.apply(next_plan)
        self.assertNotEqual(installer.snapshot(self.prefix / "share/godelclaw/console-current"), first)
        for name, before in wrappers.items():
            self.assertEqual(installer.snapshot(self.prefix / "bin" / name), before)
        installer.rollback(self.backup, True)
        self.assertEqual(installer.snapshot(self.prefix / "share/godelclaw/console-current"), first)

    def test_copy_failure_keeps_launchers_and_durable_backup(self):
        plan = self.plan()
        with mock.patch.object(installer.shutil, "copytree", side_effect=OSError("copy fixture failure")):
            with self.assertRaisesRegex(OSError, "copy fixture"):
                self.apply(plan)
        self.assert_before(plan)
        self.assertFalse(Path(plan["release_target"]).exists())
        installer.rollback(self.backup, True)

    def test_corrupt_staged_manifest_is_never_published(self):
        plan = self.plan()
        copytree = shutil.copytree

        def corrupt(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            path = Path(target) / "manifest.json"
            if path.is_file():
                path.chmod(0o644)
                path.write_bytes(path.read_bytes() + b"corrupt")
                path.chmod(0o444)
            return result

        with mock.patch.object(installer.shutil, "copytree", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "manifest"):
                self.apply(plan)
        self.assertFalse(Path(plan["release_target"]).exists())
        self.assert_before(plan)

    def test_existing_backup_refused_before_launcher_changes(self):
        plan = self.plan()
        self.backup.mkdir()
        (self.backup / "custom").write_text("preserve")
        with self.assertRaises(FileExistsError):
            self.apply(plan)
        self.assert_before(plan)
        self.assertEqual((self.backup / "custom").read_text(), "preserve")
        result = self.cli("--release", self.release, "--prefix", self.base / "untouched",
                          "--backup-dir", self.backup, "--apply")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.base / "untouched").exists())

    def test_invalid_apply_does_not_create_prefix_or_backup(self):
        result = self.cli("--release", self.base / "missing-release", "--prefix", self.prefix,
                          "--backup-dir", self.backup, "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.backup.exists())
        plan = self.plan()
        for backup in (self.prefix / "backup", self.release / "backup"):
            with self.assertRaisesRegex(ValueError, "outside"):
                installer.check_backup(backup, plan)

    def test_rollback_refuses_later_edit_or_corrupt_receipt(self):
        plan = self.plan()
        self.apply(plan)
        launcher = self.prefix / "bin/godel-cli"
        launcher.write_text("later custom edit")
        with self.assertRaisesRegex(ValueError, "changed since install"):
            installer.rollback(self.backup, True)
        self.assertEqual(launcher.read_text(), "later custom edit")
        receipt = self.backup / "before.json"
        data = json.loads(receipt.read_text())
        data["entries"][0]["before"] = {"kind": "link", "target": "corrupt"}
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            installer.rollback(self.backup, True)

    def test_refuse_modified_immutable_release_including_manifest(self):
        plan = self.plan()
        self.apply(plan)
        target = Path(plan["release_target"])
        entry = target / "files/scripts/console_entry.py"
        original = entry.read_bytes()
        entry.chmod(0o644)
        entry.write_bytes(b"changed immutable content")
        self.backup = self.base / "backup2"
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.apply(self.plan())
        self.assertEqual(entry.read_bytes(), b"changed immutable content")
        entry.write_bytes(original)
        entry.chmod(0o444)
        manifest = target / "manifest.json"
        manifest.chmod(0o644)
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        self.backup = self.base / "backup3"
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.apply(self.plan())

    def test_manifest_tampering_missing_imports_and_extra_symlink_rejected(self):
        copied = self.base / "damaged"
        shutil.copytree(self.release, copied)
        entry = copied / "files/scripts/console_entry.py"
        entry.chmod(0o644)
        entry.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "differ"):
            installer.prepare(copied, self.prefix, Path(sys.executable))
        entry.unlink()
        shutil.copy2(self.release / "files/scripts/console_entry.py", entry)
        (copied / "extra").symlink_to(self.release)
        with self.assertRaisesRegex(ValueError, "link"):
            builder.verify(copied)
        (copied / "extra").unlink()
        missing = copied / "vendor/rich/__init__.py"
        missing.unlink()
        manifest = builder.verify(self.release)
        manifest["files"] = builder.inventory(copied)
        payload = {k: v for k, v in manifest.items() if k != "release"}
        manifest["release"] = builder.digest(builder.encoded(payload))
        path = copied / "manifest.json"
        path.chmod(0o644)
        path.write_bytes(builder.encoded(manifest))
        path.chmod(0o444)
        # Even a self-consistent manifest cannot hide a broken dependency bundle.
        with self.assertRaisesRegex(ValueError, "import check failed"):
            installer.prepare(copied, self.prefix, Path(sys.executable))
        self.assertFalse(self.prefix.exists())

    def test_bad_paths_missing_executable_mail_and_symlink_destination(self):
        for python, mail in ((self.base / "missing", None), (Path(sys.executable), self.base / "missing")):
            with self.assertRaisesRegex(ValueError, "missing"):
                installer.prepare(self.release, self.prefix, python, mail)
        with self.assertRaisesRegex(ValueError, "line breaks"):
            installer.prepare(self.release, self.base / "bad\npath", Path(sys.executable))
        self.prefix.mkdir()
        (self.prefix / "bin").symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "parent"):
            self.plan()
        for name in ("../escape", "/absolute", "x/../escape", "x//y"):
            with self.assertRaises(ValueError):
                builder.relative(name)

    def test_profiles_history_and_retry_records_untouched(self):
        sentinels = [self.home / name for name in (".config/godelclaw/instances.json", ".config/godelclaw/mail.toml",
                     "state/terminal-client/history", "state/terminal-client/pending/stable-id.json")]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unchanged existing state\n")
        before = {path: path.read_bytes() for path in sentinels}
        plan = self.plan()
        self.apply(plan)
        installer.rollback(self.backup, True)
        self.assertEqual({path: path.read_bytes() for path in sentinels}, before)


if __name__ == "__main__":
    unittest.main()
