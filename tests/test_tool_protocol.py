import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

from modes import tool_protocol as protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import provider_protocol  # noqa: E402


class Parameter:
    def __init__(self, name): self.name = name


class Tool:
    def __init__(self, name="metta"):
        self.name, self.description = name, "Evaluate one MeTTa expression"
        self.parameters = [Parameter("sexpression")]


class Message:
    def __init__(self, role, content=None):
        self.role, self.content = role, content


class ToolResponse(Message):
    def __init__(self, callid, content):
        super().__init__("tool", content); self.callid = callid


class OmegaCall:
    def __init__(self, ident="c1", name="metta", arguments=None):
        self.id, self.name = ident, name
        self.arguments = arguments or {"sexpression": "(+ 1 2)"}


class ToolMessage(Message):
    def __init__(self, calls):
        super().__init__("assistant", "[TOOL CALL]"); self.calls = calls


class Response:
    def __init__(self): self.calls = []; self.error = None
    def add_tool_call(self, call): self.calls.append(call); return self
    def with_error(self, error): self.error = error; return self


class Call:
    def __init__(self):
        self.id = self.name = self.error = None; self.arguments = {}
    def with_id(self, value): self.id = value; return self
    def with_name(self, value): self.name = value; return self
    def with_arguments(self, value): self.arguments = value; return self
    def with_error(self, value): self.error = value; return self


OMEGA = types.SimpleNamespace(LLMResponse=Response, LLMToolCall=Call)


class ToolProtocolTests(unittest.TestCase):
    def test_metta_is_an_ordinary_canonical_tool_call(self):
        call = protocol.ToolCallV1("c1", "metta", {"sexpression": "(+ 1 2)"})
        self.assertEqual(call.atom(),
            ("ToolCallV1", "c1", "metta", '{"sexpression":"(+ 1 2)"}'))
        self.assertEqual(protocol.call_from_provider(protocol.provider_call(call)), call)
        result = protocol.ToolResultV1("c1", "returned", "3")
        self.assertEqual(result.atom(), ("ToolResultV1", "c1", "returned", "3"))

    def test_omega_request_preserves_specs_calls_and_results(self):
        request = types.SimpleNamespace(
            tools=[Tool()], max_tokens=6000, reasoning_mode="medium",
            messages=[Message("system", "prompt"),
                      ToolMessage([OmegaCall()]), ToolResponse("c1", "3")])
        value = protocol.request_from_omega(request)
        self.assertEqual(value["tool_choice"], "required")
        self.assertEqual(value["tools"][0]["function"]["name"], "metta")
        self.assertEqual(value["messages"][1]["tool_calls"][0]["id"], "c1")
        self.assertEqual(value["messages"][2]["tool_call_id"], "c1")

    def test_provider_response_becomes_omega_calls(self):
        payload = {"choices": [{"message": {"tool_calls": [{
            "id": "c9", "type": "function", "function": {
                "name": "metta", "arguments": '{"sexpression":"(foo)"}'}}]}}]}
        response = protocol.response_to_omega(payload, OMEGA)
        self.assertIsNone(response.error)
        self.assertEqual((response.calls[0].id, response.calls[0].name,
                          response.calls[0].arguments),
                         ("c9", "metta", {"sexpression": "(foo)"}))

    def test_canonical_omega_history_projects_to_anthropic_ids(self):
        request = types.SimpleNamespace(
            tools=[Tool()], max_tokens=6000, reasoning_mode="high",
            messages=[Message("system", "prompt"),
                      ToolMessage([OmegaCall()]), ToolResponse("c1", "3")])
        body = provider_protocol.anthropic_body(
            protocol.request_from_omega(request), "claude-fixture")
        assistant = next(item for item in body["messages"]
                         if item["role"] == "assistant")
        returned = next(item for item in body["messages"]
                        if item["role"] == "user")
        use = next(block for block in assistant["content"]
                   if block["type"] == "tool_use")
        result = next(block for block in returned["content"]
                      if block["type"] == "tool_result")
        self.assertEqual(use["id"], "c1")
        self.assertEqual(result["tool_use_id"], "c1")
        self.assertEqual(body["tools"][0]["name"], "metta")

    def test_non_object_arguments_are_an_explicit_call_error(self):
        payload = {"choices": [{"message": {"tool_calls": [{
            "id": "bad", "function": {"name": "metta", "arguments": "[]"}}]}}]}
        response = protocol.response_to_omega(payload, OMEGA)
        self.assertIn("JSON object", response.calls[0].error)

    def test_schema_hash_is_stable_and_nonempty(self):
        self.assertEqual(len(protocol.schema_sha256()), 64)

    @unittest.skipUnless(os.environ.get("CETTA_MODULE_BIN"),
                         "set CETTA_MODULE_BIN to a module-enabled engine")
    def test_module_schema_loads(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "tool-schema.metta"
            probe.write_text('''
!(import! &self (library lib_module))
!(let* (($reader (langdef:load-baked-parser "module-core-reader"))
        ($document (module:read-file $reader "godelclaw:tool-calls" ''' +
                json.dumps(str(protocol.SCHEMA)) + '''))
        ($_ (langdef:close-parser $reader))
        ($presentation (module:assemble $document "Schema" LNil LNil OriginViewV1 "tool-calls:v1"))
        ($core (module:core $presentation "ToolCalls")))
   (println! (TOOL_SCHEMA $core)))
''')
            result = subprocess.run(
                [os.environ["CETTA_MODULE_BIN"], "--lang", "petta",
                 "--import-mode", "ancestor-walk", str(probe)],
                cwd=root, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ToolSpecV1", result.stdout)
        self.assertIn("ToolCallV1", result.stdout)
        self.assertIn("ToolResultV1", result.stdout)


if __name__ == "__main__": unittest.main()
