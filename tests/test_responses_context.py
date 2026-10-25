"""Large context stays in ordered input items on every Responses route.

All HTTP and credential values below are local fixtures. No context is clipped
to satisfy the independent limit on the provider's instructions field.
"""
import json
import os
import sqlite3
import unittest

import test_native_llm_boundary as boundary
import provider_protocol


LARGE = "λ" * 1_100_000
PARTS = [LARGE, "policy", "completed history", "next chunk"]
PROMPT = "_cache_boundary_".join([LARGE, "policy", "completed history\n_cache_chunk_\nnext chunk", "turn"])
INPUT = [{"role": "system", "content": part} for part in PARTS] + [{"role": "user", "content": "turn"}]


class ResponsesContextTests(unittest.TestCase):
    def test_python_command_preserves_all_large_unicode_parts(self):
        endpoint, body = provider_protocol.command_body("responses", "fixture", 100, "high", PROMPT)
        self.assertEqual(endpoint, "/responses")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"], INPUT)
        _, changed = provider_protocol.command_body("responses", "fixture", 100, "high",
                                                   PROMPT.rsplit("_cache_boundary_", 1)[0] + "_cache_boundary_changed")
        self.assertEqual(changed["prompt_cache_key"], body["prompt_cache_key"])

    def test_python_structured_preserves_roles_and_reasoning_tool_pairs(self):
        reasoning = {"type": "reasoning", "id": "r17", "encrypted_content": "opaque-fixture"}
        messages = [{"role": "system", "content": LARGE}, {"role": "developer", "content": "policy"},
                    {"role": "user", "content": "task"},
                    {"role": "assistant", "content": "", "reasoning_details": [reasoning],
                     "tool_calls": [{"id": "c17", "function": {"name": "inspect", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "c17", "content": "evidence"}]
        body = provider_protocol.responses_body({"max_tokens": 100, "messages": messages}, "fixture")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"], messages[:3] + [reasoning,
            {"type": "function_call", "call_id": "c17", "name": "inspect", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c17", "output": "evidence"}])


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to a native Python/HTTP engine")
class NativeResponsesContextTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest

    def prepare(self):
        self.peer.delay = .01
        self.env.update(OPENAI_API_KEY="fixture-secret", OPENAI_BASE_URL=self.env["SYNTHETIC_BASE_URL"])
        self.peer.reply = {"id": "r", "status": "completed", "output": [
            {"type": "function_call", "id": "item", "call_id": "c17", "name": "metta",
             "arguments": json.dumps({"program": "((nop))"})}],
            "usage": {"input_tokens": 10, "output_tokens": 2}}
        prompt = self.directory / "prompt.txt"
        prompt.write_text(PROMPT, encoding="utf-8")
        return (f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
                '!(import! &self (library fs))\n'), prompt

    def assert_wire(self, expected):
        self.assertEqual(len(self.peer.requests), 1)
        path, _, body = self.peer.requests[0]
        self.assertEqual(path, "/v1/responses")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"], expected)
        with sqlite3.connect(self.env["METTACLAW_LLM_LEDGER_PATH"]) as db:
            self.assertEqual(db.execute("SELECT status FROM requests").fetchall(), [("completed",)])
            self.assertEqual(db.execute("SELECT state FROM native_calls").fetchall(), [("completed",)])

    def test_native_api_command_with_and_without_commentary(self):
        for commentary in ("0", "1"):
            with self.subTest(commentary=commentary):
                self.peer.requests.clear()
                self.env.update(METTACLAW_MODEL_COMMENTARY=commentary, SYNTHETIC_MODEL="openai:fixture-model",
                                METTACLAW_LLM_LEDGER_PATH=str(self.directory / (commentary + ".db")))
                forms, path = self.prepare()
                self.run_guest(forms + f'!(let $prompt (fs:read-text {json.dumps(str(path))}) '
                    '(println! (RESULT (llm:command "openai:fixture-model" 100 "high" $prompt))))\n', timeout=40)
                self.assert_wire(INPUT)

    def subscription_session(self):
        self.env.update(CETTA_IO_AUTH_CONTEXT_FIXTURE_URL_PREFIX=self.env["SYNTHETIC_BASE_URL"] + "/",
                        CETTA_IO_AUTH_CONTEXT_FIXTURE_HEADER="Authorization",
                        CETTA_IO_AUTH_CONTEXT_FIXTURE_ENV="OPENAI_API_KEY",
                        CETTA_IO_AUTH_CONTEXT_FIXTURE_PREFIX="Bearer ")
        return json.dumps(json.dumps({"value": {"credential_alias": "CONTEXT_FIXTURE"},
                                      "api_base": self.env["SYNTHETIC_BASE_URL"] + "/"}))

    def test_native_subscription_command_with_and_without_commentary(self):
        for commentary in ("0", "1"):
            with self.subTest(commentary=commentary):
                self.peer.requests.clear()
                self.env.update(METTACLAW_MODEL_COMMENTARY=commentary,
                                METTACLAW_LLM_LEDGER_PATH=str(self.directory / (commentary + ".db")))
                forms, path = self.prepare()
                session = self.subscription_session()
                self.run_guest(forms +
                    f'!(let* (($prompt (fs:read-text {json.dumps(str(path))})) '
                    f'($session (json-parse {session})) '
                    '($plan (chatgpt:command-plan "chatgpt:fixture-model" 100 "high" $prompt $session))) '
                    '(println! (RESULT (llm:run $plan))))\n', timeout=40)
                self.assert_wire(INPUT)

    def test_native_structured_api_and_subscription(self):
        for subscription in (False, True):
            with self.subTest(subscription=subscription):
                self.peer.requests.clear()
                self.env.update(SYNTHETIC_MODEL="openai:fixture-model",
                                METTACLAW_LLM_LEDGER_PATH=str(self.directory / (str(subscription) + ".db")))
                forms, _ = self.prepare()
                messages = [{"role": "system", "content": LARGE},
                            {"role": "developer", "content": "policy"}, {"role": "user", "content": "task"}]
                request = self.directory / "request.json"
                request.write_text(json.dumps({"max_tokens": 100, "messages": messages}), encoding="utf-8")
                if subscription:
                    plan = f'(chatgpt:structured-plan "chatgpt:fixture-model" $request (json-parse {self.subscription_session()}))'
                else:
                    plan = '(py-call (native_llm_host.native_structured_json_plan (fs:read-text ' + json.dumps(str(request)) + ')))'
                self.run_guest(forms + f'!(let* (($request (json-parse (fs:read-text {json.dumps(str(request))}))) '
                               f'($plan {plan})) (println! (RESULT (llm:run $plan))))\n', timeout=40)
                self.assert_wire(messages)


if __name__ == "__main__":
    unittest.main()
