"""Recorded health reaches decisions without becoming action authority."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import runtime_context as view
import strategy_host as host
import topic_jobs


class RuntimeContextTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.now = 2000000000
        self.modes = self.root / "modes"; self.modes.mkdir()
        (self.modes / "channel-handover").mkdir()
        env = {"METTACLAW_COGNITIVE_HEALTH_PATH": str(self.root / "health.json"),
               "METTACLAW_ENGINE_STATE_PATH": str(self.root / "engine"),
               "METTACLAW_MODE_STATE_ROOT": str(self.modes),
               "METTACLAW_STRATEGY_PATH": str(self.root / "strategy.db"),
               "METTACLAW_EFFECT_RECEIPT_PATH": str(self.root / "receipts.jsonl"),
               "METTACLAW_CHROMA_DIR": str(self.root), "METTACLAW_CHROMA_COLLECTION": "memories",
               "METTACLAW_LLM_POLICY_PATH": "", "METTACLAW_LLM_POLICY": "managed",
               "METTACLAW_LLM_LEDGER_PATH": str(self.root / "budget.db"),
               "METTACLAW_LLM_REQUESTS_PER_HOUR": "0", "METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY": "0",
               "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_FAIR_GOALS": "0"}
        patch = mock.patch.dict(os.environ, env); patch.start(); self.addCleanup(patch.stop)
        self.write("health.json", dict(schema=2, obligation_since=self.now-10,
            in_flight_since=0, retry_at=0, last_outcome="completed", last_completed_at=self.now-4,
            irrelevant_secret="CANARY_PRIVATE"))
        self.write("modes/control-status.json", dict(observed_at=self.now-1, channel_ready=True))
        (self.root / "receipts.jsonl").write_text("")

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def database(self, name, script):
        with sqlite3.connect(self.root / name) as db:
            db.executescript(script)

    def memory(self):
        self.database("chroma.sqlite3", """
            CREATE TABLE collections(id TEXT,name TEXT,config_json_str TEXT);
            INSERT INTO collections VALUES('c','memories','{"hnsw":{"sync_threshold":20}}');
            CREATE TABLE segments(id TEXT,scope TEXT,collection TEXT);
            INSERT INTO segments VALUES('m','METADATA','c'),('v','VECTOR','c');
            CREATE TABLE max_seq_id(segment_id TEXT,seq_id INTEGER);
            INSERT INTO max_seq_id VALUES('m',20),('v',17);
            CREATE TABLE embeddings(segment_id TEXT);
            INSERT INTO embeddings VALUES('m'),('m');
        """)

    def background(self, legacy=False):
        column = "" if legacy else ",finished_at REAL"
        self.database("strategy.db", "CREATE TABLE model_jobs(state TEXT,observed INTEGER,adopted INTEGER" + column + ");")
        with sqlite3.connect(self.root / "strategy.db") as db:
            if legacy:
                db.execute("INSERT INTO model_jobs VALUES('returned',0,0)")
            else:
                db.executemany("INSERT INTO model_jobs VALUES(?,?,?,?)", [
                    ("returned", 0, 0, self.now-45), ("failed", 0, 0, self.now-10),
                    ("running", 0, 0, None), ("uncertain", 0, 0, None),
                    ("returned", 1, 0, self.now-90), ("returned", 0, 1, self.now-900)])

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

    def test_input_age_is_pending_processing_not_completed_answer(self):
        import input_ack
        tasks = [[str(identity), json.dumps(["input", "route", "message", role,
                  {"message": {"date": self.now-age, "text": "CANARY_PRIVATE"}}])]
                 for identity, age, role in ((1, 80, "operator"), (2, 10, "operator"), (3, 5, "bot"))]
        with mock.patch.object(input_ack.time, "time", return_value=self.now-1):
            input_ack.begin_scan(tasks)
        self.write("modes/control-status.json", dict(observed_at=self.now-1, channel_ready=True,
                   operator_inbox=input_ack.input_view()))
        facts = view.read(self.now)
        self.assertEqual(facts["humanInbox"], {"pending": 2, "newestAgeSeconds": 10, "oldestAgeSeconds": 80, "sampleAgeSeconds": 1})
        self.assertIs(facts["turnPending"], True)
        self.assertEqual(len(tasks), 3)

    def test_absent_inbox_is_not_known_empty(self):
        self.assertIsNone(view.read(self.now)["humanInbox"])

    def test_complete_empty_scan_is_distinct_from_failed_or_truncated_scan(self):
        import input_ack
        with mock.patch.object(input_ack.time, "time", return_value=self.now-1):
            input_ack.begin_scan([])
        self.assertEqual(input_ack.input_view()["pending"], 0)
        for tasks in ([["runtime-scan-unavailable", "[]"]], [["task", "not-json"]],
                      [[str(i), "[]"] for i in range(257)]):
            input_ack.begin_scan(tasks)
            self.assertIsNone(input_ack.input_view())

    def test_unfamiliar_input_format_cannot_establish_an_empty_inbox(self):
        import input_ack
        for body in (["input", "new-format"], ["future-input", {}], {}):
            input_ack.begin_scan([["task", json.dumps(body)]])
            self.assertIsNone(input_ack.input_view())

    def test_memory_watermarks_do_not_claim_missing_document_count(self):
        self.assertIsNone(view.read(self.now)["memory"])
        self.memory()
        self.assertEqual(view.read(self.now)["memory"],
                         {"documents": 2, "persistedSequenceGap": 3, "threshold": 20})
        with sqlite3.connect(self.root / "chroma.sqlite3") as db:
            db.execute("UPDATE max_seq_id SET seq_id=21 WHERE segment_id='v'")
        self.assertIsNone(view.read(self.now)["memory"])

    def test_legacy_background_results_are_not_prompt_bookkeeping(self):
        self.background(legacy=True)
        self.assertIsNone(view.read(self.now)["background"])

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
        self.memory(); self.background()
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        with mock.patch.object(host, "ledger", side_effect=AssertionError("writer opened")), \
             mock.patch("socket.socket", side_effect=AssertionError("network opened")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            facts = view.read(self.now)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(facts["memory"]["persistedSequenceGap"], 3)
        self.assertIsNone(facts["background"])
        with view._database(self.root / "strategy.db") as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM model_jobs")

    def test_missing_databases_are_not_created(self):
        facts = view.read(self.now)
        self.assertIsNone(facts["memory"])
        self.assertIsNone(facts["background"])
        self.assertFalse((self.root / "chroma.sqlite3").exists())
        self.assertFalse((self.root / "strategy.db").exists())

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
        with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(_current_batch_update_ids=set())}), \
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
