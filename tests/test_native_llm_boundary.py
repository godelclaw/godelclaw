"""Offline native HTTP/provider qualification; every key and reply is a fixture."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import provider_protocol


class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests.append((self.path, dict(self.headers), json.loads(
            self.rfile.read(int(self.headers["Content-Length"])))))
        time.sleep(self.server.delay)
        body = json.dumps(self.server.reply).encode()
        self.send_response(self.server.status)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class NativeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        # Iter's data helper imports its upstream SDK, even though this client
        # must never construct or call one. Make that boundary executable.
        modules = self.directory / "fixture_modules"
        modules.mkdir()
        (modules / "openai.py").write_text('class OpenAI:\n'
            '    def __init__(self, *args, **kwargs):\n'
            '        raise AssertionError("native client invoked the Python provider SDK")\n')
        self.peer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.peer.requests, self.peer.delay, self.peer.status = [], .35, 200
        self.peer.reply = {"id": "fixture", "choices": [{"message": {
            "role": "assistant", "content": "(nop)"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        threading.Thread(target=self.peer.serve_forever, daemon=True).start()
        latch = self.directory / "lifecycle.json"
        latch.write_text('{"schema":1,"state":"running"}')
        self.env = {**os.environ, "PYTHONHOME": sys.prefix,
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": os.pathsep.join([str(modules), str(ROOT / "src"), str(ROOT / "modes"), str(ROOT / "modes/iter")]),
            "METTACLAW_ACTIVE_ENGINE": "cetta", "METTACLAW_ENGINE": "cetta", "METTACLAW_LLM_TRANSPORT": "native",
            "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "engine"),
            "METTACLAW_LOOP_MODE_PATH": str(self.directory / "loop-mode.json"),
            "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw", "METTACLAW_LLM_POLICY": "managed",
            "METTACLAW_LLM_POLICY_PATH": "", "METTACLAW_LLM_LEDGER_PATH": str(self.directory / "ledger.db"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_ANTHROPIC_USAGE_PATH": str(self.directory / "anthropic-usage.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_LIFECYCLE_PATH": str(latch), "SYNTHETIC_MODEL": "fixture-model",
            "METTACLAW_MODEL_STATE_PATH": str(self.directory / "model"),
            "SYNTHETIC_BASE_URL": f"http://127.0.0.1:{self.peer.server_port}/v1",
            "SYNTHETIC_API_KEY": "fixture-secret", "SYNTHETIC_EMPTY_BACKOFF": "0"}
        self.env.pop("METTACLAW_MODE_RUNTIME", None)

    def tearDown(self):
        self.peer.shutdown()
        self.peer.server_close()
        self.temporary.cleanup()

    def run_guest(self, forms, lang="petta"):
        entry = self.directory / "probe.metta"
        entry.write_text(forms)
        result = subprocess.run([os.environ["CETTA_BIN"], "--lang", lang,
            "--import-mode", "ancestor-walk", "--quiet", str(entry)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        self.last_stderr = result.stderr
        return result.stdout

    def test_slow_command_submits_once_and_uses_host_credential(self):
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n'
            '!(llm:command "fixture-model" 42 "low" "system_cache_boundary_user")\n')
        self.assertIn("(nop)", out)
        self.assertEqual(len(self.peer.requests), 1)
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer fixture-secret")
        self.assertEqual(body["messages"], [{"role": "system", "content": "system"},
            {"role": "user", "content": "user"}])

    def test_command_wire_parity_in_both_dialects_for_all_protocols(self):
        prompt = "S_cache_boundary_T_cache_boundary_U_cache_boundary_user _cache_boundary_ literal"
        self.peer.delay = .01
        cases = [("chat-completions", "fixture-model", self.peer.reply),
                 ("anthropic", "claude-fixture", {"id": "a", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "(nop)"}]}),
                 ("responses", "openai:fixture-model", {"id": "r", "status": "completed",
                    "output": [{"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "(nop)"}]}]})]
        self.env.update(ANTHROPIC_API_KEY="fixture-secret", OPENAI_API_KEY="fixture-secret",
                        ANTHROPIC_BASE_URL=self.env["SYNTHETIC_BASE_URL"],
                        OPENAI_BASE_URL=self.env["SYNTHETIC_BASE_URL"])
        for lang in ("he", "petta"):
            for protocol, model, reply in cases:
                self.env["SYNTHETIC_MODEL"] = model
                self.peer.reply = reply
                before = len(self.peer.requests)
                out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n'
                    f'!(llm:command {json.dumps(model)} 42 "low" {json.dumps(prompt)})\n', lang)
                self.assertIn("(nop)", out, f"{lang}:{protocol}: {self.last_stderr[-500:]}")
                self.assertEqual(len(self.peer.requests) - before, 1)
                effective = model.removeprefix("openai:")
                path, expected = provider_protocol.command_body(protocol, effective, 42, "low", prompt)
                if protocol == "chat-completions":
                    # Synthetic documents a scalar field, independent of the
                    # generic compatible conversation's reasoning object.
                    expected.pop("reasoning")
                    expected["reasoning_effort"] = "low"
                actual_path, _, body = self.peer.requests[-1]
                self.assertEqual(actual_path, "/v1" + path)
                self.assertEqual(body, expected)

    def test_a_plan_can_be_submitted_only_once(self):
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n'
            '!(let $plan (py-call (native_llm_host.command_plan "fixture-model" 42 "low" "input"))\n'
            '   (progn (println! (llm:run $plan)) (println! (llm:run $plan))))\n')
        self.assertIn("(nop)", out)
        self.assertEqual(len(self.peer.requests), 1)

    def test_codecs_keep_tools_reasoning_usage_and_stops(self):
        fixtures = [("anthropic", {"id": "a", "stop_reason": "tool_use", "content": [
            {"type": "thinking", "thinking": "opaque", "signature": "signed"},
            {"type": "tool_use", "id": "c1", "name": "nop", "input": {"x": 1}},
            {"type": "tool_use", "id": "c2", "name": "nop", "input": {}}],
            "usage": {"input_tokens": 10, "output_tokens": 2}}),
            ("responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "reasoning", "id": "r", "summary": [], "encrypted_content": "opaque"},
                           {"type": "function_call", "call_id": "c", "name": "nop", "arguments": "{}"}]}),
            ("responses", {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "refusal", "refusal": "no"}]}]})]
        forms = f'!(import! &self "{ROOT}/src/llm_boundary.metta")\n' + "\n".join(
            f'!(json-stringify (llm:decode {json.dumps(protocol)} {json.dumps(json.dumps(wire))}))'
            for protocol, wire in fixtures)
        for lang in ("petta", "he"):
            out = self.run_guest(forms, lang)
            decoded = [json.loads(json.loads(line)[0] if line.startswith("[") else json.loads(line))
                       for line in out.splitlines() if line.startswith(('"', '["'))]
            self.assertEqual(len(decoded), len(fixtures), out[-3000:])
            for actual, (protocol, wire) in zip(decoded, fixtures):
                expected = provider_protocol.normalize_response(protocol, wire)
                for result in (actual, expected):
                    for call in result["choices"][0]["message"].get("tool_calls", []):
                        call["function"]["arguments"] = json.loads(call["function"]["arguments"])
                self.assertEqual(actual, expected)

    def test_wrong_handle_and_duplicate_completion(self):
        forms = f'!(import! &self "{ROOT}/src/llm_boundary.metta")\n'
        forms += '!(assert (== (llm:observe (llm:pending turn "chat-completions" 7) (io:event 8 (Error private secret))) (llm:foreign (llm:pending turn "chat-completions" 7) (io:event 8 (Error private secret)))))\n'
        forms += '!(assert (== (llm:observe (llm:completed turn value) (io:event 7 (Error private secret))) (llm:completed turn value)))\n'
        self.run_guest(forms)

    def test_native_anthropic_tool_batch_preserves_signed_history(self):
        self.env.update(SYNTHETIC_MODEL="claude-sonnet-5-5", ANTHROPIC_API_KEY="fixture-secret",
                        ANTHROPIC_BASE_URL=self.env["SYNTHETIC_BASE_URL"])
        signed = {"type": "thinking", "thinking": "opaque", "signature": "signed"}
        self.peer.reply = {"stop_reason": "tool_use", "content": [signed,
            {"type": "tool_use", "id": "c2", "name": "nop", "input": {}},
            {"type": "tool_use", "id": "c3", "name": "nop", "input": {}}],
            "usage": {"input_tokens": 7, "output_tokens": 4}}
        payload = {"messages": [{"role": "system", "content": "fixture"},
            {"role": "assistant", "reasoning_details": [signed], "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "nop", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"}], "max_tokens": 42,
            "reasoning": {"effort": "low"}, "extra_body": {"enable_thinking": True}}
        fixture = self.directory / "request_fixture.py"
        fixture.write_text('from iter_host import Box\nimport json\ndef request(): return Box(json.loads(' +
                           repr(json.dumps(payload)) + '))\n')
        forms = f'!(import! &self "{ROOT}/src/native_llm.metta")\n!(import! &self "{fixture}")\n'
        forms += '!(let $r (llm:structured (py-call (request_fixture.request))) (println! (FINISH (py-call (iter_host.get $r "finish")))))\n'
        out = self.run_guest(forms)
        self.assertIn("(FINISH tool_calls)", out)
        self.assertEqual(len(self.peer.requests), 1)
        path, headers, body = self.peer.requests[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers["x-api-key"], "fixture-secret")
        self.assertEqual(body["output_config"]["effort"], "low")
        self.assertEqual(body["messages"][0]["content"][0], signed)
        self.assertEqual(body["messages"][1]["content"][0]["tool_use_id"], "c1")

    def test_truncated_text_is_not_executed(self):
        self.peer.reply["choices"][0]["finish_reason"] = "length"
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.assertIn("(RESULT ())", out)
        self.assertEqual(len(self.peer.requests), 1)

    def test_truncated_tool_batch_is_refused_before_the_imported_loop_can_dispatch(self):
        self.peer.delay = .01
        self.peer.reply["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": "",
            "tool_calls": [{"id": "partial", "type": "function", "function": {"name": "nop", "arguments": "{}"}}]})
        fixture = self.directory / "request_fixture.py"
        request = {"messages": [{"role": "user", "content": "input"}], "max_tokens": 42}
        fixture.write_text('from iter_host import Box\nimport json\ndef request(): return Box(json.loads(' +
                           repr(json.dumps(request)) + '))\n')
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n!(import! &self "{fixture}")\n'
            '!(catch (llm:structured (py-call (request_fixture.request))))\n')
        self.assertIn("Error", out)
        self.assertNotIn("FINISH tool_calls", out)
        self.assertEqual(len(self.peer.requests), 1)

    def test_quota_cooldown_is_shared_and_upstream_can_bypass_it(self):
        self.peer.status = 429
        forms = f'!(import! &self "{ROOT}/src/native_llm.metta")\n' + (
            '!(llm:command "fixture-model" 42 "low" "input")\n' * 2)
        self.run_guest(forms)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertIn("failed request: http-status; provider=synthetic; status=429", self.last_stderr)
        self.env["METTACLAW_LLM_POLICY"] = "upstream"
        self.run_guest(forms)
        self.assertEqual(len(self.peer.requests), 3)

    def test_configured_deadline_recovers_a_slow_request_and_preserves_failed_work(self):
        self.peer.delay = 1.6
        forms = f'!(import! &self "{ROOT}/src/native_llm.metta")\n' + (
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.env["SYNTHETIC_TIMEOUT"] = "1"
        self.env["METTACLAW_LLM_HARD_SECONDS"] = "1"
        out = self.run_guest(forms)
        self.assertIn("(RESULT ())", out)
        self.assertIn("failed request: transport-error", self.last_stderr)
        self.assertIn("deadline_ms=1000", self.last_stderr)
        health = json.loads((self.directory / "health.json").read_text())
        self.assertEqual(health["last_outcome"], "failed")
        self.assertGreater(health["obligation_since"], 0)
        self.env["SYNTHETIC_TIMEOUT"] = "3"
        self.env["METTACLAW_LLM_HARD_SECONDS"] = "3"
        out = self.run_guest(forms)
        self.assertIn("(RESULT (nop))", out)
        self.assertNotIn("failed request:", self.last_stderr)
        self.assertEqual(len(self.peer.requests), 2)
        health = json.loads((self.directory / "health.json").read_text())
        self.assertEqual(health["last_outcome"], "completed")

    def test_failure_diagnostics_do_not_print_unknown_payloads(self):
        fixture = self.directory / "diagnostic_fixture.py"
        fixture.write_text('from types import SimpleNamespace\nimport native_llm_host\n'
            'def probe():\n'
            '    native_llm_host._record_failure(SimpleNamespace(provider="fixture-secret", '
            'sent=False), "fixture-secret https://private.invalid", 0)\n'
            '    return True\n')
        self.run_guest(f'!(import! &self "{fixture}")\n!(py-call (diagnostic_fixture.probe))\n')
        self.assertIn("failed request: provider-error; provider=unknown", self.last_stderr)
        self.assertNotIn("private.invalid", self.last_stderr)

    def test_credential_scope_rejects_different_origin_and_redirects(self):
        self.env.update(CETTA_IO_AUTH_FIXTURE_URL_PREFIX=self.env["SYNTHETIC_BASE_URL"]+"/",
            CETTA_IO_AUTH_FIXTURE_HEADER="Authorization", CETTA_IO_AUTH_FIXTURE_ENV="SYNTHETIC_API_KEY",
            CETTA_IO_AUTH_FIXTURE_PREFIX="Bearer ")
        prefix = f'!(import! &self "{ROOT}/src/llm_boundary.metta")\n'
        out = self.run_guest(prefix + '!(llm:submit turn "chat-completions" "http://127.0.0.1:1/v1/chat/completions" ((http:credential "FIXTURE")) "{}" 1000 1024)\n')
        self.assertIn("llm:failed turn submit-error", out)
        self.assertEqual(self.peer.requests, [])
        out = self.run_guest(prefix + f'!(catch (io:submit (http:request "POST" "{self.env["SYNTHETIC_BASE_URL"]}/chat/completions" ((http:credential "FIXTURE")) "{{}}" 1000 1024 True)))\n')
        self.assertIn("Error", out)
        self.assertEqual(self.peer.requests, [])

    def test_credential_echo_never_enters_guest(self):
        self.peer.reply["choices"][0]["message"]["content"] = "fixture-secret"
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n'
            '!(println! (RESULT (llm:command "fixture-model" 42 "low" "input")))\n')
        self.assertIn("(RESULT ())", out)


if __name__ == "__main__":
    unittest.main()
