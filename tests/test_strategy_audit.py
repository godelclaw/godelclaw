"""Recorded safety checks must remain read-only and detect missing evidence."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts import audit_strategy
from observed_strategy import Ledger

ROUTER = {"advertised": ["tool", "advertised-only"],
          "executable": ["tool"], "permitted": ["tool"]}


def example(path):
    ledger = Ledger(path)
    ledger.accept("work", "private-request-text")
    for _ in range(2):
        snap = ledger.capture("work", "private-prompt", ROUTER, 0)
        ticket = ledger.decide(snap["serial"], {"kind": "command", "request": "work",
            "owner": snap["owner"], "tool": "tool",
            "command": ["tool", "private-command-argument"]}, 0)
        with ledger.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT, position INTEGER, command TEXT, result TEXT, PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES (?,?,?,?)",
                       (ticket, 0, json.dumps(["tool", "private-command-argument"]), '"private-result"'))
            ledger.event(db, "command-reserved", ticket=ticket, position=0)
            ledger.event(db, "command-result", ticket=ticket, position=0)
        ledger.report(ticket, "private-result")
        results = ledger.protected("work")["results"]
        ledger.observe([ticket], {ticket: results})
    return audit_strategy.snapshot(path)


def broken_views(view):
    """Deliberate semantic failures, preserving unrelated snapshot fields."""
    cases = {}

    missing = copy.deepcopy(view)
    first = missing["actions"][0]["ticket"]
    missing["events"] = [r for r in missing["events"] if not (
        json.loads(r["event"])["kind"] == "observe" and json.loads(r["event"])["ticket"] == first)]
    missing["actions"][0]["observed"] = 0
    cases["capture-without-observation"] = missing

    stale = copy.deepcopy(view)
    index = next(i for i, r in enumerate(stale["events"]) if json.loads(r["event"])["kind"] == "reply")
    stale["events"].insert(index, {"sequence": 0, "event": json.dumps({"kind": "control", "mode": "omega", "paused": False})})
    for i, row in enumerate(stale["events"]):
        row["sequence"] = i + 1
    cases["stale-decision"] = stale

    duplicate = copy.deepcopy(view)
    index = next(i for i, r in enumerate(duplicate["events"]) if json.loads(r["event"])["kind"] == "reply")
    duplicate["events"].insert(index + 1, copy.deepcopy(duplicate["events"][index]))
    for i, row in enumerate(duplicate["events"]):
        row["sequence"] = i + 1
    cases["duplicate-issue"] = duplicate

    reversed_result = copy.deepcopy(view)
    index = next(i for i, r in enumerate(reversed_result["events"]) if json.loads(r["event"])["kind"] == "command-reserved")
    left, right = reversed_result["events"][index:index + 2]
    left["event"], right["event"] = right["event"], left["event"]
    cases["result-without-reservation"] = reversed_result

    finish = copy.deepcopy(view)
    finish["events"].append({"sequence": finish["events"][-1]["sequence"] + 1,
        "event": json.dumps({"kind": "finish", "request": "work", "ticket": first})})
    finish["requests"][0]["status"] = "achieved"
    cases["unchecked-completion"] = finish

    lost = copy.deepcopy(view)
    lost["requests"][0]["status"] = "cancelled"
    cases["lost-request"] = lost
    return cases


class StrategyAuditTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.view = example(self.path)

    def test_snapshot_does_not_mutate_database(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns, self.path.stat().st_mode
        self.assertEqual(audit_strategy.snapshot(self.path), self.view)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns, self.path.stat().st_mode))

    def test_missing_database_is_not_created(self):
        missing = self.path.with_name("missing.sqlite3")
        with self.assertRaises(Exception):
            audit_strategy.snapshot(missing)
        self.assertFalse(missing.exists())

    def test_certificate_excludes_private_payloads(self):
        terms, expected, summary = audit_strategy.export_trace(self.view)
        public_projection = json.dumps([terms, expected, summary])
        for private in ("private-request-text", "private-prompt", "private-command-argument", "private-result"):
            self.assertNotIn(private, public_projection)
        self.assertEqual(summary["actions"], 2)

    def test_missing_receipt_event_fails(self):
        self.view["events"] = [r for r in self.view["events"] if json.loads(r["event"])["kind"] != "report"]
        with self.assertRaisesRegex(ValueError, "action differs"):
            audit_strategy.export_trace(self.view)

    def test_unmodeled_event_is_not_silently_skipped(self):
        self.view["events"][-1]["event"] = '{"kind":"unexpected-host-transition"}'
        with self.assertRaisesRegex(ValueError, "unmodeled event"):
            audit_strategy.export_trace(self.view)

    def test_foreign_ticket_fails(self):
        self.view["meta"] = [dict(r, value='"another-owner"') if r["key"] == "owner" else r for r in self.view["meta"]]
        with self.assertRaisesRegex(ValueError, "owner or serial mismatch"):
            audit_strategy.export_trace(self.view)

    def test_advertisement_does_not_authorize_command(self):
        self.view["actions"][0]["tool"] = "advertised-only"
        self.view["actions"][0]["command"] = '["advertised-only"]'
        with self.assertRaisesRegex(ValueError, "captured executable permission"):
            audit_strategy.export_trace(self.view)

    def test_all_semantic_controls_reach_lean_monitor(self):
        # These controls pass row normalization, so the Lean transition or
        # final-state comparison must reject them, rather than a schema check.
        for name, view in broken_views(self.view).items():
            with self.subTest(control=name):
                terms, expected, _ = audit_strategy.export_trace(view)
                self.assertTrue(terms)
                self.assertTrue(expected)


if __name__ == "__main__":
    unittest.main()
