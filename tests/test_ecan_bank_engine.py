"""Conformance fixtures for the attention bank imported into godelclaw."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BankTests(unittest.TestCase):
    def test_bank_conformance_and_fresh_process_snapshot(self):
        engine = os.environ.get('CETTA_BIN')
        if not engine:
            self.skipTest('set CETTA_BIN')
        with tempfile.TemporaryDirectory(prefix='ecan-conformance-') as directory:
            env = dict(os.environ, ECAN_SNAPSHOT_PATH=str(Path(directory) / 'snapshot.metta'))
            for name, phase in [('essential', ''), ('arguments', ''), ('policy', ''),
                                ('snapshot', 'write'), ('snapshot', 'read')]:
                with self.subTest(name=name, phase=phase):
                    env['ECAN_SNAPSHOT_PHASE'] = phase
                    result = subprocess.run([engine, '--lang', 'petta', str(ROOT / f'tests/ecan_{name}.metta')],
                                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn('TestFailure', result.stdout)
                    self.assertNotIn('(Error ', result.stdout)
