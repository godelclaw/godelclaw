"""Native input classification shared by cognition and its independent observer."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_command_dispatch_engine as dispatch_fixture

ROOT = Path(__file__).resolve().parents[1]


class ControlInputTests(unittest.TestCase):
    def probe(self, body):
        if not os.environ.get('CETTA_BIN'):
            self.skipTest('set CETTA_BIN to the native engine')
        with tempfile.TemporaryDirectory(prefix='native-control-input-') as folder:
            path = Path(folder) / 'probe.metta'
            path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'channels/telegram_control.metta')) + ')\n' + body)
            env = dispatch_fixture.CommandDispatchEngineTest()._cetta_env()
            env['METTACLAW_ENGINE_STATE_PATH'] = str(Path(folder) / 'engine')
            result = subprocess.run([env['CETTA_BIN'], '--lang', 'petta', '--import-mode', 'ancestor-walk', str(path)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('(Error ', result.stdout)
            return result.stdout

    def test_commands_are_discovered_from_declarations(self):
        output = self.probe('''
!(println! (OTHER (ti:control-text? "/activity@OtherBot")))
!(println! (CASE (ti:control-text? "  /ACTIVITY@OtherBot  ")))
!(println! (UNKNOWN (ti:control-text? "/custom question")))
!(println! (ORDINARY (ti:control-text? "please explain /activity")))
!(println! (EMPTY (ti:control-text? "")))
(tg-cmd:command "/new_control" delegated "fixture declaration")
!(println! (DYNAMIC (ti:control-text? "/new_control@OtherBot")))
''')
        for marker in ['OTHER', 'CASE', 'DYNAMIC']:
            self.assertIn('(' + marker + ' true)', output)
        for marker in ['UNKNOWN', 'ORDINARY', 'EMPTY']:
            self.assertIn('(' + marker + ' false)', output)

    def test_only_ordinary_operator_input_counts_as_awaiting_cognition(self):
        tasks = []
        for identity, role, content in [('foreign', 'operator', {'text': '/activity@OtherBot'}),
                                        ('caption', 'operator', {'caption': ' /ACTIVITY@OtherBot'}),
                                        ('ordinary', 'operator', {'text': 'a real question'}),
                                        ('unknown', 'operator', {'text': '/custom explain'}),
                                        ('peer', 'peer', {'text': '/activity'})]:
            update = {'update_id': identity, 'message': {'date': 1790000000, **content}}
            body = json.dumps(['input', '42.0', 'message', role, update])
            tasks.append('(' + json.dumps(identity) + ' ' + json.dumps(body) + ')')
        output = self.probe('''
!(let $tasks (tc:notice-tasks (''' + ' '.join(tasks) + '''))
   (let $_ (py-call (input_ack.begin_scan (quote $tasks)))
     (println! (FILTERED (size-atom $tasks) (py-call (str (py-call (input_ack.input_view))))))))
''')
        self.assertIn('(FILTERED 3 ', output)
        self.assertIn('pending', output)
        # Two conversational operator inputs, including an unknown slash tool.
        self.assertRegex(output, r'pending[^0-9]+2')


if __name__ == '__main__':
    unittest.main()
