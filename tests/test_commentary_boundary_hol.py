"""Exact-conclusion authentication for the small commentary HOL model."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'check_commentary_boundary', ROOT / 'scripts/check_commentary_boundary.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CommentaryHOLAuthenticationTests(unittest.TestCase):
    def test_expected_result_is_accepted_with_or_without_import_result(self):
        expected = checker.expected_result()
        checker.authenticate(expected)
        checker.authenticate('true\n' + expected)

    def test_changed_conclusion_hypotheses_axioms_or_extra_output_is_rejected(self):
        expected = checker.expected_result()
        for forged in (
                expected.replace('command-batch', 'different-command'),
                expected.replace('(thm ()', '(thm (extra-hypothesis)'),
                expected.replace('(Axioms ())', '(Axioms (untrusted))'),
                expected + '\n' + expected,
                '(UnverifiedLabel ' + expected + ')'):
            with self.subTest(forged=forged):
                with self.assertRaises(AssertionError):
                    checker.authenticate(forged)

    def test_finite_implementation_correspondence(self):
        self.assertEqual(checker.correspondence(), 180)

    @unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN for native HOL checking')
    def test_native_model_exact_sequents(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/check_commentary_boundary.py'),
             '--cetta', os.environ['CETTA_BIN']],
            cwd=ROOT, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"exact_sequents_checked": 2', result.stdout)
        self.assertIn('"refinement_proof": false', result.stdout)


if __name__ == '__main__':
    unittest.main()
