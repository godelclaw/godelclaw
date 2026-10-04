"""Native Claude tool batches survive Iter's structured provider boundary."""
import http.server
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "modes"), str(ROOT / "src")]
import runtime_host


class ClaudePeer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.received.append((self.path, dict(self.headers),
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        body = json.dumps(self.server.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class IterAnthropicTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ClaudePeer)
        self.server.received = []
        self.server.reply = {"content": [
            {"type": "thinking", "thinking": "fixture reasoning", "signature": "fixture-signature"},
            {"type": "redacted_thinking", "data": "fixture-opaque"},
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "call-a", "name": "shell", "input": {"command": "echo hello"}},
            {"type": "tool_use", "id": "call-b", "name": "nop", "input": {}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 17, "output_tokens": 12}}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        for name in ("gate", "record", "sleep"):
            patcher = patch.object(runtime_host, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(runtime_host, "mode", return_value="iter")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(runtime_host, "cognitive_health")
        self.health = patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("current_model", "claude-opus-5-5"), ("_provider_for", {
                "name": "anthropic", "protocol": "anthropic", "base": f"http://127.0.0.1:{self.server.server_port}/v1",
                "key": "fixture-only"})):
            patcher = patch.object(runtime_host.synthetic_llm, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(runtime_host.synthetic_llm, "_tally_anthropic")
        self.tally = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, messages):
        return {"model": "upstream-default", "messages": messages,
                "max_tokens": 2524, "tool_choice": "required",
                "extra_body": {"enable_thinking": True}, "tools": [{
                    "type": "function", "function": {"name": "nop",
                    "description": "finish without sending", "parameters": {"type": "object"}}}]}

    def test_native_roundtrip_keeps_batch_ids_and_result_order(self):
        system = {"role": "system", "content": "own identity"}
        user = {"role": "user", "content": "first turn"}
        answer = runtime_host.provider_request(self.request([system, user]))
        message = answer["choices"][0]["message"]
        self.assertEqual(answer["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["content"], "working")
        self.assertEqual(message["reasoning_details"], self.server.reply["content"][:2])
        self.assertEqual([call["id"] for call in message["tool_calls"]], ["call-a", "call-b"])
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]),
                         {"command": "echo hello"})
        history = [system, user, message,
            {"role": "tool", "tool_call_id": "call-a", "content": "hello"},
            {"role": "tool", "tool_call_id": "call-b", "content": "done"},
            {"role": "user", "content": "continue"}]
        runtime_host.provider_request(self.request(history))
        path, headers, payload = self.server.received[-1]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers["X-api-key"], "fixture-only")
        self.assertEqual(headers["Anthropic-version"], "2023-06-01")
        self.assertEqual(payload["model"], "claude-opus-5-5")
        self.assertEqual(payload["tool_choice"], {"type": "auto"})
        self.assertNotIn("enable_thinking", payload)
        self.assertEqual(payload["system"], [{"type": "text", "text": "own identity",
                                               "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(payload["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(payload["messages"][1]["content"][:2], self.server.reply["content"][:2])
        self.assertEqual([b["id"] for b in payload["messages"][1]["content"] if b["type"] == "tool_use"],
                         ["call-a", "call-b"])
        self.assertEqual(payload["messages"][2], {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-a", "content": "hello"},
            {"type": "tool_result", "tool_use_id": "call-b", "content": "done"},
            {"type": "text", "text": "continue"}]})
        self.tally.assert_called_with(self.server.reply["usage"])

    def test_token_stop_is_exposed_to_the_core_retry_policy(self):
        self.server.reply = {"content": [{"type": "text", "text": "unfinished"}],
                             "stop_reason": "max_tokens"}
        result = runtime_host.provider_request(self.request([{"role": "user", "content": "go"}]))
        self.assertEqual(result["choices"][0]["finish_reason"], "length")
        self.assertNotIn("tool_calls", result["choices"][0]["message"])

    def test_invalid_history_is_paced_and_reported_without_raw_content(self):
        request = self.request([{"role": "assistant", "tool_calls": [{
            "id": "invalid", "function": {"name": "nop", "arguments": "private invalid data"}}]}])
        with self.assertRaisesRegex(RuntimeError, "provider failed: JSONDecodeError"):
            runtime_host.provider_request(request)
        self.assertEqual(self.server.received, [])
        self.sleep.assert_not_called()
        self.health.turn_failed.assert_called_once_with("JSONDecodeError", retry_after=15)
        self.record.assert_called_with("provider-error", error="JSONDecodeError")


if __name__ == "__main__":
    unittest.main()
