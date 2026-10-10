"""Delayed native streams, soft detachment, recycling and truncation."""
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
import test_native_llm_boundary as boundary
from test_provider_stream import frame


class StreamingPeer(boundary.Peer):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, dict(self.headers), request))
        time.sleep(self.server.delay)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        body = (": keepalive\n\n" + frame({"role": "assistant", "reasoning_content": "reason ž"}) +
                frame({"reasoning_content": "r"}) * getattr(self.server, "reasoning_deltas", 0) +
                frame({"content": "(nop)"}) + frame({}, "stop", usage={"prompt_tokens": 8, "completion_tokens": 3}))
        if not self.server.truncated:
            body += "data: [DONE]" + ("" if getattr(self.server, "terminal_eof", False) else "\n\n")
        encoded = body.encode()
        try:
            # Deliberately split UTF-8 code units and JSON/event boundaries.
            step = 65536 if getattr(self.server, "reasoning_deltas", 0) else 7
            for index in range(0, len(encoded), step):
                self.wfile.write(encoded[index:index+step]); self.wfile.flush()
                time.sleep(.002)
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to a native HTTP/Python build")
class NativeStreamingTest(unittest.TestCase):
    run_guest = boundary.NativeBoundaryTests.run_guest
    tearDown = boundary.NativeBoundaryTests.tearDown

    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.peer.RequestHandlerClass = StreamingPeer
        self.peer.truncated = False
        self.peer.delay = .2
        self.env.update(METTACLAW_LLM_STREAM="1", SYNTHETIC_TIMEOUT=".05",
                        METTACLAW_LLM_HARD_SECONDS="3", METTACLAW_LLM_CONNECT_SECONDS="1",
                        METTACLAW_LLM_FIRST_BYTE_SECONDS="1", METTACLAW_LLM_IDLE_SECONDS=".5")

    def journal(self):
        with sqlite3.connect(self.directory / "ledger.db") as db:
            return db.execute("SELECT state,soft_elapsed,result,reason FROM native_calls").fetchall()

    def test_silent_first_wait_and_soft_deadline_keep_one_request(self):
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertTrue(self.peer.requests[0][2]["stream"])
        state, soft, result, reason = self.journal()[0]
        self.assertEqual((state, soft, reason), ("completed", 1, None))
        self.assertIn("(nop)", result)

    def test_long_reasoning_stream_survives_sse_envelope_overhead(self):
        self.peer.reasoning_deltas = 14000
        self.env["METTACLAW_LLM_HARD_SECONDS"] = "10"
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 32768 "low" "input")))\n')
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.journal()[0][0], "completed")

    def test_terminal_marker_without_sse_blankline_completes_once(self):
        self.peer.terminal_eof = True
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.journal()[0][0], "completed")

    def test_detached_wait_returns_pending_and_later_delivers_same_call(self):
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(let $plan (py-call (native_llm_host.command_plan "fixture-model" 42 "low" "input"))\n'
            '  (let $state (llm:begin $plan)\n'
            '    (let $waiting (llm:wait-once $plan $state 20)\n'
            '      (progn (println! (DETACHED $waiting))\n'
            '             (println! (RESULT (llm:after-submit $plan $waiting)))))))\n')
        self.assertIn("DETACHED (llm:v1:pending", output)
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(len(self.peer.requests), 1)

    def test_recycle_request_waits_for_the_admitted_call(self):
        fixture = self.directory / "recycle_fixture.py"
        fixture.write_text('import os\nfrom pathlib import Path\ndef request():\n'
                           '    Path(os.environ["METTACLAW_RECYCLE_REQUEST_PATH"]).write_text("fixture")\n'
                           '    return True\n')
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            f'!(import! &self "{fixture}")\n'
            '!(let $plan (py-call (native_llm_host.command_plan "fixture-model" 42 "low" "input"))\n'
            '  (let $state (llm:begin $plan)\n'
            '    (progn (py-call (recycle_fixture.request))\n'
            '           (println! (RESULT (llm:after-submit $plan $state))))))\n')
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(self.journal()[0][0], "completed")
        self.assertEqual(len(self.peer.requests), 1)

    def test_eof_does_not_dispatch_partial_text(self):
        self.peer.truncated = True
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.assertIn("(RESULT ())", output)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.journal()[0][0], "failed")
        self.assertEqual(self.journal()[0][3], "incomplete-stream")

    def test_foreign_pending_state_cannot_consume_the_owned_answer(self):
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            '!(let $plan (py-call (native_llm_host.command_plan "fixture-model" 42 "low" "input"))\n'
            '  (let $state (llm:begin $plan)\n'
            '    (progn (println! (FOREIGN (llm:wait-once $plan (llm:v1:pending other other "chat-completions" 1) 20)))\n'
            '           (println! (RESULT (llm:after-submit $plan $state))))))\n')
        self.assertIn("FOREIGN (llm:v1:rejected ownership-error)", output)
        self.assertIn("(RESULT (nop))", output)
        self.assertEqual(len(self.peer.requests), 1)

    def test_steered_result_is_retained_as_evidence_without_action(self):
        fixture = self.directory / "guard_fixture.py"
        fixture.write_text('import strategy_host\nrevision="captured"\n'
            'strategy_host.enabled=lambda: True\nstrategy_host.guard=lambda: revision\n'
            'def steer():\n    global revision\n    revision="steered"\n    return True\n')
        output = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
            f'!(import! &self "{fixture}")\n'
            '!(let $plan (py-call (native_llm_host.command_plan "fixture-model" 42 "low" "input"))\n'
            '  (let $state (llm:begin $plan)\n'
            '    (progn (py-call (guard_fixture.steer))\n'
            '           (println! (RESULT (llm:after-submit $plan $state))))))\n')
        self.assertIn("(RESULT ())", output)
        state, _, evidence, reason = self.journal()[0]
        self.assertEqual((state, reason), ("late", "captured-authority-changed"))
        self.assertIn("(nop)", evidence)
        self.assertEqual(len(self.peer.requests), 1)


if __name__ == "__main__":
    unittest.main()
