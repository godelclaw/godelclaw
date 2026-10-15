"""Native Responses stream/profile qualification. All input and keys are fixtures."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest

import test_native_llm_boundary as boundary

ROOT = Path(__file__).resolve().parents[1]


def response():
    return {'id': 'resp_fixture', 'status': 'completed', 'output': [
        {'type': 'reasoning', 'id': 'reason_fixture', 'summary': [], 'encrypted_content': 'opaque — Ω'},
        {'type': 'message', 'role': 'assistant', 'status': 'completed',
         'content': [{'type': 'output_text', 'text': '(nop)', 'annotations': []}]}],
        'usage': {'input_tokens': 10, 'output_tokens': 2}}


def event(value, name=None, newline='\n'):
    kind = name if name is not None else value['type']
    return 'event: ' + kind + newline + 'data: ' + json.dumps(value, ensure_ascii=False) + newline * 2


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to a native engine')
class StreamCodecTests(unittest.TestCase):
    def run_metta(self, body):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'probe.metta'
            path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/responses_stream_cetta.metta')) + ')\n'
                            '!(import! &self ' + json.dumps(str(ROOT / 'src/chatgpt_plan_cetta.metta')) + ')\n' + body)
            result = subprocess.run([os.environ['CETTA_BIN'], '--lang', 'petta', '--quiet', str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('(Error ', result.stdout)
        return result.stdout

    def decode(self, body):
        return self.run_metta('!(println! (responses:stream ' + json.dumps(body, ensure_ascii=False) + '))\n')

    def rendered(self, output):
        return json.loads(json.loads(next(line for line in output.splitlines() if line.startswith('"'))))

    def test_completion_preserves_the_whole_response_and_ordered_reasoning(self):
        final = response()
        final['output'].append({'type': 'function_call', 'call_id': 'call_fixture',
                                'name': 'fixture-tool', 'arguments': '{"x":1}'})
        body = event({'type': 'response.output_text.delta', 'delta': 'provisional'})
        body += event({'type': 'response.completed', 'response': final})
        out = self.run_metta('!(case (responses:stream ' + json.dumps(body, ensure_ascii=False) + ')'
          ' (((ResponsesComplete $value) (let $text (llm:v1:render $value) (println! $text))) ($bad (println! $bad))))\n')
        self.assertEqual(self.rendered(out), final)

    def test_crlf_multiline_data_bom_and_comments(self):
        value = {'type': 'response.completed', 'response': response()}
        body = '\ufeff:keepalive\r\nid: ignored\r\nretry: 1000\r\nevent: response.completed\r\n'
        body += ''.join('data: ' + line + '\r\n' for line in json.dumps(value, indent=2).splitlines()) + '\r\n'
        self.assertIn('(ResponsesComplete ', self.decode(body))

    def test_truncated_or_provisional_text_is_never_a_completed_response(self):
        delta = event({'type': 'response.output_text.delta', 'delta': '(nop)'})
        self.assertIn('(ResponsesStreamError missing-completion)', self.decode(delta))
        completed = event({'type': 'response.completed', 'response': response()})
        self.assertIn('(ResponsesStreamError truncated-event)', self.decode(completed.rstrip('\n')))

    def test_failure_after_deltas_keeps_provider_code(self):
        body = event({'type': 'response.output_text.delta', 'delta': '(nop)'})
        failed = {'type': 'response.failed', 'response': {'status': 'failed',
            'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'fixture usage error'}}}
        out = self.decode(body + event(failed))
        self.assertIn('subscription_sharing_usage_limit_exceeded', out)
        self.assertNotIn('ResponsesComplete', out)

    def test_incomplete_invalid_json_and_conflicting_event_types(self):
        with self.subTest('incomplete'):
            self.assertIn('provider-incomplete', self.decode(event({'type': 'response.incomplete',
                          'response': {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}})))
        with self.subTest('invalid-json'):
            self.assertIn('ResponsesStreamError', self.decode('data: {\n\n'))
        with self.subTest('conflicting-type'):
            self.assertIn('conflicting-event-type', self.decode(event({'type': 'response.failed'}, 'response.completed')))

    def test_completed_requires_completed_status_and_rejects_later_data(self):
        bad = response(); bad['status'] = 'in_progress'
        self.assertIn('invalid-completed-response', self.decode(event({'type': 'response.completed', 'response': bad})))
        good = event({'type': 'response.completed', 'response': response()})
        self.assertIn('data-after-completion', self.decode(good + event({'type': 'response.output_text.delta', 'delta': 'late'})))

    def test_data_only_events_and_optional_done(self):
        body = 'data: ' + json.dumps({'type': 'response.completed', 'response': response()}) + '\n\n'
        self.assertIn('ResponsesComplete', self.decode(body + 'data: [DONE]\n\n'))
        self.assertIn('ResponsesComplete', self.decode('\ufeff' + body))
        self.assertIn('missing-completion', self.decode('data: [DONE]\n\n'))

    def test_chatgpt_profile_changes_only_the_explicit_wire_differences(self):
        request = {'model': 'fixture-model', 'instructions': 'one stable instruction',
                   'input': 'user text', 'max_output_tokens': 100, 'store': True,
                   'reasoning': {'effort': 'high'}, 'include': ['reasoning.encrypted_content'],
                   'prompt_cache_key': 'stable-fixture'}
        out = self.run_metta('!(let $text (chatgpt:wire ' + json.dumps(json.dumps(request)) + ') (println! $text))\n')
        actual = self.rendered(out)
        self.assertNotIn('max_output_tokens', actual)
        self.assertTrue(actual['stream']); self.assertFalse(actual['store'])
        self.assertEqual(actual['input'], [{'role': 'user', 'content': 'user text'}])
        for key in ('model', 'instructions', 'reasoning', 'include', 'prompt_cache_key'):
            self.assertEqual(actual[key], request[key])

    def test_chatgpt_profile_preserves_history_and_opaque_tool_identities(self):
        history = [response()['output'][0], {'type': 'function_call', 'call_id': 'call_1',
            'name': 'fixture-tool', 'arguments': '{}'}, {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'result'}]
        request = {'model': 'fixture-model', 'input': history, 'max_output_tokens': 100}
        out = self.run_metta('!(let $text (chatgpt:wire ' + json.dumps(json.dumps(request)) + ') (println! $text))\n')
        actual = self.rendered(out)
        self.assertEqual(actual['input'], history)

    def test_chatgpt_profile_reports_incompatible_options_without_dropping_them(self):
        for key, value in [('previous_response_id', 'resp_fixture'), ('conversation', 'conv_fixture'),
                           ('temperature', 0.4), ('metadata', {'tag': 'fixture'})]:
            with self.subTest(key=key):
                request = {'model': 'fixture-model', 'input': 'hello', key: value}
                out = self.run_metta('!(let $value (chatgpt:wire ' + json.dumps(json.dumps(request))
                                     + ') (println! $value))\n')
                self.assertIn('(llm:v1:failure (unsupported-chatgpt-field "' + key + '"))', out)

    def test_duplicate_keys_and_missing_input_are_reported(self):
        self.assertIn('invalid-or-ambiguous-json', self.decode('data: {"type":"response.completed","type":"error"}\n\n'))
        out = self.run_metta('!(let $value (chatgpt:wire "{}") (println! $value))\n')
        self.assertIn('(llm:v1:failure expected-input-array)', out)
        out = self.run_metta('!(let $value (chatgpt:wire "{") (println! $value))\n')
        self.assertIn('(llm:v1:failure invalid-or-ambiguous-json)', out)


class ResponsesPeer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, dict(self.headers), request))
        body = event({'type': 'response.output_text.delta', 'delta': '(nop)'})
        if getattr(self.server, 'failure_code', None):
            body += event({'type': 'response.failed', 'response': {'status': 'failed',
                'error': {'code': self.server.failure_code, 'message': 'fixture plan error'}}})
        elif not getattr(self.server, 'truncated', False):
            body += event({'type': 'response.completed', 'response': response()})
        data = body.encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        try:
            for start in range(0, len(data), 19):
                self.wfile.write(data[start:start + 19]); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError): pass


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to an HTTP-enabled engine')
class ResponsesHTTPTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest

    def prepare(self):
        self.peer.RequestHandlerClass = ResponsesPeer
        self.env.update(SYNTHETIC_MODEL='openai:fixture-model', OPENAI_API_KEY='fixture-only-key',
                        OPENAI_BASE_URL='http://127.0.0.1:%d/v1' % self.peer.server_port)

    def test_native_http_stream_returns_complete_result_once(self):
        self.prepare()
        out = self.run_guest('!(import! &self ' + json.dumps(str(ROOT / 'src/native_llm.metta')) + ')\n'
                             '!(println! (RESULT (llm:command "openai:fixture-model" 42 "high" "stable_cache_boundary_input")))\n')
        self.assertIn('(RESULT (nop))', out)
        self.assertEqual(len(self.peer.requests), 1)
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer fixture-only-key')
        self.assertNotIn('stream', body)  # Existing API-key request shape is preserved.
        self.assertFalse(body['store'])
        self.assertEqual(body['max_output_tokens'], 42)  # Ordinary API-key profile retains its limit.
        self.assertEqual(body['instructions'], 'stable')
        self.assertNotIn('fixture-only-key', out)

    def test_native_http_truncation_does_not_run_provisional_command(self):
        self.prepare(); self.peer.truncated = True
        out = self.run_guest('!(import! &self ' + json.dumps(str(ROOT / 'src/native_llm.metta')) + ')\n'
                             '!(println! (RESULT (llm:command "openai:fixture-model" 42 "high" "input")))\n')
        self.assertNotIn('(RESULT (nop))', out)
        self.assertEqual(len(self.peer.requests), 1)
        with sqlite3.connect(self.directory / 'ledger.db') as db:
            self.assertEqual(db.execute('SELECT state FROM native_calls').fetchall(), [('failed',)])

    def test_usage_error_after_deltas_returns_failure_once(self):
        self.prepare(); self.peer.failure_code = 'subscription_sharing_usage_limit_exceeded'
        out = self.run_guest('!(import! &self ' + json.dumps(str(ROOT / 'src/native_llm.metta')) + ')\n'
                             '!(println! (RESULT (llm:command "openai:fixture-model" 42 "high" "input")))\n')
        self.assertNotIn('(RESULT (nop))', out)
        self.assertEqual(len(self.peer.requests), 1)
        with sqlite3.connect(self.directory / 'ledger.db') as db:
            self.assertEqual(db.execute('SELECT state FROM native_calls').fetchall(), [('failed',)])

    def test_chatgpt_profile_posts_through_native_http_without_a_python_client(self):
        self.prepare()
        self.env.update(CETTA_IO_AUTH_PLAN_FIXTURE_URL_PREFIX=self.env['OPENAI_BASE_URL'] + '/',
                        CETTA_IO_AUTH_PLAN_FIXTURE_HEADER='Authorization',
                        CETTA_IO_AUTH_PLAN_FIXTURE_ENV='OPENAI_API_KEY',
                        CETTA_IO_AUTH_PLAN_FIXTURE_PREFIX='Bearer ')
        request = {'model': 'fixture-model', 'instructions': 'stable', 'input': 'hello',
                   'max_output_tokens': 42}
        forms = ('!(import! &self ' + json.dumps(str(ROOT / 'src/responses_stream_cetta.metta')) + ')\n'
                 '!(import! &self ' + json.dumps(str(ROOT / 'src/chatgpt_plan_cetta.metta')) + ')\n'
                 '!(let $wire (chatgpt:wire ' + json.dumps(json.dumps(request)) + ')\n'
                 ' (let $call (io:submit (http:request "POST" '
                 + json.dumps(self.env['OPENAI_BASE_URL'] + '/responses')
                 + ' ((http:header "Content-Type" "application/json") (http:credential "PLAN_FIXTURE")) $wire 10000 1048576))\n'
                 ' (case $call (((io:pending $id)\n'
                 ' (case (io:wait $id 10000)\n'
                 '  (((io:event $id (http:response 200 $body))\n'
                 '    (let $done (responses:stream $body) (println! $done)))\n'
                 '   ($bad (println! $bad))))) ($bad (println! $bad))))))\n')
        out = self.run_guest(forms)
        self.assertIn('(ResponsesComplete ', out)
        self.assertEqual(len(self.peer.requests), 1)
        path, headers, actual = self.peer.requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer fixture-only-key')
        self.assertTrue(actual['stream']); self.assertFalse(actual['store'])
        self.assertNotIn('max_output_tokens', actual)
        self.assertEqual(actual['input'], [{'role': 'user', 'content': 'hello'}])
        self.assertNotIn('fixture-only-key', out)


if __name__ == '__main__': unittest.main()
