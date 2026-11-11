"""Direct native checkpoint transport, with no embedded Python in the guest."""
import hermetic_env  # noqa: F401,E402
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
import input_ack
import native_ledger
from isolated_env import isolated_environ


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to a qualified CeTTa')
class CheckpointLedgerNativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.socket = native_ledger.start(self, self.directory)

    def guest(self, forms, **env):
        source = self.directory / 'native.metta'
        source.write_text('\n'.join([
            '!(import! &self ' + q(ROOT / 'src/checkpoint_ledger_cetta.metta') + ')',
            *forms, '!(println! "CHECKPOINT_NATIVE_OK")']) + '\n')
        result = subprocess.run(
            [os.environ['CETTA_BIN'], '--value-bytes', str(input_ack.LEDGER_VALUE_BYTES),
             '--lang', 'petta', '--import-mode', 'ancestor-walk', str(source)],
            cwd=ROOT, env={**isolated_environ(), input_ack.LEDGER_SOCKET: self.socket, **env},
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertIn('CHECKPOINT_NATIVE_OK', result.stdout)
        self.assertNotIn('Assertion failed', result.stdout + result.stderr)
        return result

    def test_missing_is_distinct_from_empty_and_unavailable(self):
        self.guest(['!(assert (== (checkpoint:read) (0)))'])
        input_ack.turn_checkpoint('', [], '', 1.0)
        self.guest(['!(assert (== (checkpoint:read) (1 "")))'])
        self.guest(['!(assert (erv:failed? (checkpoint:read)))'],
                   METTACLAW_INPUT_LEDGER_SOCKET=str(self.directory / 'missing.sock'))
        self.guest(['!(assert (erv:failed? (checkpoint:read)))'],
                   METTACLAW_INPUT_LEDGER_SOCKET='')

    def test_same_occurrence_replays_collision_refuses_and_new_equal_occurrence_applies(self):
        self.guest([
            '!(assert (checkpoint:write-with-id "A" ("" "one" "one") "" 1.0 "ck1.a"))',
            '!(assert (checkpoint:write-with-id "B" ("two") "" 2.0 "ck1.b"))',
            '!(assert (checkpoint:write-with-id "A" ("" "one" "one") "" 1.0 "ck1.a"))',
            '!(assert (== (checkpoint:read) (1 "B")))',
            '!(assert (erv:failed? (checkpoint:write-with-id "wrong" () "" 1.0 "ck1.a")))',
            '!(assert (== (checkpoint:read) (1 "B")))',
            '!(assert (checkpoint:write-with-id "A" ("one") "" 1.0 "ck1.new-a"))',
            '!(assert (== (checkpoint:read) (1 "A")))',
        ])
        self.assertEqual(input_ack.working_set(), 'A')
        with mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY='1'):
            self.assertEqual([input_ack.already_committed(i) for i in ('one', 'two', '')], [1, 1, 0])

    def test_full_write_allocates_distinct_occurrences_for_equal_payloads(self):
        self.guest([
            '!(assert (checkpoint:write "A" () "" 1.0))',
            '!(assert (checkpoint:write "B" () "" 2.0))',
            '!(assert (checkpoint:write "A" () "" 1.0))',
            '!(assert (== (checkpoint:read) (1 "A")))',
        ])
        self.assertEqual(input_ack.working_set(), 'A')

    def test_large_binary_unicode_text_is_preserved_across_restart(self):
        text = ('λ\x00雪\r\n' * 190000) + 'END'
        path = self.directory / 'payload'
        path.write_text(text, newline='')
        forms = ['!(import! &self (library fs))',
                 '!(assert (checkpoint:write (fs:read-text %s) ("large") "" 3.0))' % q(path),
                 '!(assert (== (checkpoint:read) (1 (fs:read-text %s))))' % q(path)]
        self.guest(forms)
        self.assertEqual(input_ack.working_set(), text)
        self.ledger_server.stop()
        self.ledger_server.start()
        self.guest(['!(import! &self (library fs))',
                    '!(assert (== (checkpoint:read) (1 (fs:read-text %s))))' % q(path)])

    def test_refused_large_write_preserves_checkpoint_and_server_remains_usable(self):
        self.ledger_server.stop()
        self.ledger_server.value_bytes = 65536
        self.ledger_server.start()
        path = self.directory / 'too-large'
        path.write_text('x' * 131072)
        self.guest([
            '!(import! &self (library fs))',
            '!(assert (checkpoint:write "before" () "" 1.0))',
            '!(assert (erv:failed? (checkpoint:write (fs:read-text %s) ("refused") "" 2.0)))' % q(path),
            '!(assert (== (checkpoint:read) (1 "before")))',
            '!(assert (checkpoint:write "after" () "" 3.0))',
            '!(assert (== (checkpoint:read) (1 "after")))',
        ])
        with mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY='1'):
            self.assertEqual(input_ack.already_committed('refused'), 0)


if __name__ == '__main__':
    unittest.main()
