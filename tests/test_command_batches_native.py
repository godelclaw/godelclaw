"""Native envelope normalization matches the Python oracle without calling it."""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import helper


class NativeCommandBatches(unittest.TestCase):
    def run_cases(self, cases, enabled):
        binary = os.environ.get('CETTA_BIN', '')
        if not Path(binary).is_file():
            self.skipTest('set CETTA_BIN to the envelope-capable native bundle')
        # Expected values are computed before the child poisons the Python path.
        expected = [helper.normalize_command_batches(text) if enabled else text for text in cases]
        poison = "lambda *a: (_ for _ in ()).throw(RuntimeError('PYTHON_NORMALIZER_USED'))"
        setup = "(" + ",".join(
            "setattr(__import__(%r), %r, %s)" % (module, name, poison)
            for module, name in (("helper", "normalize_command_batches"),
                                 ("helper", "_top_level_forms"),
                                 ("strategy_host", "normalize_response"))) + ")"
        lines = ['!(import! &self (library lib_llm))',
                 '!(import! &self ./src/command_pipeline.metta)',
                 '!(import! &self ./src/strategy_host.py)',
                 '!(import! &self ./src/helper.py)',
                 '!(py-eval ' + json.dumps(setup) + ')']
        for text in cases:
            codes = ' '.join(str(ord(c)) for c in text)
            lines.append('!(println! (str:sha256 (model-response-normalize (str:from-codepoints (' + codes + ')))))')
        env = isolated_environ(METTACLAW_ENGINE='cetta', METTACLAW_ACTIVE_ENGINE='cetta',
                               METTACLAW_OBSERVED_STRATEGY='1' if enabled else '0')
        prefix = os.environ.get('PETTA_PY_ENV', str(Path(sys.executable).resolve().parent.parent))
        env.update(PYTHONHOME=prefix, PYTHONNOUSERSITE='1',
                   PYTHONPATH=os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'channels'))))
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as program:
            program.write('\n'.join(lines) + '\n'); program.flush()
            result = subprocess.run([binary, '--lang', 'petta', '--import-mode', 'ancestor-walk', program.name], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-2000:])
        self.assertNotIn('PYTHON_NORMALIZER_USED', result.stdout + result.stderr)
        observed = []
        for line in result.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                value = line
            if isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value):
                observed.append(value)
        self.assertEqual(observed, [hashlib.sha256(text.encode()).hexdigest() for text in expected])

    def test_explicit_envelopes_preserve_order_strings_and_refusals(self):
        cases = ['(command-batch)', '(command-batch (a) (a))',
                 'pre (command-batch (a)) post', '(command-batch (run (command-batch (data))))',
                 '(command-batch (send "(command-batch (data))"))',
                 '(command-batch "not a command")', '(command-batch(a))',
                 '(command-batch (a)) "unfinished', '(command-batch (a)) (unfinished',
                 '(command-batch (send "a\x00b"))', '(command-batch\u2005(a)\u3000(b))']
        cases += ['(' * n + '(command-batch (send "λ😀"))' + ')' * n
                  for n in (0, 1, 62, 63, 64, 65, 200, 8000)]
        self.run_cases(cases, True)

    def test_seeded_nested_envelopes_match_the_oracle(self):
        rng = random.Random(927)
        leaves = ['(a)', '(a)', '(send "λ😀(b)")', '(send "a\\\"b")',
                  '(run (command-batch (data)))', '(command-batch (b) (c))']
        cases = []
        for _ in range(400):
            text = '(command-batch ' + ' '.join(rng.choices(leaves, k=rng.randrange(8))) + ')'
            text = '(' * rng.randrange(4) + text
            if rng.random() < .5:
                text = text + ')' * (len(text) - len(text.lstrip('(')) - 1)
            cases.append(text)
        self.run_cases(cases, True)

    def test_disabled_strategy_preserves_raw_text(self):
        self.run_cases(['(command-batch (a))', ' pre  (command-batch (a))\n',
                        '(command-batch (send "unterminated))'], False)


if __name__ == '__main__':
    unittest.main()
