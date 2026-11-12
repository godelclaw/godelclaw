"""Measured usage survives retries, late results and admission-window pruning."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

CETTA = os.environ.get("CETTA_BIN")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from isolated_env import isolated_environ  # noqa: E402
import input_ack  # noqa: E402
import llm_policy  # noqa: E402
import native_ledger  # noqa: E402
import native_llm_host  # noqa: E402
import synthetic_llm  # noqa: E402
import usage_history  # noqa: E402


def result(incoming=12, cached=4, outgoing=3):
    return dict(choices=[dict(finish_reason="stop", message=dict(content="private reply sentinel"))],
                usage=dict(prompt_tokens=incoming, completion_tokens=outgoing,
                           prompt_tokens_details=dict(cached_tokens=cached)))


class UsageHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.socket = native_ledger.start(self, self.root)
        # Only this test's own settings: the invoking agent's METTACLAW_* stay out, the
        # engine locations the ledger client needs stay in.
        env = patch.dict(os.environ, {**isolated_environ(),
            "METTACLAW_INPUT_LEDGER_SOCKET": self.socket, "METTACLAW_LLM_POLICY": "managed",
            "METTACLAW_INSTANCE": "fixture-agent", "METTACLAW_ACTIVITY_CALL_SCOPE": "work-fixture",
            "METTACLAW_LLM_COMPLETION_PATH": str(self.root / "completion.json"),
        }, clear=True)
        env.start(); self.addCleanup(env.stop)
        hook = patch("strategy_host.provider_admitted", return_value=1)
        hook.start(); self.addCleanup(hook.stop)

    def call(self, model="fixture-model", provider="synthetic", purpose="turn", now=100):
        ticket = llm_policy.admit(provider, 100, now=now)
        llm_policy.describe(ticket, model=model, protocol="chat-completions", purpose=purpose)
        return ticket

    def report(self, **window):
        return usage_history.report_native(**window)

    def request(self, ticket):
        rows = [row for row in native_ledger.llm_records("requests") if row["id"] == ticket]
        return rows[0] if rows else None

    def test_distinct_calls_accumulate_and_remain_attributable(self):
        first = self.call(model="first", purpose="turn")
        second = self.call(model="second", provider="chatgpt", purpose="coding")
        llm_policy.complete(second, result(24, 8, 6))
        llm_policy.complete(first, result())
        report = self.report()
        self.assertEqual(report["totals"]["calls"], 2)
        self.assertEqual(report["totals"]["known_input_tokens"], 36)
        self.assertEqual(report["totals"]["known_cached_tokens"], 12)
        self.assertEqual(report["totals"]["known_output_tokens"], 9)
        self.assertEqual({(g["provider"], g["model"], g["purpose"]) for g in report["groups"]},
                         {("synthetic", "first", "turn"), ("chatgpt", "second", "coding")})
        self.assertIsNone(report["monetary_cost"])
        self.assertFalse(report["billing_reconciled"])

    def test_duplicate_and_concurrent_completion_never_add_usage_twice(self):
        ticket = self.call()
        with ThreadPoolExecutor(max_workers=3) as pool:
            accepted = list(pool.map(lambda _: llm_policy.complete(ticket, result()), range(3)))
        self.assertEqual(sum(accepted), 1)
        self.assertFalse(llm_policy.complete(ticket, result(900, 800, 700)))
        report = self.report()
        self.assertEqual(report["totals"]["known_input_tokens"], 12)
        self.assertEqual(report["totals"]["completed_calls"], 1)

    def test_missing_cache_counter_does_not_erase_known_input(self):
        ticket = self.call()
        response = result()
        del response["usage"]["prompt_tokens_details"]
        llm_policy.complete(ticket, response)
        totals = self.report()["totals"]
        self.assertEqual(totals["known_input_tokens"], 12)
        self.assertEqual(totals["known_output_tokens"], 3)
        self.assertEqual(totals["cached_tokens_unknown_calls"], 1)

    def test_timeout_is_unknown_but_failed_before_submission_is_zero(self):
        llm_policy.fail(self.call(), sent=True)
        llm_policy.fail(self.call(), sent=False)
        totals = self.report()["totals"]
        self.assertEqual(totals["calls"], 2)
        self.assertEqual(totals["not_submitted_calls"], 1)
        self.assertEqual(totals["input_tokens_unknown_calls"], 1)
        self.assertEqual(totals["known_input_tokens"], 0)

    def test_late_usage_is_recorded_without_committing_a_failed_turn(self):
        ticket = self.call()
        llm_policy.fail(ticket, sent=True)
        self.assertFalse(llm_policy.complete(ticket, result()))
        report = self.report()
        self.assertEqual(report["totals"]["failed_calls"], 1)
        self.assertEqual(report["totals"]["known_input_tokens"], 12)
        self.assertNotIn("completed_calls", report["totals"])

    def test_usage_survives_the_admission_window(self):
        old = self.call(now=100)
        llm_policy.complete(old, result())
        fresh = self.call(now=300000)
        llm_policy.complete(fresh, result())
        # Admission long after the first call: both calls' usage is still reported.
        self.assertEqual(self.report()["totals"]["known_input_tokens"], 24)

    def test_dead_owner_remains_unknown_in_history(self):
        with patch.object(llm_policy, "_owner", "0:0"):
            self.call()
        llm_policy.recover_interrupted()
        totals = self.report()["totals"]
        self.assertEqual(totals["unknown_calls"], 1)
        self.assertEqual(totals["input_tokens_unknown_calls"], 1)

    def test_local_denial_does_not_invent_another_provider_call(self):
        llm_policy.fail(self.call(), status=429, retry_after=10, now=100)
        with self.assertRaises(llm_policy.AdmissionDenied):
            self.call(now=101)
        self.assertEqual(self.report()["totals"]["calls"], 1)

    def test_only_numeric_usage_fields_are_retained(self):
        ticket = self.call()
        response = result()
        response["usage"].update(api_key="credential sentinel", content="private prompt sentinel")
        response["usage"]["prompt_tokens_details"]["content"] = "private nested sentinel"
        llm_policy.complete(ticket, response)
        history = json.dumps([row["detail"] for row in native_ledger.llm_events()])
        for text in ("private reply sentinel", "credential sentinel", "private prompt sentinel", "private nested sentinel"):
            self.assertNotIn(text, history)

    def test_anthropic_input_includes_cache_reads_and_writes_once(self):
        usage = dict(input_tokens=10, cache_read_input_tokens=20,
                     cache_creation_input_tokens=30, output_tokens=4)
        evidence = llm_policy.usage_evidence(usage)
        self.assertEqual(evidence["counts"], dict(input_tokens=60, cached_tokens=20, output_tokens=4))
        self.assertEqual(evidence["reported_usage"], usage)

    def test_report_separates_cache_writes_and_trigger_without_inventing_missing_counts(self):
        ticket = llm_policy.admit('anthropic', 100, now=100)
        llm_policy.describe(ticket, model='fixture-model', protocol='anthropic', purpose='turn',
                            origin=dict(trigger='heartbeat-event', turn_id='wake-7'))
        llm_policy.complete(ticket, dict(choices=[dict(finish_reason='stop')],
            usage=dict(input_tokens=10, cache_read_input_tokens=20, cache_creation_input_tokens=30, output_tokens=4)))
        llm_policy.complete(self.call(), result())
        report = self.report()
        self.assertEqual(report['totals']['known_cache_write_tokens'], 30)
        self.assertEqual(report['totals']['cache_write_tokens_unknown_calls'], 1)
        self.assertEqual({g['trigger'] for g in report['groups']}, {'heartbeat-event', 'unrecorded'})

    def test_partial_usage_retains_each_known_counter_without_a_reporting_cap(self):
        evidence = llm_policy.usage_evidence(dict(cache_read_input_tokens=20, output_tokens=4))
        self.assertEqual(evidence["counts"], dict(input_tokens=None, cached_tokens=20, output_tokens=4))
        huge = 10**15
        evidence = llm_policy.usage_evidence(dict(prompt_tokens=huge, completion_tokens=0,
                                                prompt_tokens_details=dict(cached_tokens=huge)))
        self.assertEqual(evidence["counts"], dict(input_tokens=huge, cached_tokens=huge, output_tokens=0))

    def test_bad_output_counter_is_unknown_and_cannot_strand_a_completed_response(self):
        for value in ('abc', '5_0', '+5', True, 1.5, -1, {}, []):
            with self.subTest(value=value):
                ticket = self.call()
                response = result(); response['usage']['completion_tokens'] = value
                self.assertTrue(llm_policy.complete(ticket, response))
                row = self.request(ticket)
                self.assertEqual((row['status'], row['output']), ('completed', None))

    def test_exact_usage_is_reported_beyond_the_64_bit_request_counter(self):
        ticket = self.call()
        huge = 10**30
        self.assertTrue(llm_policy.complete(ticket, result(outgoing=huge)))
        self.assertEqual(self.report()['totals']['known_output_tokens'], huge)
        row = self.request(ticket)
        self.assertEqual((row['status'], row['output']), ('completed', None))

    def test_python_transport_keeps_the_sent_model_when_selection_changes(self):
        provider = dict(name="synthetic", protocol="chat-completions",
                        base="https://example.invalid/v1", key="fixture-secret")
        def answer(*args):
            os.environ["SYNTHETIC_MODEL"] = "different-later-model"
            return result()
        with patch.object(synthetic_llm, "_request_json", side_effect=answer), \
             patch("cognitive_health.turn_started"), patch("process_supervision.provider_wait"):
            synthetic_llm._exchange(provider, "/chat/completions", dict(model="sent-model"), 100, purpose="coding")
        group = self.report()["groups"][0]
        self.assertEqual((group["model"], group["purpose"]), ("sent-model", "coding"))
        row = json.loads((self.root / "llm-usage.jsonl").read_text())
        self.assertEqual((row["model"], row["provider"], row["purpose"]),
                         ("sent-model", "synthetic", "coding"))
        self.assertTrue(row["call_id"])

    def test_python_auxiliary_usage_tally_cannot_fail_a_completed_answer(self):
        provider = dict(name='anthropic', protocol='anthropic', base='https://example.invalid/v1', key='fixture-secret')
        payload = dict(stop_reason='end_turn', content=[dict(type='text', text='(nop)')],
                       usage=dict(input_tokens=10, output_tokens='abc'))
        with patch.object(synthetic_llm, '_request_json', return_value=payload), \
             patch('cognitive_health.turn_started'), patch('process_supervision.provider_wait'):
            answer = synthetic_llm._exchange(provider, '/messages', dict(model='fixture'), 100)
        self.assertEqual(answer['choices'][0]['message']['content'], '(nop)')
        self.assertEqual([(row['status'], row['output']) for row in native_ledger.llm_records('requests')],
                         [('completed', None)])
        self.assertEqual(self.report()['totals']['output_tokens_unknown_calls'], 1)

    def test_report_writes_nothing_and_needs_a_ledger(self):
        llm_policy.complete(self.call(), result())
        kinds = ("requests", "cooldowns", "calls", "results", "events")
        before = {kind: native_ledger.llm_records(kind) for kind in kinds}
        self.report()
        self.assertEqual({kind: native_ledger.llm_records(kind) for kind in kinds}, before)
        with patch.dict(os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": ""}):
            with self.assertRaises(input_ack.LedgerUnavailable):
                self.report()

    def test_cost_projection_adds_across_time_windows(self):
        llm_policy.complete(self.call(now=100), result())
        llm_policy.complete(self.call(now=200), result(24, 8, 6))
        first = self.report(since=0, until=150)["totals"]
        second = self.report(since=150, until=300)["totals"]
        whole = self.report(since=0, until=300)["totals"]
        self.assertEqual({k: first.get(k, 0) + second.get(k, 0) for k in whole}, whole)

    @unittest.skipUnless(CETTA, "set CETTA_BIN to run the ledger's usage reader")
    def test_older_untracked_calls_do_not_poison_a_fully_recorded_new_window(self):
        """A request row without an admitted event (one migrated from a ledger that predates
        the history) counts against coverage only in a window that contains it: the ledger's
        own reader (llm-ledger:usage-of), over records built for the case."""
        entry = self.root / "probe.metta"
        entry.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/input_ledger.metta')) + ')\n'
            '!(bind! &usage (new-space))\n'
            '!(add-atom &usage (llm:event 1 "" "recording-started" 40 "{}"))\n'
            '!(add-atom &usage (llm:request "legacy" 50 10 "completed" none "fixture" "godelclaw" none "" ""))\n'
            '!(add-atom &usage (llm:request "new" 200 10 "completed" 2 "fixture" "godelclaw" none "" ""))\n'
            '!(add-atom &usage (llm:event 2 "new" "admitted" 200 "{}"))\n'
            '!(println! (WINDOW (llm-ledger:usage-of &usage 150 300)))\n'
            '!(println! (WHOLE (llm-ledger:usage-of &usage 0 300)))\n')
        run = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet", str(entry)],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        lines = [line.strip() for line in run.stdout.splitlines() if line.strip().startswith("(W")]
        self.assertEqual(lines, ['(WINDOW (usage 40 0 (("new" "admitted" "{}" 200))))',
                                 '(WHOLE (usage 40 1 (("new" "admitted" "{}" 200))))'])

if __name__ == "__main__":
    unittest.main()
