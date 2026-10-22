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
        self.assertIn('(RESULT ((nop)))', output, output + self.last_stderr)
        self.assertNotIn('Checking display', output)
        self.assertEqual([n['text'] for n in self.notes()], ['Checking display layout.'])
        self.assertEqual(len(self.peer.requests), 1)

    def test_native_metta_tool_is_the_only_command_authority(self):
        self.peer.reply['choices'][0].update(
            finish_reason='tool_calls',
            message={'role': 'assistant', 'content': 'Checking the typed boundary.',
                     'tool_calls': [{'id': 'call-1', 'type': 'function',
                         'function': {'name': 'metta', 'arguments':
                                      json.dumps({'program': '((nop))'})}}]})
        output = self.call()
        self.assertIn('(RESULT ((nop)))', output)
        self.assertEqual([n['text'] for n in self.notes()],
                         ['Checking the typed boundary.'])
        body = self.peer.requests[0][2]
        self.assertEqual(body['tools'][0]['function']['name'], 'metta')
        self.assertEqual(body['tools'][0]['function']['parameters']
                         ['properties']['program']['type'], 'string')

    def test_anthropic_native_metta_tool_roundtrip(self):
        self.env.update(SYNTHETIC_MODEL='claude-sonnet-5-5',
                        ANTHROPIC_API_KEY='fixture-secret',
                        ANTHROPIC_BASE_URL=self.env['SYNTHETIC_BASE_URL'])
        self.peer.reply = {'id': 'a', 'stop_reason': 'tool_use', 'content': [
            {'type': 'text', 'text': 'Continuing with typed commands.'},
            {'type': 'tool_use', 'id': 'call-a', 'name': 'metta',
             'input': {'program': '((nop))'}}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "claude-sonnet-5-5" 42 "low" "input")))\n')
        self.assertIn('(RESULT ((nop)))', output)
        self.assertEqual([n['text'] for n in self.notes()],
                         ['Continuing with typed commands.'])
        path, _headers, body = self.peer.requests[0]
        self.assertEqual(path, '/v1/messages')
        self.assertIn('tools', body, body)
        self.assertEqual(body['tools'][0]['name'], 'metta')
        self.assertEqual(body['tool_choice'], {'type': 'auto'})

    def test_subscription_native_metta_tool_roundtrip(self):
        self.env.update(
            CETTA_IO_AUTH_COMMENTARY_FIXTURE_URL_PREFIX=
                self.env['SYNTHETIC_BASE_URL'] + '/',
            CETTA_IO_AUTH_COMMENTARY_FIXTURE_HEADER='Authorization',
            CETTA_IO_AUTH_COMMENTARY_FIXTURE_ENV='SYNTHETIC_API_KEY',
            CETTA_IO_AUTH_COMMENTARY_FIXTURE_PREFIX='Bearer ')
        session = {'value': {'credential_alias': 'COMMENTARY_FIXTURE'},
                   'api_base': self.env['SYNTHETIC_BASE_URL'] + '/'}
        self.peer.reply = {'id': 'r', 'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': 'Subscription command is typed.'}]},
            {'type': 'function_call', 'call_id': 'call-r', 'name': 'metta',
             'arguments': json.dumps({'program': '((nop))'})}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}
        output = self.run_guest(
            f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(let* (($session (json-parse ' + json.dumps(json.dumps(session)) + ')) '
            '($plan (chatgpt:command-plan "chatgpt:fixture-model" 42 "low" '
            '"system_cache_boundary_user" $session))) '
            '(println! (RESULT (llm:run $plan))))\n')
        self.assertIn('(RESULT ((nop)))', output, output + self.last_stderr)
        self.assertEqual([n['text'] for n in self.notes()],
                         ['Subscription command is typed.'])
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer fixture-secret')
        self.assertIn('tools', body, body)
        self.assertEqual(body['tools'][0]['name'], 'metta')
        self.assertEqual(body['tool_choice'], 'auto')

    def test_rejected_prose_is_retained_and_next_turn_receives_actionable_feedback(self):
        import sqlite3
        text = "A substantive review that has not been sent to its requester."
        self.peer.reply['choices'][0]['message']['content'] = text
        output = self.call()
        self.assertIn('(RESULT ())', output)
        with sqlite3.connect(self.directory / 'ledger.db') as db:
            identity, state, reason = db.execute(
                "SELECT id,state,reason FROM native_calls ORDER BY created DESC LIMIT 1").fetchone()
        self.assertEqual((state, reason), ('completed', 'command-output:invalid-envelope'))
        self.assertEqual(self.notes(), [])
        feedback = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (py-call (native_lifetime.evidence_context)))\n'
            '!(println! (py-call (native_lifetime.model_result "' + identity + '")))\n')
        self.assertIn('PREVIOUS_COMMAND_OUTPUT_REJECTED', feedback)
        self.assertIn(text, feedback)
        self.assertIn('No commands', feedback)
        self.peer.reply['choices'][0]['message']['content'] = json.dumps(
            dict(commentary='', commands='((nop))'))
        self.assertIn('(RESULT ((nop)))', self.call())
        feedback = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (py-call (native_lifetime.evidence_context)))\n')
        self.assertNotIn('PREVIOUS_COMMAND_OUTPUT_REJECTED', feedback)

    def test_complete_fenced_json_crosses_the_same_native_terminal_boundary(self):
        self.peer.reply['choices'][0]['message']['content'] = '```json\n' + json.dumps(
            dict(commentary='Checked the source.', commands='((nop))')) + '\n```'
        self.assertIn('(RESULT ((nop)))', self.call())
        self.assertEqual([n['text'] for n in self.notes()], ['Checked the source.'])

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

    def test_rejected_output_is_failed_and_not_published(self):
        envelope = json.dumps({'commentary': 'not accepted', 'commands': '((nop))'})
        cases = [(reason, source, {})
                 for reason in ('length', 'refusal', 'content_filter', 'tool_calls', 'unknown')
                 for source in ('((nop))', envelope)]
        cases += [('stop', envelope, {'refusal': 'fixture refusal'}),
                  ('stop', envelope, {'tool_calls': [{'id': 'fixture'}]}),
                  ('stop', envelope + ' trailing', {})]
        for reason, source, fields in cases:
            with self.subTest(reason=reason, source=source, fields=fields):
                self.peer.reply['choices'][0] = {
                    'finish_reason': reason,
                    'message': {'role': 'assistant', 'content': source, **fields}}
                self.assertIn('(RESULT ())', self.call())
                health = json.loads((self.directory / 'health.json').read_text())
                self.assertEqual(health['last_outcome'], 'failed')
                self.assertEqual(self.notes(), [])
        self.assertEqual(len(self.peer.requests), len(cases))

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

    def test_python_rejects_invalid_terminal_and_envelope_states(self):
        envelope = json.dumps({'commentary': 'not accepted', 'commands': '((nop))'})
        cases = [(reason, source, {})
                 for reason in ('length', 'refusal', 'content_filter', 'tool_calls', 'unknown', None)
                 for source in ('((nop))', envelope)]
        cases += [('stop', envelope, {'refusal': 'fixture refusal'}),
                  ('stop', envelope, {'tool_calls': [{'id': 'fixture'}]}),
                  ('stop', envelope + ' trailing', {})]
        provider = {'key': 'fixture', 'protocol': 'chat-completions', 'name': 'synthetic'}
        for reason, source, fields in cases:
            reply = {'choices': [{'message': {'content': source, **fields}, 'finish_reason': reason}]}
            with self.subTest(reason=reason, source=source, fields=fields), \
                 patch.dict(os.environ, METTACLAW_MODEL_COMMENTARY='1'), \
                 patch.object(synthetic_llm, 'current_model', return_value='fixture-model'), \
                 patch.object(synthetic_llm, '_provider_for', return_value=provider), \
                 patch.object(synthetic_llm, '_exchange', return_value=reply) as exchange, \
                 patch('model_commentary.Publication') as publication, \
                 patch('context_budget.observe'), \
                 patch.object(synthetic_llm, '_note_answered') as answered, \
                 patch('cognitive_health.turn_completed') as completed, \
                 patch('cognitive_health.turn_failed') as failed:
                self.assertEqual(synthetic_llm.chat('fixture-model', 42, 'low', 'input'), '()')
                exchange.assert_called_once()
                answered.assert_not_called()
                completed.assert_not_called()
                failed.assert_called_once()
                publication.return_value.publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
