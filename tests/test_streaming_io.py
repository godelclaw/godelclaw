"""Native incremental framing and display isolation on actual provider bytes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import test_native_llm_boundary as boundary
from test_responses_stream_engine import event, response
import provider_stream
import stream_sink

ROOT = boundary.ROOT


class ProjectionTests(unittest.TestCase):
    def test_replay_does_not_duplicate_distinct_occurrences(self):
        p = stream_sink.Projection()
        e = dict(producer="p", sequence=1, session="s", attempt="a",
                 kind="text.delta", item="m", part=0, channel="commentary", text="same")
        self.assertTrue(p.apply(e))
        self.assertFalse(p.apply(e))
        p.apply({**e, "sequence": 2, "item": "other"})
        self.assertEqual([i["text"] for i in p.sessions["s"]["items"].values()], ["same", "same"])
        p.apply({**e, "sequence": 3, "kind": "text.reconcile", "item": "final", "text": "authoritative"})
        self.assertEqual([i["text"] for i in p.sessions["s"]["items"].values()], ["authoritative"])

    def test_storage_keeps_long_text_and_closing_viewer_is_irrelevant(self):
        with tempfile.TemporaryDirectory() as d:
            w = stream_sink.Writer(d, "session")
            text = "λ" * 10000
            self.assertEqual(w.emit(dict(kind="text.delta", item="m", text=text)), 1)
            self.assertTrue(w.flush(2))
            saved = json.loads(w.path.read_text().splitlines()[0])
            self.assertEqual(saved["text"], text)

    def test_anthropic_terminal_preserves_summary_signature_and_tools(self):
        frames = [
            {"type": "message_start", "message": {"id": "m", "role": "assistant", "content": [], "usage": {}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Compare boundaries"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "c", "name": "metta", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"program":'}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"((nop))"}'}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
            {"type": "message_stop"},
        ]
        wire = "".join(event(v) for v in frames)
        value = json.loads(provider_stream.anthropic(wire))
        self.assertEqual(value["content"][0]["signature"], "opaque")
        self.assertEqual(value["content"][1]["input"], {"program": "((nop))"})
        self.assertEqual(value["usage"], {"output_tokens": 12})
        for bad in (wire.rsplit("event:", 1)[0], wire + event({"type": "ping"}),
                    wire.replace('"thinking_delta"', '"text_delta"')):
            with self.assertRaises((ValueError, TypeError)):
                provider_stream.anthropic(bad)


class StreamingPeer(boundary.Peer):
    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()
        chunks = [
            event({"type": "response.reasoning_summary_text.delta", "item_id": "r", "output_index": 0,
                   "summary_index": 0, "delta": "Checking ž 🐈"}),
            event({"type": "response.output_text.delta", "item_id": "m", "output_index": 1,
                   "content_index": 0, "delta": "Running the boundary checks."}),
            event({"type": "response.output_item.added", "output_index": 2,
                   "item": {"type": "function_call", "id": "tool", "name": "metta"}}),
            event({"type": "response.function_call_arguments.delta", "item_id": "tool",
                   "output_index": 2, "delta": '{"program":"PRIVATE_ARGUMENT'}),
            ":" + " " * 128 + "\n\n",
        ]
        try:
            for frame in chunks:
                data = frame.encode()
                for i in range(0, len(data), 5):
                    self.wfile.write(data[i:i+5]); self.wfile.flush()
            self.server.sent.set()
            self.server.release.wait(8)
            if self.server.fail:
                self.wfile.write(event({"type": "response.failed", "response": {"status": "failed"}}).encode())
            else:
                final = response()
                final["output"][0]["id"] = "r"
                final["output"][0]["summary"] = [{"type": "summary_text", "text": "Checking ž 🐈"}]
                final["output"][1]["content"][0]["text"] = "Running the boundary checks."
                final["output"].append({"type": "function_call", "id": "tool",
                    "call_id": "c", "name": "metta", "arguments": '{"program":"((nop))"}'})
                self.wfile.write(event({"type": "response.completed", "response": final}).encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class OtherPeer(boundary.Peer):
    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()
        if self.server.protocol == "anthropic":
            prefix = [
                {"type":"message_start","message":{"id":"m","role":"assistant","content":[],"usage":{}}},
                {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}},
                {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Checking boundaries"}},
                {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"OPAQUE_SIGNATURE"}},
                {"type":"content_block_stop","index":0},
                {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}},
                {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Running checks."}},
                {"type":"content_block_stop","index":1}]
            suffix = [
                {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"c","name":"metta","input":{}}},
                {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":'{"program":"((nop))"}'}},
                {"type":"content_block_stop","index":2},
                {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":5}},
                {"type":"message_stop"}]
            encode=event
        else:
            prefix=[{"id":"r","choices":[{"index":0,"delta":{"role":"assistant","content":"Running checks.","reasoning_content":"PRIVATE_REASONING"},"finish_reason":None}]}]
            suffix=[{"id":"r","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c","type":"function","function":{"name":"metta","arguments":'{"program":"((nop))"}'}}]},"finish_reason":"tool_calls"}]}]
            encode=lambda v:"data: "+json.dumps(v)+"\n\n"
        try:
            self.wfile.write(("".join(map(encode,prefix))+":"+" "*128+"\n\n").encode());self.wfile.flush()
            self.server.sent.set();self.server.release.wait(8)
            self.wfile.write("".join(map(encode,suffix)).encode())
            if self.server.protocol!="anthropic":self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN")
class NativeStreamTests(unittest.TestCase):
    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.peer.RequestHandlerClass = StreamingPeer
        self.peer.sent, self.peer.release = threading.Event(), threading.Event()
        self.peer.fail = False
        self.env.update(METTACLAW_STREAM_DIR=str(self.directory/"display"),
            METTACLAW_MODEL_COMMENTARY="1", METTACLAW_ACTIVITY_CALL_SCOPE="coding-test",
            METTACLAW_CODING_SESSION="1", SYNTHETIC_MODEL="openai:fixture",
            OPENAI_API_KEY="fixture-secret", OPENAI_BASE_URL=self.env["SYNTHETIC_BASE_URL"])
        self.process = None

    def tearDown(self):
        self.peer.release.set()
        if self.process and self.process.poll() is None:
            self.process.kill(); self.process.communicate()
        boundary.NativeBoundaryTests.tearDown(self)

    def observations(self):
        return [json.loads(line) for path in (self.directory/"display").glob("*.jsonl")
                for line in path.read_text().splitlines() if line]

    def test_coding_observation_precedes_terminal_and_never_exposes_arguments(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                if self.process and self.process.poll() is None:
                    self.process.kill(); self.process.communicate()
                self.peer.fail = failure
                self.peer.release.clear(); self.peer.sent.clear()
                old_attempts = {e.get("attempt") for e in self.observations()}
                path = self.directory/"probe.metta"
                path.write_text('!(import! &self "'+str(ROOT/'src/native_llm.metta')+'")\n'
                    '!(let $plan (py-call (native_llm_host.command_plan "openai:fixture" 512 "high" "inspect")) '
                    '(progn (py-call (native_llm_host.for_coding $plan)) '
                    '(println! (RESULT (llm:run $plan)))))\n')
                output = (self.directory/("output-"+str(failure))).open("w")
                self.addCleanup(output.close)
                self.process = subprocess.Popen([os.environ["CETTA_BIN"],"--lang","petta",
                    "--import-mode","ancestor-walk",str(path)],cwd=ROOT,env=self.env,
                    stdout=output,stderr=subprocess.PIPE,text=True)
                self.assertTrue(self.peer.sent.wait(6))
                deadline = time.monotonic()+4
                while not any(e.get("channel")=="reasoning-summary" and e.get("attempt") not in old_attempts for e in self.observations()) and time.monotonic()<deadline:
                    time.sleep(.02)
                records=[e for e in self.observations() if e.get("attempt") not in old_attempts]
                self.assertTrue(any(e.get("text")=="Checking ž 🐈" for e in records),records)
                self.assertIsNone(self.process.poll())
                self.assertNotIn("PRIVATE_ARGUMENT",json.dumps(records))
                self.assertNotIn("opaque",json.dumps(records))
                self.assertTrue(self.peer.requests[-1]["stream"])
                self.assertEqual(self.peer.requests[-1]["reasoning"]["summary"],"auto")
                self.peer.release.set()
                self.process.communicate(timeout=15)
                self.assertEqual(self.process.returncode,0)
                last=[e for e in self.observations() if e["kind"]=="attempt.finished" and e.get("attempt") not in old_attempts][-1]
                self.assertEqual(last["status"],"interrupted" if failure else "completed")

    def test_native_framer_is_invariant_at_every_text_split(self):
        wire='\ufeff:ping\r\nevent: x\r\ndata: {"a":\r\ndata: "ž🐈"}\r\n\r\ndata: [DONE]\n\n'
        forms=['!(import! &self "'+str(ROOT/'src/stream_sse.metta')+'")']
        for i in range(len(wire)+1):
            forms.append('!(let* (((SSEBatch $one $a) (sse:feed (sse:new) '+json.dumps(wire[:i])+' 65536)) '
                '((SSEBatch $two $b) (sse:feed $one '+json.dumps(wire[i:])+' 65536))) '
                '(println! (FRAMES (sse:reverse (sse:reverse $a ()) $b))))')
        path=self.directory/"framing.metta";path.write_text("\n".join(forms))
        result=subprocess.run([os.environ["CETTA_BIN"],"--lang","petta",str(path)],cwd=ROOT,
            env=self.env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=[line for line in result.stdout.splitlines() if line.startswith("(FRAMES ")]
        self.assertEqual(len(rows),len(wire)+1)
        self.assertEqual(len(set(rows)),1,set(rows))

    def test_anthropic_and_chat_streams_keep_the_terminal_command(self):
        self.peer.RequestHandlerClass=OtherPeer
        for protocol,model in (("anthropic","claude-opus-5-5"),("chat-completions","fixture-model")):
            with self.subTest(protocol=protocol):
                self.peer.protocol=protocol;self.peer.sent.clear();self.peer.release.clear()
                self.env.update(SYNTHETIC_MODEL=model,ANTHROPIC_API_KEY="fixture-secret",
                    ANTHROPIC_BASE_URL=self.env["SYNTHETIC_BASE_URL"])
                old={r.get("attempt") for r in self.observations()}
                path=self.directory/"other.metta"
                path.write_text('!(import! &self '+json.dumps(str(ROOT/'src/native_llm.metta'))+')\n'+
                    '!(println! (RESULT (llm:command '+json.dumps(model)+' 512 "high" "inspect")))')
                self.process=subprocess.Popen([os.environ["CETTA_BIN"],"--lang","petta","--import-mode","ancestor-walk",str(path)],
                    cwd=ROOT,env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertTrue(self.peer.sent.wait(5))
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    rows=[r for r in self.observations() if r.get("attempt") not in old]
                    if any(r.get("text")=="Running checks." for r in rows):break
                    time.sleep(.02)
                self.assertIsNone(self.process.poll())
                self.assertTrue(any(r.get("text")=="Running checks." for r in rows),rows)
                self.assertNotIn("OPAQUE_SIGNATURE",json.dumps(rows));self.assertNotIn("PRIVATE_REASONING",json.dumps(rows))
                if protocol=="anthropic":
                    self.assertTrue(any(r.get("text")=="Checking boundaries" for r in rows))
                    self.assertEqual(self.peer.requests[-1]["thinking"]["display"],"summarized")
                    self.assertEqual(self.peer.requests[-1]["output_config"]["effort"],"high")
                self.peer.release.set();out,err=self.process.communicate(timeout=10)
                self.assertEqual(self.process.returncode,0,err)
                self.assertIn("((nop))",out)


if __name__ == "__main__":
    unittest.main()
