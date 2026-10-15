"""Exercise the native cleanup against real persisted notice identities."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeNoticeCleanupTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('CETTA_BIN'):
            self.skipTest('set CETTA_BIN to the native engine')
        self.temporary = tempfile.TemporaryDirectory(prefix='native-notice-cleanup-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.db = self.folder / 'strategy.db'
        with sqlite3.connect(self.db) as db:
            db.execute('''CREATE TABLE input_notices (
              task TEXT PRIMARY KEY, update_id TEXT, lane TEXT, send_key TEXT UNIQUE,
              created REAL, message_id INTEGER, processed INTEGER DEFAULT 0,
              delete_key TEXT, state TEXT DEFAULT 'submitted', checked REAL DEFAULT 0)''')
            db.execute('CREATE TABLE processed_inputs(id TEXT PRIMARY KEY, processed REAL)')
            db.execute("INSERT INTO input_notices VALUES ('own','7','-42.0','-42.0.111',0,501,1,'-42.0.222','submitted',0)")

    def run_native(self, forms):
        path = self.folder / 'probe.metta'
        path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'channels/telegram_notice_cleanup.metta')) + ')\n' + forms)
        env = dict(os.environ, METTACLAW_STRATEGY_PATH=str(self.db))
        result = subprocess.run([env['CETTA_BIN'], '--lang', 'petta', '--import-mode', 'ancestor-walk', str(path)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('(Error ', result.stdout)
        return result.stdout

    def delivery(self, key, outcome):
        body = json.dumps(['delivery', key, 0, 1, outcome], separators=(',', ':'))
        return '(nc:receipt (json-parse ' + json.dumps(body) + '))'

    def row(self):
        with sqlite3.connect(self.db) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM input_notices').fetchone())

    def test_send_receipt_cannot_replace_the_owned_message(self):
        output = self.run_native('!(println! (WRONG ' + self.delivery('-42.0.111', ['delivered', 999]) + '))\n')
        self.assertIn('(WRONG false)', output)
        self.assertEqual(self.row()['message_id'], 501)

    def test_delete_receipt_must_match_target_and_eligible_notice(self):
        output = self.run_native('!(println! (WRONG ' + self.delivery('-42.0.222', ['delivered', 999]) + '))\n'
                                 '!(println! (OWN ' + self.delivery('-42.0.222', ['delivered', 0]) + '))\n')
        self.assertIn('(WRONG false)', output)
        self.assertIn('(OWN true)', output)
        self.assertEqual(self.row()['state'], 'deleted')

    def test_definitely_not_sent_delete_gets_a_fresh_persisted_identity(self):
        output = self.run_native('!(println! (FAILED ' + self.delivery('-42.0.222', ['not-sent']) + '))\n'
                                 '!(println! (KEY (nc:delete-key "own" "-42.0")))\n'
                                 '!(println! (SAME (nc:delete-key "own" "-42.0")))\n')
        self.assertIn('(FAILED true)', output)
        first = self.row()['delete_key']
        self.assertNotEqual(first, '-42.0.222')
        self.assertTrue(first.startswith('-42.0.'))
        self.assertEqual(len(first.rsplit('.', 1)[1]), 20)
        self.assertIn('(KEY ' + json.dumps(first) + ')', output)
        self.assertIn('(SAME ' + json.dumps(first) + ')', output)
        # Another process and a lost submit reply reuse the persisted attempt.
        self.run_native('!(println! (KEY (nc:delete-key "own" "-42.0")))\n')
        self.assertEqual(self.row()['delete_key'], first)

    def test_mistaken_notice_is_withdrawn_without_completing_input(self):
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE input_notices SET processed=0')
        output = self.run_native('!(println! (EARLY ' + self.delivery('-42.0.222', ['delivered', 0]) + '))\n'
                                 '!(println! (WITHDRAW (nc:withdraw "own")))\n'
                                 '!(println! (DONE ' + self.delivery('-42.0.222', ['delivered', 0]) + '))\n'
                                 '!(println! (OTHER ' + self.delivery('-42.0.999', ['delivered', 0]) + '))\n')
        for expected in ['(EARLY false)', '(WITHDRAW true)', '(DONE true)', '(OTHER false)']:
            self.assertIn(expected, output)
        self.assertEqual(self.row()['processed'], 0)
        self.assertEqual(self.row()['state'], 'deleted')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM processed_inputs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
