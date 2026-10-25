import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import install_claw_mail


class MailInstallTest(unittest.TestCase):
    def test_installed_cli_is_a_snapshot_not_a_mutable_checkout(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory) / 'source'
            (root / 'scripts').mkdir(parents=True)
            source = root / 'scripts/shared_mail.py'
            source.write_text('original CLI\n')
            (root / 'scripts/shared_mail.metta').write_text('(original mailbox)\n')
            target = Path(directory) / 'installed/bin'
            first = install_claw_mail.install_cli(root, target, False)
            source.write_text('new development work\n')
            self.assertEqual((target / 'claw-mail').read_text(), 'original CLI\n')
            second = install_claw_mail.install_cli(root, target, True)
            self.assertNotEqual(first, second)
            self.assertEqual((target / 'claw-mail').read_text(), 'new development work\n')
            self.assertEqual((first / 'shared_mail.py').read_text(), 'original CLI\n')
