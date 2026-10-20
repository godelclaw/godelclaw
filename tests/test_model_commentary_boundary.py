"""Offline provider-to-command commentary qualification; no external requests."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_native_llm_boundary as boundary

sys.path.insert(0, str(boundary.ROOT / 'src'))
import public_worklog
import synthetic_llm


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to an HTTP/Python build')
class NativeCommentaryTest(unittest.TestCase):
    run_guest = boundary.NativeBoundaryTests.run_guest
    tearDown = boundary.NativeBoundaryTests.tearDown

    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.worklog = self.directory / 'worklog'
        public_worklog.transact(self.worklog, 'on')
        self.env.update(METTACLAW_MODEL_COMMENTARY='1', METTACLAW_LLM_STREAM='0',
                        METTACLAW_PUBLIC_WORK_DIR=str(self.worklog), METTACLAW_PUBLIC_WORK_LIB='')

    def notes(self):
        return public_worklog.transact(self.worklog, 'read')[1]

    def call(self):
        return self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')

    def test_completed_commentary_is_not_command_text(self):
        self.peer.reply['choices'][0]['message'].update(
            content=json.dumps({'commentary':'Checking display layout.', 'commands':'((nop))'}),
            reasoning_content='PRIVATE REASONING')
        output = self.call()
        self.assertIn('(RESULT ((nop)))', output)
        self.assertNotIn('Checking display', output)
        self.assertEqual([n['text'] for n in self.notes()], ['Checking display layout.'])
        self.assertEqual(len(self.peer.requests), 1)

    def test_responses_protocol_preserves_the_same_separation(self):
        from test_responses_stream_engine import response
        reply = response()
        reply['output'][1]['content'][0]['text'] = json.dumps(
            {'commentary':'Checking Responses layout.', 'commands':'((nop))'})
        self.peer.reply = reply
        self.env.update(SYNTHETIC_MODEL='openai:fixture-model',
                        OPENAI_API_KEY='fixture-only-key',
                        OPENAI_BASE_URL=self.env['SYNTHETIC_BASE_URL'])
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "openai:fixture-model" 42 "low" "input")))\n')
        self.assertIn('(RESULT ((nop)))', output)
        self.assertEqual([n['text'] for n in self.notes()], ['Checking Responses layout.'])
        self.assertEqual(len(self.peer.requests), 1)
        self.assertNotIn('opaque', self.notes()[0]['text'])

    def test_malformed_envelope_cannot_be_repaired_into_actions(self):
        self.peer.reply['choices'][0]['message']['content'] = '{"commentary":"hello","commands":"((shell'
        self.assertIn('(RESULT ())', self.call())
        self.assertEqual(self.notes(), [])

    def test_length_envelope_does_not_release_commands(self):
        self.peer.reply['choices'][0].update(finish_reason='length')
        self.peer.reply['choices'][0]['message']['content'] = json.dumps(
            {'commentary':'unfinished', 'commands':'((nop))'})
        self.assertIn('(RESULT ())', self.call())
        self.assertEqual(self.notes(), [])

    def test_legacy_response_remains_usable_without_publication(self):
        self.assertIn('(RESULT (nop))', self.call())
        self.assertEqual(self.notes(), [])

    def test_display_off_does_not_prevent_command_completion(self):
        public_worklog.transact(self.worklog, 'off')
        self.peer.reply['choices'][0]['message']['content'] = json.dumps(
            {'commentary':'not retained', 'commands':'((nop))'})
        self.assertIn('(RESULT ((nop)))', self.call())
        self.assertFalse((self.worklog / 'notes.jsonl').exists())


class PythonCommentaryTest(unittest.TestCase):
    def test_python_transport_separates_completed_envelope(self):
        reply = {'choices':[{'message':{'content':json.dumps(
            {'commentary':'Public finding.', 'commands':'((nop))'})}, 'finish_reason':'stop'}]}
        provider = {'key':'fixture', 'protocol':'chat-completions', 'name':'synthetic'}
        with patch.dict(os.environ, METTACLAW_MODEL_COMMENTARY='1'), \
             patch.object(synthetic_llm, 'current_model', return_value='fixture-model'), \
             patch.object(synthetic_llm, '_provider_for', return_value=provider), \
             patch.object(synthetic_llm, '_exchange', return_value=reply), \
             patch('model_commentary.Publication') as publication, \
             patch('context_budget.observe'), patch.object(synthetic_llm, '_note_answered'), \
             patch('cognitive_health.turn_completed'):
            self.assertEqual(synthetic_llm.chat('fixture-model', 42, 'low', 'input'), '((nop))')
            publication.return_value.publish.assert_called_once_with('Public finding.')


if __name__ == '__main__':
    unittest.main()
