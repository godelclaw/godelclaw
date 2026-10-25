"""Local capture observes final wire bytes without changing provider behavior."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import native_llm_host as host
import test_native_llm_boundary as boundary


class RequestCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / 'request-capture'
        env = mock.patch.dict(os.environ, METTACLAW_COGNITIVE_HEALTH_PATH=str(self.root / 'health.json'))
        env.start()
        self.addCleanup(env.stop)

    def enable(self, remaining=2):
        self.capture.mkdir()
        (self.capture / 'control.json').write_text(json.dumps(dict(remaining=remaining, purpose='turn')))

    def records(self):
        return [json.loads(path.read_text()) for path in self.capture.glob('*.json')
                if path.name != 'control.json']

    def plan(self, protocol='responses', provider='openai', purpose='turn'):
        return SimpleNamespace(kind='command', correlation='fixture-call', native_ledger=True,
            protocol=protocol, provider=provider, stream=False, purpose=purpose,
            headers='{"Authorization":"fixture-header-secret"}')

    def test_disabled_capture_creates_nothing(self):
        host.capture_request(self.plan(), '{"input":"private fixture"}')
        self.assertFalse(self.capture.exists())

    def test_final_transformed_body_is_exact_and_headers_are_absent(self):
        self.enable(3)
        for protocol, provider in [('responses', 'openai'), ('anthropic', 'anthropic'),
                                   ('chat-completions', 'synthetic')]:
            with self.subTest(protocol=protocol):
                body = dict(model='fixture', messages=[dict(role='user', content='λ\nfixture')],
                            reasoning=dict(effort='low'))
                with mock.patch.object(host.stream_sink, 'configured', return_value=True), \
                     mock.patch.object(host.stream_sink, 'emit'):
                    plan = self.plan(protocol, provider)
                    wire = host.observe_wire(plan, host.wire_body(plan, json.dumps(body)))
                record = next(row for row in self.records() if row['protocol'] == protocol)
                self.assertEqual(record['wire_body'], wire)
                self.assertNotIn('fixture-header-secret', json.dumps(record))
                self.assertTrue(json.loads(wire)['stream'])
        self.assertEqual(json.loads((self.capture / 'control.json').read_text())['remaining'], 0)

    def test_capture_counter_and_purpose_do_not_limit_calls(self):
        self.enable(1)
        for purpose in ('coding', 'compaction', 'turn', 'turn'):
            host.capture_request(self.plan(purpose=purpose), '{"input":"fixture"}')
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]['purpose'], 'turn')

    def test_concurrent_observers_share_the_capture_count(self):
        self.enable(2)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda _: host.capture_request(self.plan(), '{"input":"fixture"}'), range(12)))
        self.assertEqual(len(self.records()), 2)

    def test_bad_control_or_storage_does_not_break_requests(self):
        self.enable()
        for text in ('broken', '[]', '{"remaining":true,"purpose":"turn"}'):
            (self.capture / 'control.json').write_text(text)
            host.capture_request(self.plan(), '{}')
        (self.capture / 'control.json').write_text('{"remaining":1,"purpose":"turn"}')
        with mock.patch('tempfile.mkstemp', side_effect=OSError('disk full')):
            host.capture_request(self.plan(), '{}')
        self.assertEqual(self.records(), [])


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to a native Python/HTTP engine')
class NativeCaptureTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest

    def check_capture(self, subscription=False):
        self.peer.delay = .01
        self.env.update(OPENAI_API_KEY='fixture-secret', OPENAI_BASE_URL=self.env['SYNTHETIC_BASE_URL'],
                        SYNTHETIC_MODEL='openai:fixture-model')
        self.peer.reply = {'id': 'r', 'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '((nop))'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}
        capture = Path(self.env['METTACLAW_COGNITIVE_HEALTH_PATH']).parent / 'request-capture'
        capture.mkdir()
        (capture / 'control.json').write_text('{"remaining":1,"purpose":"turn"}')
        forms = f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
        if subscription:
            self.env.update(CETTA_IO_AUTH_CONTEXT_FIXTURE_URL_PREFIX=self.env['SYNTHETIC_BASE_URL']+'/',
                CETTA_IO_AUTH_CONTEXT_FIXTURE_HEADER='Authorization',
                CETTA_IO_AUTH_CONTEXT_FIXTURE_ENV='OPENAI_API_KEY', CETTA_IO_AUTH_CONTEXT_FIXTURE_PREFIX='Bearer ')
            session=json.dumps(json.dumps({'value':{'credential_alias':'CONTEXT_FIXTURE'},
                                         'api_base':self.env['SYNTHETIC_BASE_URL']+'/'}))
            forms += f'!(let $plan (chatgpt:command-plan "chatgpt:fixture-model" 100 "low" "private λ fixture" (json-parse {session})) (println! (RESULT (llm:run $plan))))\n'
        else:
            forms += '!(println! (RESULT (llm:command "openai:fixture-model" 100 "low" "private λ fixture")))\n'
        self.run_guest(forms)
        self.assertEqual(len(self.peer.requests), 1)
        records = [json.loads(path.read_text()) for path in capture.glob('*.json') if path.name != 'control.json']
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0]['wire_body']), self.peer.requests[0][2])
        self.assertNotIn('fixture-secret', json.dumps(records))
        if subscription:
            self.assertNotIn('max_output_tokens', json.loads(records[0]['wire_body']))

    def test_capture_equals_the_actual_api_http_body(self):
        self.check_capture()

    def test_capture_equals_the_actual_subscription_http_body(self):
        self.check_capture(subscription=True)

    def test_disabled_capture_does_not_send_the_body_back_to_the_host(self):
        self.env.update(OPENAI_API_KEY='fixture-secret', OPENAI_BASE_URL=self.env['SYNTHETIC_BASE_URL'],
                        SYNTHETIC_MODEL='openai:fixture-model')
        self.peer.reply = {'id': 'r', 'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '((nop))'}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}
        probe = self.directory / 'capture_probe.py'
        probe.write_text('import native_llm_host\n'
            'def forbid(*args):\n    raise AssertionError("disabled capture crossed the host boundary")\n'
            'def install():\n    native_llm_host.observe_wire = forbid\n    return True\n')
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            f'!(import! &self "{probe}")\n!(py-call (capture_probe.install))\n'
            '!(println! (RESULT (llm:command "openai:fixture-model" 100 "low" "private λ fixture")))\n')
        self.assertIn('(RESULT ((nop)))', output)
        self.assertEqual(len(self.peer.requests), 1)


if __name__ == '__main__':
    unittest.main()
