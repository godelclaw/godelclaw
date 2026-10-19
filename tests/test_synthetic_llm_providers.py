"""Provider routing in synthetic_llm.chat/set_model: synthetic stays the
default path byte-for-byte; claude-* models route to the Anthropic
OpenAI-compatible endpoint with ANTHROPIC_API_KEY; a missing anthropic key
degrades to the empty action instead of raising."""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import synthetic_llm  # noqa: E402


def ok_chat(content="((rest))"):
    return {"choices": [{"message": {"content": content}}]}


class ProviderRoutingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.flag_dir = tempfile.mkdtemp(prefix="fable-flag-")
        self.flag = os.path.join(self.flag_dir, "fable-enabled")
        self.env = mock.patch.dict(os.environ, {
            "SYNTHETIC_API_KEY": "syn-key",
            "SYNTHETIC_RETRIES": "0",
            "SYNTHETIC_TIMEOUT": "5",
            "SYNTHETIC_EMPTY_BACKOFF": "0",
            "ANTHROPIC_ENABLED_FLAG": self.flag,
            "METTACLAW_MODEL_STATE_PATH": os.path.join(
                self.flag_dir, "active-model.txt"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": os.path.join(
                self.flag_dir, "cognitive-health.json"),
            "METTACLAW_PROVIDER_OBSERVATION_PATH": os.path.join(
                self.flag_dir, "provider-observations.json"),
        }, clear=False)
        self.env.start()
        synthetic_llm._refreshing_observations.clear()
        os.environ.pop("SYNTHETIC_MODEL", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    def tearDown(self):
        self.env.stop()

    def enable_flag(self):
        open(self.flag, "w").close()

    def capture_chat(self, model):
        seen = {}

        def fake_request_json(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization")
            seen["body"] = json.loads(req.data)
            return ok_chat()

        with mock.patch.object(synthetic_llm, "_request_json",
                               fake_request_json):
            out = synthetic_llm.chat(model, 6000, "medium", "hello")
        return out, seen

    def test_selected_model_window_discovery_stores_only_metadata(self):
        self.enable_flag()
        with mock.patch.object(synthetic_llm,"current_model",return_value="claude-fixture"), \
             mock.patch.object(synthetic_llm,"_provider_for",return_value={"protocol":"anthropic","key":"fixture","base":"https://example.invalid/v1"}), \
             mock.patch.object(synthetic_llm,"_request_json",return_value={"max_input_tokens":1000000,"max_tokens":128000}) as fetch:
            synthetic_llm._refresh_observation("model-window")
        self.assertEqual(fetch.call_args.args[0].full_url,"https://example.invalid/v1/models/claude-fixture")
        self.assertEqual(synthetic_llm._cached_observation("model-window")["value"],
                         {"id":"claude-fixture","context":1000000,"source":"provider-model-metadata"})

    def test_synthetic_default_path_unchanged(self):
        out, seen = self.capture_chat("syn:large:text")
        self.assertEqual(out, "((rest))")
        self.assertEqual(
            seen["url"],
            "https://api.synthetic.new/openai/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer syn-key")
        self.assertEqual(seen["body"]["model"], "syn:large:text")
        health = synthetic_llm.cognitive_health.snapshot()
        self.assertEqual(health["completed_count"], 1)
        self.assertEqual(health["pending_since"], 0)

    def test_subscription_menu_choices_require_the_native_backend_and_preserve_selection(self):
        from pathlib import Path
        policy = Path(self.flag_dir)/'chatgpt.json'
        policy.write_text(json.dumps(dict(account='fixture-registration')))
        synthetic_llm._persist_model('fixture-current-model')
        with mock.patch.dict(os.environ, {
                'METTACLAW_ACTIVE_ENGINE': 'cetta', 'METTACLAW_LLM_TRANSPORT': 'native',
                'METTACLAW_CHATGPT_POLICY_PATH': str(policy)}), \
             mock.patch.object(synthetic_llm, 'refresh_provider_observations_async'):
            ids = synthetic_llm.model_ids()
            self.assertIn('chatgpt:gpt-6.1-sol', ids)
            self.assertIn('chatgpt:gpt-6-astra', ids)
            self.assertEqual(synthetic_llm.current_model(), 'fixture-current-model')
            self.assertIsNone(synthetic_llm.model_in_observed_catalog('chatgpt:gpt-6.1-sol'))
            self.assertIn('availability unverified', synthetic_llm.model_catalog_status())
            with mock.patch.dict(os.environ, {'METTACLAW_ACTIVE_ENGINE': 'petta'}):
                self.assertNotIn('chatgpt:gpt-6.1-sol', synthetic_llm.model_ids())

    def test_claude_model_routes_to_native_messages_api(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        os.environ["SYNTHETIC_MODEL"] = "claude-fable-5"
        seen = {}

        def fake_request_json(req, timeout=None):
            seen["url"] = req.full_url
            seen["key"] = req.headers.get("X-api-key")
            seen["body"] = json.loads(req.data)
            return {"content": [{"type": "text", "text": "((rest))"}],
                    "usage": {"input_tokens": 5}}

        with mock.patch.object(synthetic_llm, "_request_json",
                               fake_request_json):
            out = synthetic_llm.chat(
                "syn:large:text", 6000, "medium",
                "PROMPT: p SKILLS: s LOOPS_LEFT: 5 HISTORY: h")
        self.assertEqual(out, "((rest))")
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(seen["key"], "anth-key")
        self.assertEqual(seen["body"]["model"], "claude-fable-5")
        s_block = seen["body"]["system"][0]
        self.assertEqual(s_block["text"], "PROMPT: p SKILLS: s")
        self.assertEqual(s_block["cache_control"], {"type": "ephemeral"})
        self.assertEqual(seen["body"]["messages"][0]["content"],
                         [{"type": "text", "text": " LOOPS_LEFT: 5 HISTORY: h"}])

    def test_anthropic_caches_the_layout_prefix_before_the_turn(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        os.environ["SYNTHETIC_MODEL"] = "claude-opus-5-5"
        seen = {}

        def fake_request_json(req, timeout=None):
            seen["body"] = json.loads(req.data)
            return {"content": [{"type": "text", "text": "((rest))"}],
                    "usage": {"input_tokens": 5}}

        with mock.patch.object(synthetic_llm, "_request_json",
                               fake_request_json):
            synthetic_llm.chat(
                "syn:large:text", 6000, "medium",
                "HEAD_cache_boundary_STABLE_cache_boundary_CONV"
                "_cache_boundary_TURN read _cache_boundary_ in a file")
        system = seen["body"]["system"]
        self.assertEqual([block["text"] for block in system],
                         ["HEAD", "STABLE", "CONV"])
        # One breakpoint on the last block: Anthropic finds earlier cached
        # prefixes at block boundaries.
        self.assertEqual([block.get("cache_control") for block in system],
                         [None, None, {"type": "ephemeral"}])
        self.assertEqual(seen["body"]["messages"],
                         [{"role": "user",
                           "content": [{"type": "text", "text": "TURN read _cache_boundary_ in a file"}]}])

    def test_anthropic_transcript_chunks_are_blocks_with_one_breakpoint(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        os.environ["SYNTHETIC_MODEL"] = "claude-opus-5-5"
        seen = {}

        def fake_request_json(req, timeout=None):
            seen["body"] = json.loads(req.data)
            return {"content": [{"type": "text", "text": "((rest))"}],
                    "usage": {"input_tokens": 5}}

        with mock.patch.object(synthetic_llm, "_request_json", fake_request_json):
            synthetic_llm.chat(
                "syn:large:text", 6000, "medium",
                "HEAD_cache_boundary_STABLE_cache_boundary_\n\nSTEPS 0-7\n_cache_chunk_\nSTEPS 8-15\n\n"
                "_cache_boundary_CONVERSATION TURN")
        system = seen["body"]["system"]
        self.assertEqual([block["text"] for block in system],
                         ["HEAD", "STABLE", "STEPS 0-7", "STEPS 8-15"])
        self.assertEqual([block.get("cache_control") for block in system],
                         [None, None, None, {"type": "ephemeral"}])
        self.assertNotIn("_cache_chunk_", json.dumps(seen["body"]))

    def test_synthetic_request_carries_no_layout_boundary(self):
        seen = {}

        def fake_request_json(req, timeout=None):
            seen["body"] = json.loads(req.data)
            return ok_chat()

        with mock.patch.object(synthetic_llm, "_request_json",
                               fake_request_json):
            synthetic_llm.chat(
                "syn:large:text", 6000, "medium",
                "A_cache_boundary_B_cache_boundary_C_cache_boundary_D")
        self.assertEqual(seen["body"]["messages"], [
            {"role": "system", "content": "ABC"},
            {"role": "user", "content": "D"}])

    def test_claude_without_key_returns_empty_action_not_raise(self):
        self.enable_flag()
        os.environ["SYNTHETIC_MODEL"] = "claude-fable-5"
        with mock.patch.object(
                synthetic_llm, "_request_json",
                side_effect=AssertionError("no network call expected")):
            out = synthetic_llm.chat("syn:large:text", 6000, "medium", "hi")
        self.assertEqual(out, "()")

    def test_native_empty_max_tokens_is_diagnosed(self):
        reason = synthetic_llm._diagnose_empty({
            "content": [],
            "stop_reason": "max_tokens",
            "usage": {"output_tokens": 6000},
        })
        self.assertIn("stop_reason=max_tokens", reason)
        self.assertIn("output_tokens=6000", reason)

    def test_one_cognitive_turn_never_hides_replacement_requests(self):
        os.environ["SYNTHETIC_RETRIES"] = "99"
        calls = []

        def unavailable(_request, timeout=None):
            calls.append(timeout)
            raise urllib.error.URLError("temporarily unavailable")

        with mock.patch.object(
                synthetic_llm, "_request_json", unavailable):
            out = synthetic_llm.chat(
                "syn:large:text", 6000, "medium", "hello")

        self.assertEqual(out, "()")
        self.assertEqual(len(calls), 1)

    def test_nonretriable_http_error_remains_inside_provider_boundary(self):
        failure = urllib.error.HTTPError(
            "https://provider.invalid/chat", 404, "model retired", {}, None)
        with mock.patch.object(
                synthetic_llm, "_request_json", side_effect=failure):
            out = synthetic_llm.chat(
                "retired-model", 6000, "medium", "hello")
        self.assertEqual(out, "()")
        health = synthetic_llm.cognitive_health.snapshot()
        self.assertEqual(health["last_failure_type"], "httperror")

    def test_set_model_claude_requires_key(self):
        self.enable_flag()
        msg = synthetic_llm.set_model("claude-fable-5")
        self.assertIn("not configured", msg)
        self.assertNotEqual(os.environ.get("SYNTHETIC_MODEL"),
                            "claude-fable-5")

    def test_set_model_claude_with_key_no_network(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        with mock.patch.object(
                synthetic_llm, "_request_json",
                side_effect=AssertionError("no network call expected")):
            msg = synthetic_llm.set_model("claude-fable-5")
        self.assertIn("anthropic", msg)
        self.assertEqual(os.environ["SYNTHETIC_MODEL"], "claude-fable-5")

    def test_set_model_accepts_opus_5_5_without_network(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        with mock.patch.dict(os.environ), \
             mock.patch.object(
                 synthetic_llm, "_request_json",
                 side_effect=AssertionError("no network call expected")):
            os.environ.pop("ANTHROPIC_MODELS", None)
            msg = synthetic_llm.set_model("claude-opus-5-5")
            self.assertIn("anthropic", msg)
            self.assertEqual(os.environ["SYNTHETIC_MODEL"], "claude-opus-5-5")
            self.assertIn("claude-opus-5-5", synthetic_llm.model_ids())

    def test_set_model_unknown_claude_rejected(self):
        self.enable_flag()
        os.environ["ANTHROPIC_API_KEY"] = "anth-key"
        msg = synthetic_llm.set_model("claude-nonexistent")
        self.assertIn("unknown anthropic model", msg)

    def test_synthetic_model_switch_never_calls_provider(self):
        with mock.patch.object(
                synthetic_llm, "_get_json",
                side_effect=AssertionError("no provider call expected")), \
             mock.patch.object(
                 synthetic_llm, "refresh_provider_observations_async"):
            msg = synthetic_llm.set_model("hf:zai-org/GLM-5.2")
        self.assertIn("persists across restarts", msg)
        self.assertEqual(os.environ["SYNTHETIC_MODEL"],
                         "hf:zai-org/GLM-5.2")

    def test_model_menu_reads_durable_observation_while_refresh_blocks(self):
        synthetic_llm._store_observation("models", [
            {"id": "provider-model", "context": 12345},
        ], now=1)
        entered = threading.Event()
        release = threading.Event()

        def blocked(_url):
            entered.set()
            release.wait(2)
            return {"error": "blocked-test"}

        with mock.patch.object(synthetic_llm, "_get_json", blocked):
            started = time.monotonic()
            ids = synthetic_llm.model_ids()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.1)
            self.assertIn("provider-model", ids)
            self.assertIn("hf:zai-org/GLM-5.2", ids)
            self.assertTrue(entered.wait(1))
            # A second control remains local while the one refresh is stalled.
            started = time.monotonic()
            self.assertIn("provider-model", synthetic_llm.model_ids())
            self.assertLess(time.monotonic() - started, 0.1)
            release.set()
        deadline = time.monotonic() + 1
        while synthetic_llm._refreshing_observations and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(synthetic_llm._refreshing_observations)

    def test_quota_reads_cache_without_waiting_for_refresh(self):
        synthetic_llm._store_observation("quota", {
            "weeklyTokenLimit": {
                "remainingCredits": 7,
                "maxCredits": 10,
                "percentRemaining": 70,
            },
        }, now=time.time())
        with mock.patch.object(
                synthetic_llm, "_get_json",
                side_effect=AssertionError("fresh cache must not query")):
            started = time.monotonic()
            reply = synthetic_llm.quota()
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertIn("synthetic_weekly=7 of 10 (70.0%)", reply)

    def test_synthetic_content_blocks_are_not_tallied_as_anthropic(self):
        payload = {
            "content": [{"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
        with mock.patch.object(synthetic_llm, "_tally_anthropic") as tally:
            self.assertEqual(
                synthetic_llm._extract_content(payload, "synthetic"),
                "answer",
            )
        tally.assert_not_called()


    def test_model_choice_persists_across_restart(self):
        import importlib, tempfile, os as _os
        state = _os.path.join(self.flag_dir, "active-model.txt")
        _os.environ["METTACLAW_MODEL_STATE_PATH"] = state
        try:
            _os.environ["ANTHROPIC_API_KEY"] = "anth-key"
            self.enable_flag() if hasattr(self, "enable_flag") else None
            synthetic_llm.set_model("claude-fable-5")
            with open(state, encoding="utf-8") as handle:
                self.assertIn("(active-model claude-fable-5)", handle.read())
            # simulate a restart: boot env sets the config default, then the
            # module import restores the persisted choice
            _os.environ["SYNTHETIC_MODEL"] = "syn:large:text"
            importlib.reload(synthetic_llm)
            self.assertEqual(_os.environ["SYNTHETIC_MODEL"], "claude-fable-5")
        finally:
            _os.environ.pop("METTACLAW_MODEL_STATE_PATH", None)
            importlib.reload(synthetic_llm)


class FakeHTTPResponse:
    def __init__(self, payload=b"{}", status=200, reason="OK",
                 headers=None, read_error=None):
        self.payload = payload
        self.status = status
        self.reason = reason
        self.headers = {} if headers is None else headers
        self.read_error = read_error

    def read(self):
        if self.read_error is not None:
            raise self.read_error
        return self.payload


class FakeConnection:
    def __init__(self, response=None, request_error=None,
                 response_error=None):
        self.response = response or FakeHTTPResponse()
        self.request_error = request_error
        self.response_error = response_error
        self.close_count = 0
        self.request_args = None

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        if self.response_error is not None:
            raise self.response_error
        return self.response

    def close(self):
        self.close_count += 1


class ProviderResourceLifetimeTest(unittest.TestCase):
    def request(self):
        return synthetic_llm.urllib.request.Request(
            "https://provider.invalid/v1/chat?trace=yes",
            b'{"hello":"world"}',
            {"Authorization": "Bearer test"},
        )

    def run_with(self, connection):
        with mock.patch.object(
                synthetic_llm, "_open_connection",
                return_value=connection):
            return synthetic_llm._request_json(self.request(), 1.0)

    def test_success_closes_connection_exactly_once(self):
        connection = FakeConnection(FakeHTTPResponse(b'{"ok":true}'))
        self.assertEqual(self.run_with(connection), {"ok": True})
        self.assertEqual(connection.close_count, 1)
        args, kwargs = connection.request_args
        self.assertEqual(args[:2], ("POST", "/v1/chat?trace=yes"))
        self.assertEqual(kwargs["body"], b'{"hello":"world"}')

    def test_request_timeout_closes_connection_exactly_once(self):
        connection = FakeConnection(request_error=TimeoutError("connect"))
        with self.assertRaises(TimeoutError):
            self.run_with(connection)
        self.assertEqual(connection.close_count, 1)

    def test_response_timeout_closes_connection_exactly_once(self):
        connection = FakeConnection(response_error=TimeoutError("headers"))
        with self.assertRaises(TimeoutError):
            self.run_with(connection)
        self.assertEqual(connection.close_count, 1)

    def test_read_timeout_closes_connection_exactly_once(self):
        response = FakeHTTPResponse(read_error=TimeoutError("body"))
        connection = FakeConnection(response)
        with self.assertRaises(TimeoutError):
            self.run_with(connection)
        self.assertEqual(connection.close_count, 1)

    def test_http_error_closes_connection_exactly_once(self):
        connection = FakeConnection(FakeHTTPResponse(
            b'{"error":"busy"}', status=429, reason="Too Many Requests"))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.run_with(connection)
        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(connection.close_count, 1)

    def test_decode_error_closes_connection_exactly_once(self):
        connection = FakeConnection(FakeHTTPResponse(b"not-json"))
        with self.assertRaises(json.JSONDecodeError):
            self.run_with(connection)
        self.assertEqual(connection.close_count, 1)

    def test_retained_real_timeouts_do_not_retain_descriptors(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("descriptor census requires procfs")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        class SlowHeaders(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                if length:
                    self.rfile.read(length)
                time.sleep(0.05)

            def log_message(self, *_args):
                pass

        server = Server(("127.0.0.1", 0), SlowHeaders)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        baseline = len(os.listdir("/proc/self/fd"))
        retained = []
        request = synthetic_llm.urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/chat",
            b"{}",
            {"Content-Type": "application/json"},
        )
        try:
            for _ in range(32):
                try:
                    synthetic_llm._request_json(request, 0.01)
                except TimeoutError as exc:
                    # Keep every traceback alive. Descriptor safety must come
                    # from the explicit finalizer, never eventual GC.
                    retained.append(exc)
            time.sleep(0.1)
            after = len(os.listdir("/proc/self/fd"))
            self.assertEqual(len(retained), 32)
            self.assertLessEqual(after, baseline)
        finally:
            server.shutdown()
            server.server_close()


class CompletionUsageLogTest(unittest.TestCase):
    def test_usage_history_appends_counts_and_preserves_unknown_without_messages(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'completion.json'
            result = dict(choices=[dict(finish_reason='stop',
                                      message=dict(content='private reply sentinel'))],
                          transport='native', provider_stop='completed',
                          usage=dict(input_tokens=12000,
                                     input_tokens_details=dict(cached_tokens=10240),
                                     output_tokens=40))
            with mock.patch.dict(os.environ, {'METTACLAW_LLM_COMPLETION_PATH': str(path)}):
                synthetic_llm._completion_metadata(result)
                result['usage'] = {}
                synthetic_llm._completion_metadata(result)
            log = path.with_name('llm-usage.jsonl')
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual((rows[0]['input_tokens'], rows[0]['cached_tokens'], rows[0]['output_tokens']),
                             (12000, 10240, 40))
            self.assertAlmostEqual(rows[0]['fraction'], 10240/12000)
            self.assertEqual(rows[1]['status'], 'unknown')
            self.assertNotIn('cached_tokens', rows[1])
            self.assertNotIn('private reply sentinel', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
