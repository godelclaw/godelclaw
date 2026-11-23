"""The installed mail client is a snapshot, not a link into the mutable checkout."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MailInstallTest(unittest.TestCase):
    def test_installed_client_is_a_snapshot_not_a_mutable_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            (root / 'scripts').mkdir(parents=True)
            shutil.copy(ROOT / 'scripts/install_claw_mail.sh', root / 'scripts')
            client = root / 'scripts/claw_mail_cetta.metta'
            client.write_text('(original client)\n')
            (root / 'scripts/shared_mail.metta').write_text('(original mailbox)\n')
            target = Path(directory) / 'installed/bin'
            def install(*extra):
                result = subprocess.run(['sh', str(root / 'scripts/install_claw_mail.sh'), '--engine', '/bin/true',
                                         '--bin-dir', str(target), '--no-skill', *extra],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return (target / 'claw-mail').resolve().parent
            first = install()
            client.write_text('(new development work)\n')
            self.assertEqual((first / 'claw_mail_cetta.metta').read_text(), '(original client)\n')
            refused = subprocess.run(['sh', str(root / 'scripts/install_claw_mail.sh'), '--engine', '/bin/true',
                                      '--bin-dir', str(target), '--no-skill'], capture_output=True, text=True, timeout=10)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            second = install('--force-links')
            self.assertNotEqual(first, second)
            self.assertEqual((second / 'claw_mail_cetta.metta').read_text(), '(new development work)\n')
            self.assertEqual((first / 'claw_mail_cetta.metta').read_text(), '(original client)\n')
            self.assertIn(str(second / 'claw_mail_cetta.metta'), (target / 'claw-mail').read_text())


if __name__ == '__main__':
    unittest.main()
