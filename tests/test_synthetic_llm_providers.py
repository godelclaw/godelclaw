"""Provider routing and model controls in synthetic_llm: credentials and
endpoints per provider, model switching and persistence, the durable provider
observations, and failure pacing."""
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
import native_ledger  # noqa: E402


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
        import shutil
        self.addCleanup(shutil.rmtree, self.flag_dir, True)
        native_ledger.start(self, self.flag_dir)
        synthetic_llm._refreshing_observations.clear()
        os.environ.pop("SYNTHETIC_MODEL", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    def tearDown(self):
        self.env.stop()

    def enable_flag(self):
        open(self.flag, "w").close()

    def test_selected_model_window_discovery_stores_only_metadata(self):
        self.enable_flag()
        with mock.patch.object(synthetic_llm,"current_model",return_value="claude-fixture"), \
             mock.patch.object(synthetic_llm,"_provider_for",return_value={"protocol":"anthropic","key":"fixture","base":"https://example.invalid/v1"}), \
             mock.patch.object(synthetic_llm,"_request_json",return_value={"max_input_tokens":1000000,"max_tokens":128000}) as fetch:
            synthetic_llm._refresh_observation("model-window")
        self.assertEqual(fetch.call_args.args[0].full_url,"https://example.invalid/v1/models/claude-fixture")
        self.assertEqual(synthetic_llm._cached_observation("model-window")["value"],
                         {"id":"claude-fixture","context":1000000,"source":"provider-model-metadata"})

    def test_chatgpt_windows_are_read_natively_once_per_interval(self):
        # The native runtime reads the ChatGPT model list; the host only asks
        # for it when due and never starts a refresh of its own for it.
        model = "chatgpt:fixture"
        with mock.patch.object(synthetic_llm, "current_model", return_value=model), \
             mock.patch.object(synthetic_llm.threading, "Thread") as thread:
            self.assertEqual(synthetic_llm.refresh_provider_observations_async(("model-window",)), ())
            thread.assert_not_called()
            self.assertEqual(synthetic_llm.model_window_due(), model)
            self.assertEqual(synthetic_llm.observe_model_window(model, 872000, "chatgpt-models"), 1)
            self.assertEqual(synthetic_llm._cached_observation("model-window")["value"],
                             {"id": model, "context": 872000, "source": "chatgpt-models"})
            self.assertEqual(synthetic_llm.model_window_due(), "")
            later = time.time() + synthetic_llm._OBSERVATION_TTLS["model-window"] + 1
            with mock.patch.object(synthetic_llm.time, "time", return_value=later):
                self.assertEqual(synthetic_llm.model_window_due(), model)
        # A failed read keeps the last window and waits a full interval for this model.
        other = "chatgpt:other"
        with mock.patch.object(synthetic_llm, "current_model", return_value=other):
            self.assertEqual(synthetic_llm.model_window_due(), other)
            self.assertEqual(synthetic_llm.observe_model_window(other, 0, "(model-list-invalid)"), 0)
            self.assertEqual(synthetic_llm.model_window_due(), "")
            self.assertEqual(synthetic_llm._cached_observation("model-window")["value"]["id"], model)
        with mock.patch.object(synthetic_llm, "current_model", return_value="claude-fixture"):
            self.assertEqual(synthetic_llm.model_window_due(), "")
        before = synthetic_llm._cached_observation("model-window")
        self.assertEqual(synthetic_llm.observe_model_window("", 0, "chatgpt-models"), 0)
        self.assertEqual(synthetic_llm.observe_model_window("claude-fixture", 900000, "chatgpt-models"), 0)
        self.assertEqual(synthetic_llm._cached_observation("model-window"), before)

    def test_kimi_has_distinct_credentials_and_does_not_change_openai(self):
        with mock.patch.dict(os.environ, {
                "KIMI_API_KEY": "kimi-fixture",
                "KIMI_BASE_URL": "https://api.kimi.ai/coding/v1",
                "OPENAI_API_KEY": "openai-fixture"}, clear=False):
            kimi = synthetic_llm._provider_for("kimi:k3.1-pro")
            openai = synthetic_llm._provider_for("openai:gpt-fixture")
        self.assertEqual(kimi, {
            "name": "kimi", "protocol": "responses", "model": "k3.1-pro",
            "base": "https://api.kimi.ai/coding/v1", "key": "kimi-fixture"})
        self.assertEqual(openai["name"], "openai")
        self.assertEqual(openai["key"], "openai-fixture")

    def test_kimi_catalog_uses_its_own_endpoint_key_and_namespace(self):
        with mock.patch.dict(os.environ, {
                "KIMI_API_KEY": "kimi-fixture",
                "KIMI_BASE_URL": "https://kimi.example.invalid/v1"}), \
             mock.patch.object(synthetic_llm, "_request_json", return_value={
                 "data": [{"id": "k3.1-pro", "context_length": 100000}]}) as fetch:
            synthetic_llm._refresh_observation("kimi-models")
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, "https://kimi.example.invalid/v1/models")
        self.assertEqual(request.headers["Authorization"], "Bearer kimi-fixture")
        self.assertEqual(request.headers["User-agent"], "godelclaw/1.0")
        self.assertEqual(synthetic_llm._cached_observation("kimi-models")["value"],
                         [{"id": "kimi:k3.1-pro", "context": 100000}])
        self.assertEqual(synthetic_llm._cached_observation("models"), {})

    def test_model_menu_keeps_kimi_and_synthetic_catalogs_distinct(self):
        synthetic_llm._store_observation("models", [{"id": "hf:moonshotai/Kimi-K3.1-Pro"}])
        with mock.patch.object(synthetic_llm, "refresh_provider_observations_async"), \
             mock.patch.dict(os.environ, {"KIMI_API_KEY": "kimi-fixture"}):
            self.assertIsNone(synthetic_llm.model_in_observed_catalog("kimi:k3.1-pro"))
            reply = synthetic_llm.set_model("kimi:k3.1-pro")
            self.assertIn("Kimi Code API", reply)
            self.assertNotIn("absent from", reply)
            synthetic_llm._store_observation("kimi-models", [{"id": "kimi:k3.1-pro"}])
            self.assertTrue(synthetic_llm.model_in_observed_catalog("kimi:k3.1-pro"))
            self.assertFalse(synthetic_llm.model_in_observed_catalog("kimi:unknown"))
            self.assertIsNone(synthetic_llm.model_in_observed_catalog("openai:unknown"))
            self.assertIn("kimi:k3.1-pro", synthetic_llm.model_ids())
            self.assertIn("hf:moonshotai/Kimi-K3.1-Pro", synthetic_llm.model_ids())
            self.assertIn("Kimi Code catalog", synthetic_llm.model_catalog_status())

    def test_kimi_selection_identifies_a_missing_credential(self):
        with mock.patch.dict(os.environ, {"KIMI_API_KEY": ""}), \
             mock.patch.object(synthetic_llm, "refresh_provider_observations_async"):
            reply = synthetic_llm.set_model("kimi:k3.1-pro")
            self.assertIn("Kimi Code credential is not configured", reply)
            self.assertNotIn("absent from", reply)

    def test_subscription_menu_choices_preserve_selection(self):
        from pathlib import Path
        policy = Path(self.flag_dir)/'chatgpt.json'
        policy.write_text(json.dumps(dict(account='fixture-registration')))
        synthetic_llm._persist_model('fixture-current-model')
        with mock.patch.dict(os.environ, {
                'METTACLAW_CHATGPT_POLICY_PATH': str(policy)}), \
             mock.patch.object(synthetic_llm, 'refresh_provider_observations_async'):
            ids = synthetic_llm.model_ids()
            self.assertIn('chatgpt:gpt-6.1-sol', ids)
            self.assertIn('chatgpt:gpt-6-astra', ids)
            self.assertEqual(synthetic_llm.current_model(), 'fixture-current-model')
            self.assertIsNone(synthetic_llm.model_in_observed_catalog('chatgpt:gpt-6.1-sol'))
            self.assertIn('availability unverified', synthetic_llm.model_catalog_status())

    def test_unavailable_route_keeps_its_reason_in_the_failure_code(self):
        import admission_frontier
        self.assertEqual(admission_frontier.failure_code(
            synthetic_llm.ProviderUnavailable(["JsonString", "account_deactivated"])),
            "provider-unavailable:account_deactivated")
        self.assertEqual(admission_frontier.failure_code(
            synthetic_llm.ProviderUnavailable("native-auth-not-built")),
            "provider-unavailable:native-auth-not-built")
        self.assertEqual(admission_frontier.failure_code(
            synthetic_llm.ProviderUnavailable('{"body": "x y"}')),
            'provider-unavailable:--body-:--x-y--')
        self.assertEqual(admission_frontier.failure_code(TimeoutError()), "TimeoutError")

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
            # Only the observed catalog and the selected model: no built-in names.
            selected = synthetic_llm.current_model()
            self.assertEqual([i for i in ids if not i.startswith(("chatgpt:", "claude"))],
                             ["provider-model"] + ([selected] if selected != "provider-model" else []))
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
    def test_new_quota_schema_and_selected_subscription_remain_distinct(self):
        payload = dict(planWeeklyLimit=dict(percentRemaining=54.25,
                       nextRegenAt="2026-10-24T14:00:00Z", nextRegenPercent=2),
                       private="private quota sentinel")
        value = synthetic_llm._sanitize_quota(payload)
        self.assertEqual(value, dict(planWeeklyLimit=payload["planWeeklyLimit"]))
        with mock.patch.object(synthetic_llm, "current_model", return_value="chatgpt:fixture-model"), \
             mock.patch.object(synthetic_llm, "_cached_observation", return_value=dict(value=value)), \
             mock.patch.object(synthetic_llm, "refresh_provider_observations_async"):
            shown = synthetic_llm.quota()
        self.assertIn("ChatGPT subscription", shown)
        self.assertIn("synthetic_weekly=54.2% remaining", shown)
        self.assertNotIn("private quota sentinel", shown)

if __name__ == "__main__":
    unittest.main(verbosity=2)
