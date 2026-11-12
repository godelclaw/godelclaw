"""Build input snapshots never borrow uncommitted checkout state."""
import os
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cetta_source_snapshot as snapshot
import build_cetta


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cetta-inputs-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo, self.output = self.root / 'repo', self.root / 'inputs'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'src').mkdir()
        (self.repo / 'src/input.c').write_text('committed source\n')
        (self.repo / 'tool').write_text('#!/bin/sh\nexit 0\n')
        (self.repo / 'tool').chmod(0o755)
        self.revision = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture source')
        return self.git('rev-parse', 'HEAD')

    def test_old_commit_ignores_dirty_files_untracked_files_and_new_head(self):
        (self.repo / 'src/input.c').write_text('new commit\n')
        self.commit()
        (self.repo / 'src/input.c').write_text('live unfinished edit\n')
        (self.repo / 'private').write_text('untracked fixture\n')
        before = self.git('status', '--porcelain')
        result = snapshot.materialize(self.repo, self.revision, self.output)
        self.assertEqual(result, self.output)
        self.assertEqual((result / 'src/input.c').read_text(), 'committed source\n')
        self.assertFalse((result / 'private').exists())
        self.assertFalse((result / '.git').exists())
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual((self.repo / 'src/input.c').read_text(), 'live unfinished edit\n')

    def test_reuse_validates_all_inputs_and_allows_new_build_outputs(self):
        snapshot.materialize(self.repo, self.revision, self.output)
        (self.output / 'artifact').write_bytes(b'generated object')
        self.assertEqual(snapshot.materialize(self.repo, self.revision, self.output), self.output)
        (self.output / 'src/input.c').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'source input changed'):
            snapshot.materialize(self.repo, self.revision, self.output)

    def test_executable_mode_is_part_of_provenance(self):
        snapshot.materialize(self.repo, self.revision, self.output)
        (self.output / 'tool').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'executable mode changed'):
            snapshot.verify(self.repo, self.revision, self.output)

    def test_parent_symlink_cannot_redirect_verification(self):
        snapshot.materialize(self.repo, self.revision, self.output)
        (self.output / 'src/input.c').unlink()
        (self.output / 'src').rmdir()
        (self.output / 'src').symlink_to(self.repo / 'src')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            snapshot.verify(self.repo, self.revision, self.output)

    def test_foreign_existing_destination_is_not_replaced(self):
        self.output.mkdir()
        marker = self.output / 'marker'
        marker.write_text('keep')
        with self.assertRaises(ValueError):
            snapshot.materialize(self.repo, self.revision, self.output)
        self.assertEqual(marker.read_text(), 'keep')

    def test_snapshot_stays_outside_checkout(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            snapshot.materialize(self.repo, self.revision, self.repo / 'inputs')

    def test_symlink_destination_is_refused(self):
        self.output.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            snapshot.materialize(self.repo, self.revision, self.output)

    def test_internal_symlink_preserves_its_committed_target(self):
        (self.repo / 'link').symlink_to('src/input.c')
        revision = self.commit()
        snapshot.materialize(self.repo, revision, self.output)
        self.assertEqual(os.readlink(self.output / 'link'), 'src/input.c')
        self.assertEqual((self.output / 'link').read_text(), 'committed source\n')

    def test_export_ignored_input_cannot_be_silently_missing(self):
        (self.repo / '.gitattributes').write_text('src/input.c export-ignore\n')
        revision = self.commit()
        with self.assertRaisesRegex(ValueError, 'missing'):
            snapshot.materialize(self.repo, revision, self.output)
        self.assertFalse(self.output.exists())

    def builder(self, tamper=False):
        real_run = build_cetta.run
        def run(command, cwd=None, capture=False):
            if command[0] != 'make':
                return real_run(command, cwd, capture=capture)
            source = Path(cwd)
            artifacts = ['runtime/resolved-' + str(i) for i in range(len(build_cetta.RUNTIME_NAMES) + 1)]
            if capture:
                return ''.join('GODELCLAW_ARTIFACT=' + name + '\n' for name in artifacts)
            for name in artifacts:
                path = source / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'fixture executable')
                path.chmod(0o755)
            for name in ('lib', 'tools', 'docs/siwc/licenses'):
                (source / name).mkdir(parents=True, exist_ok=True)
            if tamper:
                (source / 'src/input.c').write_text('changed during build\n')
        argv = ['build_cetta', '--source', str(self.repo), '--source-snapshot', str(self.output),
                '--ref', self.revision, '--profile', 'native-agent', '--prefix', str(self.root / 'bundle')]
        with patch.object(sys, 'argv', argv), patch.object(build_cetta, 'run', run), \
             patch.object(build_cetta, 'verify_sqlite_feature'), patch.object(build_cetta, 'verify_durable_feature'), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            build_cetta.main()

    def test_builder_installs_named_commit_while_checkout_stays_dirty(self):
        (self.repo / 'src/input.c').write_text('live edit\n')
        self.builder()
        manifest = json.loads((self.root / 'bundle/build-manifest.json').read_text())
        self.assertEqual(manifest['revision'], self.revision)
        self.assertEqual(manifest['source_kind'], 'commit-snapshot')
        self.assertEqual((self.output / 'src/input.c').read_text(), 'committed source\n')
        self.assertEqual((self.repo / 'src/input.c').read_text(), 'live edit\n')

    def test_builder_refuses_changed_input_before_installing_any_artifact(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        marker = bundle / 'cetta'
        marker.write_bytes(b'keep prior bundle')
        with self.assertRaises(SystemExit) as error:
            self.builder(tamper=True)
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(marker.read_bytes(), b'keep prior bundle')
        self.assertEqual(list(bundle.iterdir()), [marker])


if __name__ == '__main__':
    unittest.main()
