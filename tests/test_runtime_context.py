"""Recorded health reaches decisions without becoming action authority."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import native_ledger
import runtime_context as view
import strategy_host as host
import topic_jobs


class RuntimeContextTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        native_ledger.start(self, self.root)
        self.now = 2000000000
        self.modes = self.root / "modes"; self.modes.mkdir()
        (self.modes / "channel-handover").mkdir()
        env = {"METTACLAW_COGNITIVE_HEALTH_PATH": str(self.root / "health.json"),
               "METTACLAW_ENGINE_STATE_PATH": str(self.root / "engine"),
               "METTACLAW_MODE_STATE_ROOT": str(self.modes),
               "METTACLAW_STRATEGY_PATH": str(self.root / "strategy"),
               "METTACLAW_EFFECT_RECEIPT_PATH": str(self.root / "receipts.jsonl"),
               "METTACLAW_CHROMA_DIR": str(self.root), "METTACLAW_CHROMA_COLLECTION": "memories",
               "METTACLAW_LLM_POLICY_PATH": "", "METTACLAW_LLM_POLICY": "managed",
               "METTACLAW_LLM_REQUESTS_PER_HOUR": "0", "METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY": "0",
               "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_FAIR_GOALS": "0"}
        patch = mock.patch.dict(os.environ, env); patch.start(); self.addCleanup(patch.stop)
        os.environ.pop("METTACLAW_LLM_LEDGER_PATH", None)
        self.write("health.json", dict(schema=2, obligation_since=self.now-10,
            in_flight_since=0, retry_at=0, last_outcome="completed", last_completed_at=self.now-4,
            irrelevant_secret="CANARY_PRIVATE"))
        self.write("modes/control-status.json", dict(observed_at=self.now-1, channel_ready=True))
        (self.root / "receipts.jsonl").write_text("")

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def test_returned_provider_preserves_unsettled_obligation(self):
        facts = view.read(self.now)
        self.assertEqual(facts["call"], {"phase": "returned", "ageSeconds": 4})
        self.assertIs(facts["turnPending"], True)

    def test_failure_keeps_obligation_and_exact_retry_window(self):
        self.write("health.json", dict(schema=2, obligation_since=self.now-100,
            in_flight_since=0, retry_at=self.now+30, last_outcome="failed",
            last_failed_at=self.now-1, last_failure_type="TimeoutError"))
        facts = view.read(self.now)
        self.assertEqual(facts["call"]["phase"], "backoff")
        self.assertEqual(facts["call"]["retrySeconds"], 30)
        self.assertEqual(facts["call"]["reason"], "timeout")
        self.assertIs(facts["turnPending"], True)
        self.assertEqual(view.read(self.now+31)["call"]["phase"], "failed")

    def test_local_preparation_failure_is_not_an_unclassified_provider_failure(self):
        self.write("health.json", dict(schema=2, obligation_since=self.now-100,
            in_flight_since=0, retry_at=self.now+15, last_outcome="failed",
            last_failed_at=self.now-1, last_failure_type="context-compaction-required"))
        facts = view.read(self.now)
        self.assertEqual(facts["call"]["reason"], "context-preparation")
        self.assertIs(facts["turnPending"], True)


    def test_received_answer_waiting_for_storage_is_not_provider_wait(self):
        self.write("health.json", dict(schema=2, obligation_since=self.now-100,
            in_flight_since=self.now-60, retry_at=0, local_commit_pending=True))
        facts = view.read(self.now)
        self.assertEqual(facts["call"], {"phase": "recording", "ageSeconds": 60})
        self.assertTrue(facts["turnPending"])

    def test_missing_stale_future_and_false_channel_are_distinct(self):
        for stamp in (self.now-31, self.now+1):
            self.write("modes/control-status.json", dict(observed_at=stamp, channel_ready=True))
            self.assertIsNone(view.read(self.now)["localChannelReady"])
        self.write("modes/control-status.json", dict(observed_at=self.now-1, channel_ready=False))
        self.assertIs(view.read(self.now)["localChannelReady"], False)
        (self.modes / "control-status.json").unlink()
        self.assertIsNone(view.read(self.now)["localChannelReady"])

    # The responder's scan produces the view natively (tests/unit/input_notice.metta);
    # these read what it publishes.
    def inbox(self, view):
        self.write("modes/control-status.json", dict(observed_at=self.now-1, channel_ready=True, operator_inbox=view))

    def test_input_age_is_pending_processing_not_completed_answer(self):
        # Two operator messages, 80 and 10 seconds old, as a complete scan reports them.
        self.inbox({"pending": 2, "newestMessageAt": self.now-10, "oldestMessageAt": self.now-80,
                    "observedAt": self.now-1})
        facts = view.read(self.now)
        self.assertEqual(facts["humanInbox"], {"pending": 2, "newestAgeSeconds": 10, "oldestAgeSeconds": 80, "sampleAgeSeconds": 1})
        self.assertIs(facts["turnPending"], True)

    def test_absent_inbox_is_not_known_empty(self):
        self.assertIsNone(view.read(self.now)["humanInbox"])

    def test_complete_empty_scan_is_distinct_from_failed_or_truncated_scan(self):
        self.inbox({"pending": 0, "newestMessageAt": None, "oldestMessageAt": None, "observedAt": self.now-1})
        self.assertEqual(view.read(self.now)["humanInbox"]["pending"], 0)
        # A failed, truncated or unfamiliar scan publishes no view.
        self.inbox(None)
        self.assertIsNone(view.read(self.now)["humanInbox"])

    def test_recent_receipt_window_and_closed_reason_labels(self):
        records = [{"command": "CANARY_PRIVATE", "result": "CANARY_PRIVATE", "disposition": "returned"}] * 20
        records += [{"commands": "CANARY_PRIVATE", "disposition": "withheld", "reason": '{"reason":"stale"}'},
                    {"commands": "CANARY_PRIVATE", "disposition": "deferred", "reason": "CANARY_PRIVATE"}]
        (self.root / "receipts.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
        counts = view.read(self.now)["commands"]
        self.assertEqual(counts, dict(returned=14, withheld=1, deferred=1, uncertain=0, failed=0,
                                     windowRecords=16, reasons=["other", "stale"]))
        self.assertNotIn("CANARY_PRIVATE", view.render(view.read(self.now)))

    def test_recorded_command_exceptions_are_failed_not_uncertain(self):
        (self.root / "receipts.jsonl").write_text(json.dumps({"command": "probe", "disposition": "exception"}) + "\n")
        counts = view.read(self.now)["commands"]
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["uncertain"], 0)

    def test_old_volume_caps_do_not_reappear_in_context(self):
        with mock.patch.dict(os.environ, {'METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY':'1'}):
            facts=view.read(self.now)
            self.assertEqual(facts['budgetStatus'],'no-volume-caps')
            self.assertNotIn('budget',facts)

    def test_reading_cannot_write_checkpoint_or_call_provider(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        with mock.patch.object(host, "ledger", side_effect=AssertionError("writer opened")), \
             mock.patch("socket.socket", side_effect=AssertionError("network opened")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            facts = view.read(self.now)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertIsNone(facts["background"])

    def test_missing_databases_are_not_created(self):
        facts = view.read(self.now)
        # With no native memory service the fact claims nothing about documents or sequence gaps.
        self.assertEqual(set(facts["memory"]), {"backend", "supported"})
        self.assertIs(facts["memory"]["supported"], False)
        self.assertIsNone(facts["background"])
        self.assertEqual(list(self.root.rglob("*.sqlite3")), [])

    def test_compact_projection_is_bounded_and_structured(self):
        packet = view.render(view.read(self.now))
        self.assertLessEqual(len(packet), view.MAX_CHARS)
        self.assertEqual(json.loads(packet.splitlines()[1].removeprefix("RUNTIME_STATE "))["turnPending"], True)
        fallback = view.render({"unknown": "x" * 10000})
        self.assertLessEqual(len(fallback), view.MAX_CHARS)
        self.assertEqual(json.loads(fallback.splitlines()[1].removeprefix("RUNTIME_STATE ")),
                         {"schema": 1, "unavailable": ["projection-overflow"]})

    def test_oversized_or_malformed_records_remain_unknown(self):
        (self.root / "health.json").write_text("{" + "x" * 70000)
        self.assertEqual(view.read(self.now)["call"], {"phase": "unknown"})
        self.assertIsNone(view.read(self.now)["turnPending"])

    def test_prepare_appends_and_capture_requires_exact_observed_packet(self):
        globals_patch = mock.patch.multiple(host, _ledger=None, _booted=False, _turn=None,
            _sample=None, _ticket=None, _focus=None, _position=0, _runtime_projection=None)
        globals_patch.start(); self.addCleanup(globals_patch.stop)
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(current_batch_update_ids=set, reply_chat_id=str)}), \
             mock.patch.object(host, "guard", return_value="control"), \
             mock.patch("lifecycle.cognition_enabled", return_value=1):
            host.begin(1, "operator-event", "retained human request")
            core_before = host.ledger().projection()
            prompt = host.prepare("after replaceable preparation")
            packet = host._runtime_projection
            self.assertEqual(prompt.count("RUNTIME_STATE "), 1)
            self.assertIn("retained human request", prompt)
            self.assertEqual(host.capture(prompt.replace(packet, ""), 60), 1)
            self.assertEqual(host.ledger().projection()["actions"], core_before["actions"])
            self.assertEqual(host.capture(prompt.replace('"localChannelReady":', '"erasedChannel":'), 60), 1)
            self.assertEqual(host.capture(prompt, 60), 1)
            self.assertEqual(host.ledger().active()[0]["status"], "active")

    def test_upstream_profile_preserves_its_existing_context(self):
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "0"}):
            self.assertEqual(host.prepare("upstream context"), "upstream context")


if __name__ == "__main__":
    unittest.main()
