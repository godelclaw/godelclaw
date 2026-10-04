import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import provider_protocol as protocol


class ProtocolTests(unittest.TestCase):
    def test_command_roles_effort_and_layout_content(self):
        prompt = "S_cache_boundary_T_cache_boundary_U_cache_boundary_user _cache_boundary_ literal"
        path, body = protocol.command_body("anthropic", "claude-sonnet-5-5", 2524, "low", prompt)
        self.assertEqual(path, "/messages")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual([b["text"] for b in body["system"]], ["S", "T", "U"])
        self.assertEqual(body["messages"], [{"role": "user", "content": "user _cache_boundary_ literal"}])
        _, chat = protocol.command_body("chat-completions", "m", 10, "medium", prompt)
        self.assertEqual(chat["messages"][0], {"role": "system", "content": "STU"})
        self.assertEqual(chat["messages"][1]["content"], body["messages"][0]["content"])

    def test_omega_system_user_delimiter(self):
        _, body = protocol.command_body("responses", "gpt-5.5", 20, "high", " system :-:-:-: new input ")
        self.assertEqual(body["instructions"], "system")
        self.assertEqual(body["input"], "new input")
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertFalse(body["store"])

    def test_anthropic_thinking_and_signed_history(self):
        signed = {"type": "thinking", "thinking": "opaque", "signature": "signed"}
        request = {"messages": [{"role": "assistant", "reasoning_details": [signed],
            "tool_calls": [{"id": "c1", "function": {"name": "nop", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"}], "max_tokens": 2524,
            "extra_body": {"enable_thinking": True}, "reasoning": {"effort": "medium"}}
        body = protocol.anthropic_body(request, "claude-sonnet-5-5")
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertEqual(body["messages"][0]["content"][0], signed)
        self.assertEqual(body["messages"][1]["content"][0]["tool_use_id"], "c1")
        self.assertNotIn("thinking", protocol.anthropic_reasoning("medium"))
        with self.assertRaises(ValueError):
            protocol.anthropic_reasoning("none")

    def test_responses_roundtrip_keeps_call_identity_reasoning_and_usage(self):
        reasoning = {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"}
        wire = {"id": "resp1", "status": "completed", "output": [reasoning,
            {"type": "message", "content": [{"type": "output_text", "text": "working"}]},
            {"type": "function_call", "call_id": "call1", "name": "nop", "arguments": "{}"}],
            "usage": {"input_tokens": 40, "output_tokens": 12,
                      "output_tokens_details": {"reasoning_tokens": 7}}}
        result = protocol.normalize_response("responses", wire)
        message = result["choices"][0]["message"]
        self.assertEqual(message["tool_calls"][0]["id"], "call1")
        self.assertEqual(message["reasoning_details"], [reasoning])
        self.assertEqual(result["usage"]["completion_tokens"], 12)
        self.assertEqual(result["choices"][0]["finish_reason"], "tool_calls")
        body = protocol.responses_body({"messages": [message,
            {"role": "tool", "tool_call_id": "call1", "content": "done"}], "max_tokens": 30}, "gpt-5.5")
        self.assertEqual(body["input"][0], reasoning)
        self.assertEqual(body["input"][-1], {"type": "function_call_output", "call_id": "call1", "output": "done"})

    def test_truncation_and_refusal_do_not_become_normal_completion(self):
        result = protocol.normalize_response("responses", {"status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"}, "output": []})
        self.assertEqual(result["choices"][0]["finish_reason"], "length")
        result = protocol.normalize_response("anthropic", {"stop_reason": "max_tokens", "content": []})
        self.assertEqual(result["choices"][0]["finish_reason"], "length")
        result = protocol.normalize_response("responses", {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]})
        self.assertEqual(result["choices"][0]["finish_reason"], "refusal")


if __name__ == "__main__":
    unittest.main()
