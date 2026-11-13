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
    def probe(self, body, settings=None):
        if not os.environ.get('CETTA_BIN'):
            self.skipTest('set CETTA_BIN to the native engine')
        with tempfile.TemporaryDirectory(prefix='native-control-input-') as folder:
            path = Path(folder) / 'probe.metta'
            path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'channels/telegram_control.metta')) + ')\n' + body)
            env = dispatch_fixture.CommandDispatchEngineTest()._cetta_env()
            env['METTACLAW_ENGINE_STATE_PATH'] = str(Path(folder) / 'engine')
            env.update({name: value.format(folder=folder) for name, value in (settings or {}).items()})
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
   (let $_ (notice:begin-scan $tasks $tasks)
     (println! (FILTERED (size-atom $tasks) (json-stringify (notice:input-view))))))
''')
        self.assertIn('(FILTERED 3 ', output)
        self.assertIn('pending', output)
        # Two conversational operator inputs, including an unknown slash tool.
        self.assertRegex(output, r'pending[^0-9]+2')

    def test_a_parse_is_kept_only_while_its_task_is_ordinary_input_on_the_page(self):
        body = json.dumps(json.dumps(['input', '42.0', 'message', 'operator', {'update_id': 1, 'message': {'date': 5}}]))
        edited = json.dumps(json.dumps(['input', '42.0', 'message', 'operator', {'update_id': 1, 'edited_message': {'date': 6}}]))
        control = json.dumps(json.dumps(['command', 'k', '/health', '', '42.0']))
        output = self.probe('''
(= (cached $task) (collapse (match &tc-parsed (tc:cached $task $body $value) $body)))
!(let* (($_ (tc:parse "t1" ''' + body + '''))
        ($_ (tc:parse "t1" ''' + body + '''))
        ($_ (tc:parse "c1" ''' + control + '''))
        ($_ (println! (ONCE (size-atom (cached "t1")))))
        ($_ (tc:prune (("t1" ''' + body + '''))))
        ($_ (println! (ON-PAGE (size-atom (cached "t1")) (size-atom (cached "c1")))))
        ($_ (tc:parse "t1" ''' + edited + '''))
        ($_ (println! (REPARSED (== (cached "t1") (''' + edited + ''')))))
        ($_ (tc:prune ())))
   (println! (OFF-PAGE (size-atom (cached "t1")))))
''')
        self.assertIn('(ONCE 1)', output)
        self.assertIn('(ON-PAGE 1 0)', output)
        self.assertIn('(REPARSED true)', output)
        self.assertIn('(OFF-PAGE 0)', output)

    def test_a_ledger_that_does_not_answer_gives_no_notice_and_no_key(self):
        # The ledger cannot say whether the update was processed: each answer is the
        # ledger's Error, never a key to send under, and no delay starts.
        update = json.dumps(json.dumps({'update_id': 7, 'message': {'date': 5, 'text': 'q'}}))
        output = self.probe('''
(= (kind $answer) (case $answer (((Error $where $why) error) ($other $other))))
!(let* (($update (json-parse ''' + update + '''))
        ($first (tc:input-notice "t" "42.0" "message" "operator" $update))
        ($_ (sleep 0.05))
        ($second (tc:input-notice "t" "42.0" "message" "operator" $update)))
   (println! (NOTICE (kind $first) (kind $second) (notice:pending "t"))))
''', {'METTACLAW_OBSERVED_STRATEGY': '1', 'METTACLAW_INPUT_ACK_SECONDS': '0.01',
        'METTACLAW_INPUT_LEDGER_SOCKET': '{folder}/no-ledger.sock', 'METTACLAW_TELEGRAM_SERVICE_SOCKET': '{folder}/no-service.sock'})
        self.assertIn('(NOTICE error error none)', output)


if __name__ == '__main__':
    unittest.main()
