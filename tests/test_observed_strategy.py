"""Durable transition replays at the checked managed-host boundary."""

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger, digest, search_observation
import admission_frontier


ROUTER = {"advertised": ["tool"], "executable": ["tool"], "permitted": ["tool"]}


def vector(ledger):
    state = ledger.projection()
    meta, actions = state["meta"], state["actions"]
    return [sum(r["status"] == "active" for r in state["requests"]),
            len(actions), sum(r["receipt"] is not None for r in actions),
            sum(r["observed"] for r in actions), meta["next_sample"],
            meta["allowance"], meta["revision"], int(meta["pending"] is not None),
            sum(r["status"] in ("issued", "uncertain") for r in actions),
            int(not any(r["status"] in ("issued", "uncertain") or
                        (r["receipt"] is not None and not r["observed"]) for r in actions))]


class ObservedStrategyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Ledger(Path(self.directory.name) / "state.sqlite3")
        self.store.policy(allowance=10)
        self.store.accept("task", "requested work")

    def capture(self, now=0, router=None):
        return self.store.capture("task", "prepared", router or ROUTER, now)

    def command(self, serial=0, owner=None, request="task", tool="tool"):
        return self.store.decide(serial, {"kind": "command", "request": request,
            "owner": owner or self.store.projection()["meta"]["owner"],
            "tool": tool, "command": [tool]}, 0)

    def returned(self, domain="success"):
        self.capture()
        ticket = self.command()
        self.assertTrue(self.store.report(ticket, "result", domain=domain))
        return ticket

    def observe(self, ticket):
        result = self.store.protected("task")["results"][0]
        self.store.observe([ticket], {ticket: [result]})

    def receipt(self, name):
        target = os.environ.get("METTACLAW_STRATEGY_TEST_RECEIPT", "")
        if target:
            with open(target, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"name": name, "vector": vector(self.store)}) + "\n")

    def test_01_preparation_does_not_grant_authority(self):
        router = {**ROUTER, "advertised": ["invented"]}
        self.capture(router=router)
        self.assertEqual(self.command(tool="invented"), "denied")
        self.assertEqual(self.store.projection()["requests"][0]["status"], "active")
        self.receipt("authority")

    def test_02_controls_preserve_pending_work_and_action(self):
        self.capture(); ticket = self.command()
        self.store.control(paused=True)
        self.store.control(mode="omega")
        self.store.compact(0); self.store.restore()
        self.assertEqual(self.store.projection()["actions"][0]["ticket"], ticket)
        self.assertIsNone(self.capture())
        self.receipt("controls")

    def test_03_new_input_invalidates_old_reply(self):
        self.capture(); self.store.accept("steering", "new instruction")
        self.assertEqual(self.command(), "stale")
        self.receipt("steering")

    def test_04_mode_switch_invalidates_reply(self):
        self.capture(); self.store.control(mode="omega")
        self.assertEqual(self.command(), "stale")
        self.receipt("mode")

    def test_05_owner_and_serial_are_correlated(self):
        self.capture()
        self.assertEqual(self.command(serial=99), "ignored")
        self.assertEqual(self.command(owner="another-owner"), "denied")
        self.receipt("correlation")

    def test_06_duplicate_completion_cannot_issue_twice(self):
        self.capture(); ticket = self.command()
        self.assertEqual(self.command(), "ignored")
        self.assertEqual(len(self.store.projection()["actions"]), 1)
        self.receipt("duplicate")

    def test_07_effect_requires_correlated_observation(self):
        ticket = self.returned()
        self.assertFalse(admission_frontier.may_issue(self.store.projection()["actions"], ["tool"]))
        self.store.observe([ticket], {ticket: [{"ticket": ticket, "payload_digest": "wrong"}]})
        self.assertEqual(self.store.projection()["actions"][0]["observed"], 0)
        self.assertFalse(admission_frontier.may_issue(self.store.projection()["actions"], ["tool"]))
        self.observe(ticket)
        self.assertIsNotNone(self.capture(now=1))
        self.receipt("observation")

    def test_08_completion_requires_task_checker(self):
        ticket = self.returned(); self.observe(ticket)
        self.assertFalse(self.store.finish("task", ticket, {"operator": lambda *a: False}))
        self.assertTrue(self.store.finish("task", ticket, {"operator": lambda *a: True}))
        self.receipt("finish")

    def test_09_failure_spends_once_and_sets_positive_retry(self):
        self.capture()
        self.assertEqual(self.store.decide(0, {"kind": "failure"}, 0, retry_after=0), "failure")
        self.assertIsNone(self.capture())
        self.assertIsNotNone(self.capture(now=1))
        self.assertEqual(self.store.projection()["meta"]["allowance"], 8)
        self.receipt("failure")

    def test_10_budget_and_rest_keep_work(self):
        self.store.policy(allowance=0, cooldown=1800)
        self.assertIsNone(self.capture(now=1800))
        self.assertFalse(self.store.cancel("task"))
        self.receipt("rest")

    def test_11_wake_is_strict_and_does_not_complete(self):
        self.capture()
        self.assertEqual(self.store.wake(600), 0)
        self.assertEqual(self.store.wake(601), 2)
        self.assertEqual(self.store.projection()["requests"][0]["status"], "active")
        self.receipt("wake")

    def test_12_empty_search_requires_exact_finished_coverage(self):
        good = {"scope": "tree", "revision": "r2", "hits": [],
                "finished": True, "complete_coverage": True}
        self.assertEqual(search_observation("tree", "r2", good), "absent")
        for change in ({"finished": False}, {"complete_coverage": False}, {"revision": "r1"}):
            self.assertEqual(search_observation("tree", "r2", {**good, **change}), "unavailable")
        self.receipt("search")

    def test_13_late_result_survives_restart_and_mode_change(self):
        self.capture(); ticket = self.command()
        self.store.control(mode="omega"); self.store.restore()
        recovered = Ledger(self.store.path)
        self.assertTrue(recovered.report(ticket, "late", domain="success"))
        self.assertFalse(recovered.report(ticket, "duplicate", domain="success"))
        self.receipt("late")
        snapshot = self.capture()
        self.assertIsNotNone(snapshot)
        self.assertEqual(self.command(serial=snapshot["serial"]), "blocked-effects")
        self.assertEqual(recovered.projection()["actions"][0]["receipt"],
                         self.store.projection()["actions"][0]["receipt"])

    def test_thinking_continues_but_effect_waits_for_exact_observation(self):
        ticket = self.returned()
        snapshot = self.capture()
        self.assertIsNotNone(snapshot)
        self.assertEqual(self.command(serial=snapshot["serial"]), "blocked-effects")
        self.assertEqual(self.store.projection()["actions"][0]["observed"], 0)
        self.observe(ticket)
        snapshot = self.capture()
        self.assertNotEqual(self.command(serial=snapshot["serial"]), "blocked-effects")

    def test_receipts_and_obligations_survive_reopening(self):
        ticket = self.returned(domain="unknown")
        self.observe(ticket); self.store.compact(0)
        reopened = Ledger(self.store.path)
        self.assertEqual(vector(reopened), vector(self.store))
        self.assertEqual(reopened.projection()["requests"][0]["status"], "active")
        self.assertFalse(reopened.finish("task", ticket, {"operator": lambda *a: True}))

    def test_six_broken_alternatives_are_distinguished(self):
        source = Path(sys.modules[Ledger.__module__].__file__).read_text()
        mutations = [
            ([( 'snap["revision"] == self.get(db, "revision")', 'True')],
             'test_03_new_input_invalidates_old_reply'),
            ([( 'elif not admission_frontier.may_issue(', 'elif False and not admission_frontier.may_issue(')],
             'test_thinking_continues_but_effect_waits_for_exact_observation'),
            ([( 'router["executable"]', 'router["advertised"]'),
              ( 'router["permitted"]', 'router["advertised"]')],
             'test_01_preparation_does_not_grant_authority'),
            ([( 'self.event(db, "compact", keep=max(0, int(keep)))',
                'db.execute("UPDATE requests SET status=\'inactive\'")')],
             'test_receipts_and_obligations_survive_reopening'),
            ([( 'return "absent" if report.get("complete_coverage") else "unavailable"',
                'return "absent"')], 'test_12_empty_search_requires_exact_finished_coverage'),
            ([( 'or receipt["domain"] != "success"', 'or False')],
             'test_receipts_and_obligations_survive_reopening'),
        ]
        for changes, target in mutations:
            with self.subTest(mutant=target):
                changed = source
                for before, after in changes:
                    self.assertIn(before, changed)
                    changed = changed.replace(before, after)
                module = types.ModuleType("mutant_strategy")
                module.__file__ = sys.modules[Ledger.__module__].__file__
                exec(compile(changed, "mutant_strategy", "exec"), module.__dict__)
                result = unittest.TestResult()
                with mock.patch.dict(globals(), {"Ledger": module.Ledger,
                                                "search_observation": module.search_observation}):
                    ObservedStrategyTest(target).run(result)
                self.assertFalse(result.errors, result.errors)
                self.assertEqual(len(result.failures), 1, result.failures)


if __name__ == "__main__":
    unittest.main()
