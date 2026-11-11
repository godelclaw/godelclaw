"""Offline controls for coding-context bounds and owned process waits."""
import json
import os
import unittest

import test_coding_session as session


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to the qualified bundle')
class CodingHarnessTests(unittest.TestCase):
    setUp = session.CodingSessionTests.setUp
    tearDown = session.CodingSessionTests.tearDown
    run_probe = session.CodingSessionTests.run_probe
    run_session = session.CodingSessionTests.run_session
    session = session.CodingSessionTests.session

    def test_shipped_working_rules_enter_the_native_session_without_persona_files(self):
        self.env['METTACLAW_CODING_GUIDANCE_DIR'] = str(session.ROOT / 'identity')
        workspace = self.directory / 'workspace'; workspace.mkdir()
        state = self.session('Read the coding rules and finish.', workspace)
        self.run_session(state)
        sources = json.loads((state / 'guidance.json').read_text())['payload']['sources']
        self.assertEqual([row['path'] for row in sources], [str(session.ROOT / 'identity/AGENTS.md')])
        text = next(m['content'] for m in self.server.requests[0]['messages']
                    if m['content'].startswith('# AGENTS.md instructions for '))
        self.assertIn('Coding-mode working guidance', text)
        self.assertIn('do not define a persona', text)
        self.assertIn('correctness defect hidden', text)
        self.assertNotIn('soul.md', text)

    def test_context_ceiling_also_respects_smaller_provider_windows(self):
        for reported, configured, expected in (
            # context_capacity reserves 32768 output tokens before this cap.
            ('1000000', '256000', 256000), ('64000', '256000', 31232),
            ('1000000', '128000', 128000), ('1000000', 'invalid', 256000),
            ('1000000', '0', 256000),
        ):
            with self.subTest(reported=reported, configured=configured):
                self.env['METTACLAW_CONTEXT_WINDOW_TOKENS'] = reported
                self.env['METTACLAW_CODING_CONTEXT_TOKENS'] = configured
                result = self.run_probe(
                    '!(import! &self ' + json.dumps(str(session.ROOT / 'src/native_llm.metta')) + ')\n'
                    '!(println! (Window (coding:window)))\n')
                self.assertIn('(Window %s)' % expected, result.stdout)
        self.assertEqual(self.server.requests, [])

    def poll_probe(self, command, *, stop=False):
        session = self.directory / 'session'; session.mkdir()
        workspace = self.directory / 'workspace'; workspace.mkdir()
        q = json.dumps
        args = q(json.dumps({'cmd': command, 'yield_time_ms': 0}))
        extra = ('($_ (fs:update-text %s "replace" "stop" "" ""))\n' % q(str(session / 'stop'))
                 if stop else '')
        forms = (
            '!(let* (($first (coding:exec-owned %s %s (json-parse %s)))\n'
            '        ($line (car-atom (str:split "\\n" $first)))\n'
            '        ($id (index-atom (str:split " " $line) 2))\n'
            '        %s'
            '        ($args (JsonObject ((JsonPair "execution_id" (JsonString $id)))))\n'
            '        ($result (coding:poll %s $args)))\n'
            '   (progn (println! (PollResult $result)) (coding:cancel %s $args)))\n'
        ) % (q(str(session)), q(str(workspace)), args, extra, q(str(session)), q(str(session)))
        return self.run_probe(forms)

    def test_default_poll_waits_for_a_delayed_result_without_another_model_call(self):
        result = self.poll_probe('sleep 1.4; printf POLL_COMPLETED')
        self.assertIn('Exit code: 0', result.stdout)
        self.assertIn('POLL_COMPLETED', result.stdout)
        self.assertNotIn('Still running', result.stdout)
        self.assertEqual(self.server.requests, [])

    def test_stop_interrupts_a_long_default_poll(self):
        result = self.poll_probe('exec sleep 5', stop=True)
        self.assertIn('Still running', result.stdout)
        self.assertEqual(self.server.requests, [])


if __name__ == '__main__':
    unittest.main()
