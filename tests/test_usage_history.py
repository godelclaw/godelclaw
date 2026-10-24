"""Measured usage survives retries, late results and admission-window pruning."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import llm_policy
import native_llm_host
import synthetic_llm
import usage_history

CETTA = os.environ.get("CETTA_BIN")
ROOT = Path(__file__).resolve().parents[1]


def result(incoming=12, cached=4, outgoing=3):
    return dict(choices=[dict(finish_reason="stop", message=dict(content="private reply sentinel"))],
                usage=dict(prompt_tokens=incoming, completion_tokens=outgoing,
                           prompt_tokens_details=dict(cached_tokens=cached)))


class UsageHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "ledger.db"
        env = patch.dict(os.environ, {
            "METTACLAW_LLM_LEDGER_PATH": str(self.ledger), "METTACLAW_LLM_POLICY": "managed",
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

    def report(self):
        return usage_history.report(self.ledger)

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
        with closing(sqlite3.connect(self.ledger)) as conn:
            self.assertIsNone(conn.execute("SELECT id FROM requests WHERE id=?", (old,)).fetchone())
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
        with closing(sqlite3.connect(self.ledger)) as conn:
            history = json.dumps(conn.execute("SELECT detail FROM llm_call_history").fetchall())
        for text in ("private reply sentinel", "credential sentinel", "private prompt sentinel", "private nested sentinel"):
            self.assertNotIn(text, history)

    def test_anthropic_input_includes_cache_reads_and_writes_once(self):
        usage = dict(input_tokens=10, cache_read_input_tokens=20,
                     cache_creation_input_tokens=30, output_tokens=4)
        evidence = llm_policy.usage_evidence(usage)
        self.assertEqual(evidence["counts"], dict(input_tokens=60, cached_tokens=20, output_tokens=4))
        self.assertEqual(evidence["reported_usage"], usage)

    def test_partial_usage_retains_each_known_counter_without_a_reporting_cap(self):
        evidence = llm_policy.usage_evidence(dict(cache_read_input_tokens=20, output_tokens=4))
        self.assertEqual(evidence["counts"], dict(input_tokens=None, cached_tokens=20, output_tokens=4))
        huge = 10**15
        evidence = llm_policy.usage_evidence(dict(prompt_tokens=huge, completion_tokens=0,
                                                prompt_tokens_details=dict(cached_tokens=huge)))
        self.assertEqual(evidence["counts"], dict(input_tokens=huge, cached_tokens=huge, output_tokens=0))

    def test_native_wire_records_subscription_billing_and_compaction_purpose(self):
        ticket = llm_policy.admit("chatgpt", 100, now=100)
        plan = SimpleNamespace(correlation=ticket, provider="openai", protocol="responses",
                               request_profile="chatgpt-plan", stream=False, purpose="compaction")
        native_llm_host.wire_body(plan, json.dumps(dict(model="wire-model", input="private prompt sentinel")))
        with patch.dict(os.environ, SYNTHETIC_MODEL="different-later-model"):
            llm_policy.complete(ticket, result())
        group = self.report()["groups"][0]
        self.assertEqual((group["provider"], group["model"], group["purpose"]),
                         ("chatgpt", "wire-model", "compaction"))

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

    def test_report_does_not_write_or_create_a_database(self):
        llm_policy.complete(self.call(), result())
        before = hashlib.sha256(self.ledger.read_bytes()).digest()
        self.report()
        self.assertEqual(hashlib.sha256(self.ledger.read_bytes()).digest(), before)
        missing = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            usage_history.report(missing)
        self.assertFalse(missing.exists())

    def test_legacy_ledger_reports_missing_history_instead_of_zero_spend(self):
        with closing(sqlite3.connect(self.ledger)) as conn:
            conn.execute("CREATE TABLE legacy_fixture(id TEXT)")
        report = self.report()
        self.assertFalse(report["history_available"])
        self.assertFalse(report["coverage_complete"])
        self.assertIsNone(report["monetary_cost"])
        self.assertEqual(report["totals"], {})

    def test_cost_projection_adds_across_time_windows(self):
        llm_policy.complete(self.call(now=100), result())
        llm_policy.complete(self.call(now=200), result(24, 8, 6))
        first = usage_history.report(self.ledger, since=0, until=150)["totals"]
        second = usage_history.report(self.ledger, since=150, until=300)["totals"]
        whole = usage_history.report(self.ledger, since=0, until=300)["totals"]
        self.assertEqual({k: first.get(k, 0) + second.get(k, 0) for k in whole}, whole)

    def test_older_untracked_calls_do_not_poison_a_fully_recorded_new_window(self):
        llm_policy.complete(self.call(), result())
        boundary = time.time()
        llm_policy.complete(self.call(now=boundary), result())
        with closing(sqlite3.connect(self.ledger)) as conn:
            conn.execute("INSERT INTO requests(id,started,reserved,status,provider,mode) "
                         "VALUES('legacy',50,10,'completed','fixture','godelclaw')")
            conn.commit()
        current = usage_history.report(self.ledger, since=boundary)
        self.assertEqual(current["totals"]["calls"], 1)
        self.assertEqual(current["legacy_request_rows_without_history"], 0)
        self.assertTrue(current["coverage_complete"])
        self.assertFalse(self.report()["coverage_complete"])

    @unittest.skipUnless(CETTA, "set CETTA_BIN to exercise the native MeTTa reader")
    def test_metta_reads_the_same_history_without_python_or_writes(self):
        llm_policy.complete(self.call(), result())
        before = self.ledger.read_bytes()
        entry = self.root / "probe.metta"
        entry.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/usage_history.metta')) + ')\n'
            '!(case (usage:events ' + json.dumps(str(self.ledger)) + ' 0 1000)\n'
            ' (((sqlite:result $columns $rows $changes $last) (println! (size-atom $rows)))\n'
            '  ($other (println! $other))))\n')
        env = dict(os.environ, PYTHONHOME=sys.prefix, LD_LIBRARY_PATH=str(Path(sys.prefix) / "lib"))
        run = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet", str(entry)],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        # The CLI also prints top-level import/println acknowledgements.
        # Retain every substantive result so a duplicated answer cannot hide.
        lines = [line.strip() for line in run.stdout.splitlines()
                 if line.strip() and line.strip().lower() != "true"]
        self.assertEqual(lines, ["4"])
        self.assertEqual(self.ledger.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
