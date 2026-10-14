"""Native job policy, idle wake, automatic acknowledgement and restart recovery."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch
ROOT = dispatch.ROOT

class NativeJobs(unittest.TestCase):
    def guest(self, directory, code):
        harness = dispatch.CommandDispatchEngineTest()
        binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN')
        env = harness._cetta_env()
        env.update(METTACLAW_JOBS_DIR=str(directory), METTACLAW_TELEGRAM_CLIENT='metta',
                   METTACLAW_TELEGRAM_SERVICE_SOCKET='', METTACLAW_MAIL_SOCKET='',
                   METTACLAW_MAIL_NAME='', METTACLAW_INSTANCE='native-jobs-test',
                   METTACLAW_WAKE_REQUEST_PATH='', METTACLAW_RECYCLE_REQUEST_PATH='',
                   METTACLAW_LIFECYCLE_PATH='', METTACLAW_ENGINE_STATE_PATH='',
                   METTACLAW_LOOP_MODE_PATH='')
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as source:
            source.write('!(import! &self (library lib_import))\n'
                         '!(import! &self ./src/utils_cetta)\n'
                         '!(import! &self ./channels/telegram.py)\n'
                         '!(import! &self ./channels/telegram.metta)\n'+code)
            source.flush()
            result = subprocess.run([str(binary), '--lang', 'petta', '--import-mode',
                                     'ancestor-walk', source.name], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Assertion failed', result.stderr)
        self.assertIn('JOBS_OK', result.stdout, result.stdout + result.stderr)
        return result

    def test_completion_wakes_and_committed_ack_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.guest(directory, '''!(println! (background "printf native-body; sleep .07; exit 7"))
!(assert (== (tg:sleep-until-message 30 True) "woken by job"))
!(println! (jobs:receive))
!(assert (jobs:active?))
!(assert (jobs:pending?))
!(assert (== (jobs:ack) 1))
!(assert (not (jobs:pending?)))
!(println! JOBS_OK)
''')
            records = [json.loads(p.read_text()) for p in Path(directory).glob('*.json')]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['exit'], 7)
            self.assertTrue(records[0]['notified'])
            self.assertEqual(Path(records[0]['log']).read_text(), 'native-body')
            self.guest(directory, '''!(jobs:reset)
!(assert (not (jobs:pending?)))
!(println! (jobs 0))
!(println! JOBS_OK)
''')

    def test_signal_is_agent_selected_and_result_remains_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.guest(directory, '''!(background "sleep 30")
!(let $ids (collapse (match &self (job:live $id $_) $id))
   (println! (job-stop (car-atom $ids) 9)))
!(assert (== (tg:sleep-until-message 30 True) "woken by job"))
!(println! (jobs:receive))
!(println! JOBS_OK)
''')
            record = json.loads(next(Path(directory).glob('*.json')).read_text())
            self.assertEqual((record['state'], record['exit']), ('signaled', 9))

    def test_disabled_job_wake_keeps_selected_rest_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            self.guest(directory, '''!(background "sleep .02")
!(let* (($before (get_time)) ($answer (tg:sleep-until-message 0.08 False))
        ($after (get_time)))
   (progn (assert (>= (- $after $before) 0.07)) (println! $answer)))
!(assert (jobs:pending?))
!(println! JOBS_OK)
''')

    def test_restart_reports_unknown_exit_and_keeps_old_numeric_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / '12.log'; log.write_text('retained evidence')
            (Path(directory) / '12.json').write_text(json.dumps(dict(
                id='12', command='old job', state='running', pid=0,
                started=1, log=str(log))))
            self.guest(directory, '''!(jobs:reset)
!(assert (jobs:pending?))
!(println! (jobs:receive))
!(println! (job-output 12 0))
!(println! JOBS_OK)
''')
            record = json.loads((Path(directory) / '12.json').read_text())
            self.assertIn('exit unknown', record['state'])
            self.assertNotIn('exit', record)
            self.assertEqual(log.read_text(), 'retained evidence')

if __name__ == '__main__': unittest.main()
