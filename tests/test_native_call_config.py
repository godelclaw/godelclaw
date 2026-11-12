"""Immutable provider configuration crosses the host boundary once per call."""
import json
import os
import re
import unittest

import test_native_llm_boundary as boundary


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to the native test bundle')
class NativeCallConfigTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest
    records = boundary.NativeBoundaryTests.records
    column = boundary.NativeBoundaryTests.column

    def instrument(self, entry=None):
        return ('!(import! &self ' + json.dumps(str(entry or boundary.ROOT / 'src/native_llm.metta')) + ')\n'
                '!(import! &self ' + json.dumps(str(boundary.ROOT / 'tests/call_config_probe.py')) + ')\n'
                '!(py-call (call_config_probe.start))\n')

    def test_command_uses_one_capture_and_keeps_wire_and_ledger_contract(self):
        out = self.run_guest(self.instrument() +
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "system_cache_boundary_λ (data)")))\n'
            '!(println! (CAPTURES (py-call (call_config_probe.count))))\n')
        self.assertIn('(RESULT (nop))', out)
        self.assertIn('(CAPTURES 1)', out)
        self.assertEqual(len(self.peer.requests), 1)
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer fixture-secret')
        self.assertEqual(body['messages'], [{'role': 'system', 'content': 'system'},
                                           {'role': 'user', 'content': 'λ (data)'}])
        self.assertEqual(self.column('requests', 'status', 'output'), [('completed', 2)])
        self.assertEqual(self.column('calls', 'state'), [('completed',)])

    def test_structured_call_uses_the_same_native_configuration(self):
        request = {'max_tokens': 42, 'messages': [{'role': 'user', 'content': 'Quote "λ" (data)'}],
                   'tools': []}
        out = self.run_guest(self.instrument() +
            '!(let $plan (llm:structured-plan (json-parse ' +
            json.dumps(json.dumps(request)) + ') "turn") (println! (RESULT (llm:run $plan))))\n'
            '!(println! (CAPTURES (py-call (call_config_probe.count))))\n')
        self.assertIn('(CAPTURES 1)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.peer.requests[0][2]['messages'], request['messages'])
        self.assertEqual(self.column('calls', 'state'), [('completed',)])
        self.assertNotIn('AssertionError', out)

    def test_large_quoted_context_survives_the_configuration_boundary(self):
        payload = 'λ "memo" (data)\n' * 131072
        out = self.run_guest(self.instrument() +
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" ' +
            json.dumps('system_cache_boundary_' + payload) + ')))\n'
            '!(println! (CAPTURES (py-call (call_config_probe.count))))\n', timeout=60)
        self.assertIn('(RESULT (nop))', out)
        self.assertIn('(CAPTURES 1)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertTrue(self.peer.requests[0][2]['messages'][-1]['content'] == payload,
                        'large quoted context changed at the transport-config boundary')
        self.assertEqual(self.column('calls', 'state'), [('completed',)])

    def test_replayed_plan_never_captures_or_submits_again(self):
        out = self.run_guest(self.instrument() +
            '!(let $plan (py-call (native_llm_host.native_command_plan "fixture-model" 42 "low" "input"))\n'
            '   (progn (println! (FIRST (llm:run $plan))) (println! (SECOND (llm:run $plan)))))\n'
            '!(println! (CAPTURES (py-call (call_config_probe.count))))\n')
        self.assertIn('(FIRST (nop))', out)
        self.assertIn('(SECOND ())', out)
        self.assertIn('(CAPTURES 1)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.column('calls', 'state'), [('completed',)])

    def test_he_uses_one_capture_without_evaluating_transport_descriptions(self):
        out = self.run_guest(self.instrument() +
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n'
            '!(println! (CAPTURES (py-call (call_config_probe.count))))\n', lang='he')
        self.assertIn('(nop)', out, self.last_stderr)
        self.assertIn('(CAPTURES 1)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.column('calls', 'state'), [('completed',)])

    def test_old_individual_field_rule_is_caught_before_any_provider_effect(self):
        source = (boundary.ROOT / 'src/native_llm.metta').read_text()
        marker = '(let $state (pcall:begin $owner $transport) (llm:after-submit $owner $state))'
        self.assertEqual(source.count(marker), 1)
        source = source.replace(marker,
            '(let* (($old (py-call (native_llm_host.field $owner "base"))) '
            '($state (pcall:begin $owner $transport))) (llm:after-submit $owner $state))')
        source = re.sub(r'(import!\s+&self\s+)(\./src/[^\s()]+)',
                        lambda match: match[1] + json.dumps(str(boundary.ROOT / match[2][2:])), source)
        mutant = self.directory / 'individual-field-mutant.metta'
        mutant.write_text(source)
        with self.assertRaisesRegex(AssertionError, 'CALL_CONFIG_FORBIDDEN_FIELD:base'):
            self.run_guest(self.instrument(mutant) +
                '!(llm:command "fixture-model" 42 "low" "input")\n')
        self.assertEqual(self.peer.requests, [])


if __name__ == '__main__':
    unittest.main()
