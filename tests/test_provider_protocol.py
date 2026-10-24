import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import provider_protocol as protocol


class ProtocolTests(unittest.TestCase):
    def test_synthetic_effort_maps_once_without_mutating_generic_payload(self):
        original = dict(model="fixture", messages=[], reasoning={"effort": "medium"})
        converted = protocol.synthetic_body(original)
        self.assertEqual(converted["reasoning_effort"], "medium")
        self.assertNotIn("reasoning", converted)
        self.assertEqual(original["reasoning"], {"effort": "medium"})
        self.assertEqual(protocol.synthetic_body(converted), converted)
        self.assertNotIn("reasoning_effort", protocol.synthetic_body(dict(reasoning={"effort": "default"})))
        for body in (dict(reasoning={"effort": "xhigh"}), dict(reasoning={"budget": 10}),
                     dict(reasoning={"effort": "medium"}, reasoning_effort="low"),
                     dict(reasoning_effort="none")):
            with self.subTest(body=body), self.assertRaises(ValueError):
                protocol.synthetic_body(body)

    def test_dynamic_input_does_not_change_prefix_identity_or_gain_cache_hints(self):
        a = protocol.prompt_observation("STATIC_cache_boundary_A")
        b = protocol.prompt_observation("STATIC_cache_boundary_longer B")
        self.assertEqual(a["stable_digest"], b["stable_digest"])
        self.assertNotEqual(a["dynamic_chars"], b["dynamic_chars"])
        _, body = protocol.command_body("chat-completions", "m", 10, "low", "STATIC_cache_boundary_A")
        self.assertNotIn("cache_control", body)
        self.assertNotIn("prompt_cache_key", body)

    def test_cache_usage_missing_invalid_and_zero_are_distinct(self):
        self.assertEqual(protocol.cache_observation({})["status"], "unknown")
        zero = protocol.cache_observation(dict(prompt_tokens=100, prompt_tokens_details=dict(cached_tokens=0)))
        self.assertEqual(zero["fraction"], 0)
        self.assertEqual(protocol.cache_observation(dict(prompt_tokens=100, prompt_tokens_details=dict(cached_tokens=101)))["status"], "unknown")
        self.assertEqual(protocol.cache_observation(dict(input_tokens=10, cache_read_input_tokens=80,
            cache_creation_input_tokens=10))["fraction"], .8)
    def test_command_roles_effort_and_layout_content(self):
        prompt = "S_cache_boundary_T_cache_boundary_U_cache_boundary_user _cache_boundary_ literal"
        path, body = protocol.command_body("anthropic", "claude-sonnet-5-5", 2524, "low", prompt)
        self.assertEqual(path, "/messages")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual([b["text"] for b in body["system"]], ["S", "T", "U"])
        self.assertEqual(body["messages"], [{"role": "user", "content": [{"type": "text", "text": "user _cache_boundary_ literal"}]}])
        _, chat = protocol.command_body("chat-completions", "m", 10, "medium", prompt)
        self.assertEqual(chat["messages"][0], {"role": "system", "content": "STU"})
        self.assertEqual(chat["messages"][1]["content"], "user _cache_boundary_ literal")

    def test_transcript_chunks_join_for_prefix_caching_providers(self):
        prompt = "S_cache_boundary_T_cache_boundary_A\n_cache_chunk_\nB_cache_boundary_user"
        _, body = protocol.command_body("responses", "gpt-5.5", 20, "high", prompt)
        self.assertEqual(body["instructions"], "STA\n\nB")
        _, other = protocol.command_body("responses", "gpt-5.5", 20, "high",
                                         "S_cache_boundary_T_cache_boundary_A\n_cache_chunk_\nB\n_cache_chunk_\nC_cache_boundary_user")
        # The cache key follows the first stable part, which a growing transcript leaves unchanged.
        self.assertEqual(body["prompt_cache_key"], other["prompt_cache_key"])

    def test_anthropic_preserves_region_checkpoints_and_history_before_tail(self):
        prompt = "S_cache_boundary_T_cache_boundary_A\n_cache_chunk_\nB\n_cache_chunk_\nC_cache_boundary_user"
        _, body = protocol.command_body("anthropic", "fixture", 100, "high", prompt)
        self.assertEqual([b["text"] for b in body["system"]], ["S", "T", "A", "B", "C"])
        self.assertEqual([i for i, b in enumerate(body["system"]) if "cache_control" in b], [0, 1, 3, 4])
        self.assertEqual(body["messages"], [{"role": "user", "content": [{"type": "text", "text": "user"}]}])
        self.assertEqual(body["output_config"], {"effort": "high"})

    def test_anthropic_respects_other_cache_slots_and_explicit_opt_out(self):
        for other in range(4):
            body = dict(system=[dict(type="text", text=str(i)) for i in range(9)],
                        messages=[dict(role="user", content=[dict(type="text", text="message",
                                     cache_control={"type": "ephemeral"}) for _ in range(other)])])
            body["system"][-1]["cache_control"] = dict(type="ephemeral", ttl="1h")
            before = copy.deepcopy(body)
            protocol.anthropic_cache_system(body, [0, 1, 8])
            controls = [b.get("cache_control") for b in body["system"] if "cache_control" in b]
            self.assertEqual(len(controls) + other, 4)
            self.assertTrue(all(c == dict(type="ephemeral", ttl="1h") for c in controls))
            self.assertEqual(body["messages"], before["messages"])
            self.assertEqual([b["text"] for b in body["system"]], [b["text"] for b in before["system"]])
        request = dict(messages=[dict(role="system", content="private"), dict(role="user", content="input")],
                       max_tokens=100, cache_system=False)
        disabled = protocol.anthropic_body(request, "fixture")
        self.assertNotIn("cache_control", disabled["system"][0])

    def test_cache_observation_identifies_changed_prefix_without_retaining_content(self):
        bodies = [protocol.command_body("anthropic", "fixture", 100, "high",
                    "static λ_cache_boundary_state_cache_boundary_history-" + str(i) + "_cache_boundary_input")[1]
                  for i in range(2)]
        first, second = map(protocol.anthropic_cache_observation, bodies)
        self.assertEqual(first["scope"], second["scope"])
        self.assertEqual(first["blocks"][:2], second["blocks"][:2])
        self.assertNotEqual(first["blocks"][2]["prefix_digest"], second["blocks"][2]["prefix_digest"])
        self.assertNotIn("static", json.dumps(first))
        self.assertNotIn("input", json.dumps(first))
        changed_effort = copy.deepcopy(bodies[1])
        changed_effort["output_config"]["effort"] = "low"
        self.assertNotEqual(protocol.anthropic_cache_observation(changed_effort)["scope"], first["scope"])

    def test_written_checkpoint_model_matches_changing_tail_and_lookback_limit(self):
        # A cache entry exists only where a previous request put a marker.
        # Matching earlier content alone cannot read an unwritten endpoint.
        def writes(body):
            return {b["prefix_digest"] for b in protocol.anthropic_cache_observation(body)["blocks"] if b["ttl"]}

        def reads(body, entries):
            blocks = protocol.anthropic_cache_observation(body)["blocks"]
            return {block["index"] for end in blocks if end["ttl"]
                    for block in blocks[max(0, end["index"] - 19):end["index"] + 1]
                    if block["prefix_digest"] in entries}

        def request(tail, corrected):
            body = dict(model="fixture", system=[dict(type="text", text="stable " * 500),
                                                  dict(type="text", text=tail, cache_control=dict(type="ephemeral"))],
                        messages=[])
            return protocol.anthropic_cache_system(body, [0, 1]) if corrected else body

        self.assertEqual(reads(request("next", False), writes(request("old", False))), set())
        self.assertEqual(reads(request("next", True), writes(request("old", True))), {0})
        old = dict(model="fixture", system=[dict(type="text", text=str(i)) for i in range(2)], messages=[])
        old["system"][-1]["cache_control"] = dict(type="ephemeral")
        grown = copy.deepcopy(old)
        grown["system"][-1].pop("cache_control")
        grown["system"].extend(dict(type="text", text=str(i)) for i in range(2, 22))
        grown["system"][-1]["cache_control"] = dict(type="ephemeral")
        self.assertEqual(reads(grown, writes(old)), set())
        protocol.anthropic_cache_system(grown, [1, 21])
        self.assertEqual(reads(grown, writes(old)), {1})

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
