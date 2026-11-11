"""Native failure evidence across PeTTa/HE and real offline provider requests."""
import json
import os
from pathlib import Path
import socket
import unittest

import test_native_llm_boundary as boundary

ROOT = boundary.ROOT


class TruncatedPeer(boundary.Peer):
    protocol_version = 'HTTP/1.1'

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, dict(self.headers), payload))
        prefix = b'{"private":"RESPONSE-CANARY'
        self.send_response(200)
        self.send_header('Content-Length', str(len(prefix) + 100))
        self.end_headers()
        self.wfile.write(prefix); self.wfile.flush()
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to the qualified native bundle')
class NativeTransportTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest
    records = boundary.NativeBoundaryTests.records
    column = boundary.NativeBoundaryTests.column

    def test_connection_refusal_keeps_safe_native_code_in_both_dialects(self):
        with socket.socket() as refused:
            refused.bind(('127.0.0.1', 0))
            url = 'http://127.0.0.1:%d/URL-CANARY' % refused.getsockname()[1]
            code = ('!(import! &self %s)\n'
                    '!(let (io:pending $id) (io:submit\n'
                    '    (http:request "GET" %s () "" 1000 4096))\n'
                    '   (println! (OBSERVATION (llm-transport:wait $id 2000))))\n') % (
                        json.dumps(str(ROOT / 'src/native_transport_cetta.metta')), json.dumps(url))
            for dialect in ('petta', 'he'):
                with self.subTest(dialect=dialect):
                    out = self.run_guest(code, lang=dialect)
                    self.assertIn('(NativeHttpFailureV1 "transport" 7)', out)
                    self.assertNotIn('URL-CANARY', out + self.last_stderr)
                    self.assertNotIn('http:error', out)

    def test_truncated_response_is_not_reported_as_a_timeout_or_completion(self):
        self.peer.RequestHandlerClass = TruncatedPeer
        self.env['SYNTHETIC_BASE_URL'] += '/URL-CANARY'
        out = self.run_guest(
            '!(import! &self %s)\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "PROMPT-CANARY")))\n'
            % json.dumps(str(ROOT / 'src/native_llm.metta')))
        self.assertIn('(RESULT ())', out)
        self.assertIn('transport_code=18; transport_kind=transport', self.last_stderr)
        self.assertNotIn('TimeoutError', out + self.last_stderr)
        for canary in ('URL-CANARY', 'PROMPT-CANARY', 'RESPONSE-CANARY'):
            self.assertNotIn(canary, out + self.last_stderr)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.column('calls', 'state'), [('uncertain',)])

    def test_genuine_hard_timeout_keeps_the_timeout_code(self):
        self.peer.delay = .4
        self.env.update(METTACLAW_LLM_SOFT_SECONDS='.02', METTACLAW_LLM_HARD_SECONDS='.08')
        out = self.run_guest(
            '!(import! &self %s)\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n'
            % json.dumps(str(ROOT / 'src/native_llm.metta')))
        self.assertIn('(RESULT ())', out)
        self.assertIn('transport_code=28; transport_kind=timeout', self.last_stderr)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.column('calls', 'state'), [('uncertain',)])

    def test_foreign_error_notice_does_not_fail_another_pending_request(self):
        forms = ('!(import! &self %s)\n'
                 '!(assert (== (llm:observe-plan "unused-plan" StillPending "e" "c" "responses" 7\n'
                 '   (io:event 8 (NativeHttpFailureV1 "timeout" 28))) StillPending))\n'
                 '!(println! "FOREIGN_ERROR_IGNORED")\n') % json.dumps(str(ROOT / 'src/native_llm.metta'))
        for dialect in ('petta', 'he'):
            out = self.run_guest(forms, lang=dialect)
            self.assertIn('FOREIGN_ERROR_IGNORED', out)
        self.assertEqual(self.peer.requests, [])

    def test_unclassified_failure_does_not_invent_a_timeout(self):
        fixture = self.directory / 'unknown_failure.py'
        fixture.write_text('''from types import SimpleNamespace
import native_llm_host
def check():
    plan = SimpleNamespace(kind="structured", ready="false", provider="synthetic")
    try:
        native_llm_host.failed(plan, "c", "transport-error", 0)
    except RuntimeError as error:
        return str(error)
    raise AssertionError("expected the failure boundary")
''')
        out = self.run_guest('!(import! &self %s)\n!(println! (DIAG (py-call (unknown_failure.check))))\n'
                             % json.dumps(str(fixture)))
        self.assertIn('native provider failed: OSError', out)
        self.assertNotIn('TimeoutError', out + self.last_stderr)
        self.assertEqual(self.peer.requests, [])

    def test_inconsistent_or_malformed_diagnostics_do_not_break_failure_settlement(self):
        fixture = self.directory / 'diagnostic_control.py'
        fixture.write_text('''from types import SimpleNamespace
import native_llm_host as host
def check():
    for kind, code in [("timeout",7), ("transport",28), ("transport",True),
                       ("transport",-1), ("transport",7.5), ("secret",7)]:
        plan=SimpleNamespace()
        assert host.transport_failure(plan, kind, code) == 0
        assert not hasattr(plan,"error")
    for kind, code, error in [("timeout",28,TimeoutError),("transport",7,OSError)]:
        plan=SimpleNamespace()
        assert host.transport_failure(plan,kind,code) == 1
        assert type(plan.error) is error
    return "DIAGNOSTIC_CONTROLS_OK"
''')
        out = self.run_guest('!(import! &self %s)\n!(println! (DIAG (py-call (diagnostic_control.check))))\n'
                             % json.dumps(str(fixture)))
        self.assertIn('DIAGNOSTIC_CONTROLS_OK', out)


if __name__ == '__main__':
    unittest.main()
