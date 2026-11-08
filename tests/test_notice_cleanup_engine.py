"""Exercise the native cleanup against real persisted notice identities on the native ledger."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import native_ledger  # noqa: E402
import test_input_ledger as base  # noqa: E402
from native_ledger_client import NativeRunner  # noqa: E402
from test_input_ledger_remote import available, dump_native, oracle_environment  # noqa: E402

LANE = "-42.0"


@unittest.skipUnless(available(), "set CETTA_BIN to the native engine (cetta-value-worker beside it)")
class NativeNoticeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native-notice-cleanup-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.socket = native_ledger.start(self, self.folder)

    def seed(self, processed=True):
        """One owned notice: registered, submitted, delivered as message 501, its input processed or not."""
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.socket)
            self.send = runner.apply(("register", "own", "7", LANE, 0.0))
            self.assertTrue(runner.apply(("submitted", "own")))
            runner.apply(("receipt", self.send, ["delivered", 501]))
            if processed:
                runner.apply(("mark", ["7"], 1.0))

    def run_native(self, forms):
        path = self.folder / 'probe.metta'
        path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'channels/telegram_notice_cleanup.metta')) + ')\n' + forms)
        env = dict(os.environ, METTACLAW_INPUT_LEDGER_SOCKET=self.socket)
        result = subprocess.run([env['CETTA_BIN'], '--lang', 'petta', '--import-mode', 'ancestor-walk', str(path)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('(Error ', result.stdout)
        return result.stdout

    def delete_key(self):
        """The notice's delete identity, minted (once) by the cleanup itself."""
        output = self.run_native('!(println! (KEY (nc:delete-key "own" "%s")))\n' % LANE)
        return json.loads(output.split("(KEY ", 1)[1].split(")", 1)[0])

    def delivery(self, key, outcome):
        body = json.dumps(['delivery', key, 0, 1, outcome], separators=(',', ':'))
        return '(nc:receipt (json-parse ' + json.dumps(body) + '))'

    def state(self):
        rows, processed = dump_native(self.socket, self.folder)
        return dict(zip(base.COLUMNS, rows[0])), processed

    def test_send_receipt_cannot_replace_the_owned_message(self):
        self.seed()
        output = self.run_native('!(println! (WRONG ' + self.delivery(self.send, ['delivered', 999]) + '))\n')
        self.assertIn('(WRONG false)', output)
        self.assertEqual(self.state()[0]['message_id'], 501)

    def test_delete_receipt_must_match_target_and_eligible_notice(self):
        self.seed()
        key = self.delete_key()
        output = self.run_native('!(println! (WRONG ' + self.delivery(key, ['delivered', 999]) + '))\n'
                                 '!(println! (OWN ' + self.delivery(key, ['delivered', 0]) + '))\n')
        self.assertIn('(WRONG false)', output)
        self.assertIn('(OWN true)', output)
        self.assertEqual(self.state()[0]['state'], 'deleted')

    def test_definitely_not_sent_delete_gets_a_fresh_persisted_identity(self):
        self.seed()
        old = self.delete_key()
        output = self.run_native('!(println! (FAILED ' + self.delivery(old, ['not-sent']) + '))\n'
                                 '!(println! (KEY (nc:delete-key "own" "%s")))\n'
                                 '!(println! (SAME (nc:delete-key "own" "%s")))\n' % (LANE, LANE))
        self.assertIn('(FAILED true)', output)
        first = self.state()[0]['delete_key']
        self.assertNotEqual(first, old)
        self.assertTrue(first.startswith(LANE + '.'))
        self.assertEqual(len(first.rsplit('.', 1)[1]), 20)
        self.assertIn('(KEY ' + json.dumps(first) + ')', output)
        self.assertIn('(SAME ' + json.dumps(first) + ')', output)
        # Another process and a lost submit reply reuse the persisted attempt.
        self.run_native('!(println! (KEY (nc:delete-key "own" "%s")))\n' % LANE)
        self.assertEqual(self.state()[0]['delete_key'], first)

    def test_mistaken_notice_is_withdrawn_without_completing_input(self):
        self.seed(processed=False)
        key = self.delete_key()
        output = self.run_native('!(println! (EARLY ' + self.delivery(key, ['delivered', 0]) + '))\n'
                                 '!(println! (WITHDRAW (nc:withdraw "own")))\n'
                                 '!(println! (DONE ' + self.delivery(key, ['delivered', 0]) + '))\n'
                                 '!(println! (OTHER ' + self.delivery(LANE + '.999', ['delivered', 0]) + '))\n')
        for expected in ['(EARLY false)', '(WITHDRAW true)', '(DONE true)', '(OTHER false)']:
            self.assertIn(expected, output)
        notice, processed = self.state()
        self.assertEqual(notice['processed'], 0)
        self.assertEqual(notice['state'], 'deleted')
        self.assertEqual(processed, {})


if __name__ == '__main__':
    unittest.main()
