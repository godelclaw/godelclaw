"""The native retry wait stores a computed deadline, including after replacement."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch


class NativeIdle(unittest.TestCase):
    def test_backoff_stores_one_numeric_deadline_and_arms_timer(self):
        harness = dispatch.CommandDispatchEngineTest()
        binary = harness._cetta_binary()
        if not binary.is_file():
            self.skipTest('set CETTA_BIN')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'backoff.metta'
            source.write_text('!(import! &self (library lib_import))\n!(import! &self '
                + json.dumps(str(dispatch.ROOT / 'src/idle_cetta.metta')) + ')\n' + '''
!(idle:backoff)
!(idle:backoff)
!(assert (> (idle:retry-until) (system:wall-time)))
!(assert (== (size-atom (collapse (match &self (idle:cwp-retry $time) $time))) 1))
!(idle:set-token "old-watch")
!(idle:set-token "current-watch")
!(assert (== (idle:token) "current-watch"))
!(assert (== (size-atom (collapse (match &self (idle:cwp-token $token) $token))) 1))
!(let ((retry $id)) (idle:cwp 1)
   (let $_ (io:cancel $id) (println! IDLE_BACKOFF_OK)))
''')
            env = harness._cetta_env()
            env.update(METTACLAW_IDLE_RETRY_SECONDS='30',
                       METTACLAW_TELEGRAM_SERVICE_SOCKET=str(Path(directory) / 'absent.sock'))
            result = subprocess.run([str(binary), '--lang', 'petta', '--import-mode',
                                     'ancestor-walk', str(source)], cwd=dispatch.ROOT,
                                    env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('IDLE_BACKOFF_OK', result.stdout)
        self.assertNotIn('Assertion failed', result.stderr)


if __name__ == '__main__':
    unittest.main()
