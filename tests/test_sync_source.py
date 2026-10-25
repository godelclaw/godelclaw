"""Deployment identity must name exactly the selected committed runtime."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
import sync_instances


class SourceIdentityTests(unittest.TestCase):
    def test_unrelated_edits_are_allowed_but_selected_dirty_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            (root / 'runtime.py').write_text('qualified = True\n')
            (root / 'notes.txt').write_text('draft\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'qualified fixture')
            revision = git('rev-parse', 'HEAD')
            release = root / 'release'
            (release / 'files').mkdir(parents=True)
            copied = release / 'files/runtime.py'
            copied.write_bytes((root / 'runtime.py').read_bytes())
            sync_instances.verify_committed_files(root, revision, release, ['runtime.py'])
            copied.write_text('changed during copying\n')
            with self.assertRaisesRegex(ValueError, 'not from the source commit'):
                sync_instances.verify_committed_files(root, revision, release, ['runtime.py'])
            (root / 'notes.txt').write_text('unrelated work\n')
            self.assertEqual(sync_instances.committed_source(root, ['runtime.py']), revision)
            (root / 'runtime.py').write_text('qualified = False\n')
            with self.assertRaisesRegex(ValueError, 'uncommitted'):
                sync_instances.committed_source(root, ['runtime.py'])
            (root / 'new.py').write_text('not yet committed\n')
            with self.assertRaisesRegex(ValueError, 'uncommitted'):
                sync_instances.committed_source(root, ['new.py'])
