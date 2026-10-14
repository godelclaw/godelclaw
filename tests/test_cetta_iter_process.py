"""CeTTa transformation workers and a syntax check that never executes input."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch
from test_native_iter_process import APPEND
ROOT = dispatch.ROOT

class CettaIterProcess(unittest.TestCase):
    def guest(self, directory, code):
        harness = dispatch.CommandDispatchEngineTest()
        binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN')
        env = harness._cetta_env()
        env.update(CETTA_BIN=str(binary), METTACLAW_ITER_PROCESS_DIR=str(directory),
                   METTACLAW_ITER_TEMP_DIR=str(directory / 'runs'),
                   METTACLAW_ITER_PROCESS_TIMEOUT_SECONDS='0.1')
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as source:
            source.write('!(import! &self (library lib_import))\n'
                         '!(import! &self ./src/utils)\n'
                         '!(import! &self ./src/iter_process_policy)\n'
                         '!(import! &self ./src/metta_coding_policy)\n'+code)
            source.flush()
            result = subprocess.run([str(binary), '--lang', 'petta', '--import-mode',
                                     'ancestor-walk', source.name], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ITER_OK', result.stdout, result.stdout + result.stderr)
        self.assertNotIn('Assertion failed', result.stderr)
        return result.stdout

    def test_ordered_sources_and_failure_stutter(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / '10_first.metta').write_text(APPEND % 'first')
            (directory / '20_failure.metta').write_text('(= (iter-transform $visible) failure)\n')
            (directory / '30_second.metta').write_text(APPEND % 'second')
            output = self.guest(directory, '''!(let* (($request (iter-process-prepare-request system activity advertised 1))
                   ($prompt (iter-process-render-request $request))
                   ($observations (iter-process-request-observations $request)))
   (progn (println! $prompt) (println! $observations) (println! ITER_OK)))
''')
            self.assertIn('user: first', output)
            self.assertIn('user: second', output)
            self.assertLess(output.index('user: first'), output.index('user: second'))
            observations = output.replace('\\"', '').replace('"', '')
            self.assertIn('20_failure.metta failure', observations)
            self.assertIn('30_second.metta success', observations)

    def test_timeout_is_result_and_later_transform_still_runs(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / '10_timeout.metta').write_text('(= (spin) (spin))\n(= (iter-transform $x) (spin))\n')
            (directory / '20_after.metta').write_text(APPEND % 'after-timeout')
            output = self.guest(directory, '''!(let $request (iter-process-prepare-request system activity advertised 1)
              (progn (println! (iter-process-render-request $request)) (println! ITER_OK)))
''')
            self.assertIn('timeout', output)
            self.assertIn('user: after-timeout', output)

    def test_syntax_checker_reads_bang_forms_without_running_them(self):
        harness = dispatch.CommandDispatchEngineTest(); binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN')
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.metta'; marker = Path(d) / 'effect'
            source.write_text('!(fs:write '+json.dumps(str(marker))+' "must not execute")\n')
            command = [str(binary), '--lang', 'petta', str(ROOT / 'scripts/metta_parse_only.metta'), '--', str(source)]
            result = subprocess.run(command, cwd=ROOT, env=harness._cetta_env(), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('SYNTAX_OK', result.stdout)
            self.assertFalse(marker.exists())
            source.write_text('(unfinished')
            result = subprocess.run(command, cwd=ROOT, env=harness._cetta_env(), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

if __name__ == '__main__': unittest.main()
