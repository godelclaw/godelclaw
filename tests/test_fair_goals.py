"""Matched model traces, durable charges and real provider-admission hooks."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fair_goals as fair
from observed_strategy import Ledger, digest
import strategy_host as host
import llm_policy

ROUTER = dict(advertised=["help"], executable=["help"], permitted=["help"])


def definition(name, share=1, interval=None, urgency=0, checker="operator"):
    return dict(name=name, area="Research", note="Inspect evidence before acting", blocked="none",
                share=share, interval=interval, baseUrgency=urgency, checker=checker)


class FairGoalsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Ledger(self.root / "strategy.sqlite3")
        self.defs = [definition("research", 2, urgency=1000), definition("review", interval=3)]
        fair.synchronize(self.store, self.defs, 100)

    def capture(self, now=100):
        view = fair.snapshot(self.store, now)
        goal = view["selected"]
        key = fair.accept_selected(self.store, view)
        lease = {k: goal[k] for k in ("id", "round", "service")}
        lease["inventory_digest"] = view["inventory_digest"]
        return self.store.capture(key, "prompt", ROUTER, now, goal_lease=lease)

    def services(self):
        return [g["service"] for g in fair.snapshot(self.store, 100)["queue"]]

    def reply(self, snap, kind="text", now=100):
        return self.store.decide(snap["serial"], dict(kind=kind, tool="help", request=snap["focus"], owner=snap["owner"]), now)

    def observed(self, ticket):
        self.store.report(ticket, "witness", domain="success")
        result = self.store.protected(None)["results"][0]
        self.store.observe([ticket], {ticket: [result]})

    def test_matches_lean_weighted_trace_without_task_completion(self):
        trace = []
        for i in range(60):
            snap = self.capture()
            trace.append(snap["goal"]["id"])
            self.reply(snap)
        self.assertEqual(trace[:6], [1, 2, 1, 1, 2, 1])
        self.assertEqual(self.services(), [40, 20])
        self.assertTrue(all(r["status"] == "active" for r in self.store.active()))

    def test_exact_fractions_zero_and_blocked(self):
        def goal(id, service, share, **kw):
            return dict(id=id, service=service, share=share, age=0, baseUrgency=0,
                        blocked=False, finished=False, interval=None, **kw)
        a, b = goal(1, 3, 4), goal(2, 1, 2)
        self.assertEqual(fair.select([a, b]), b)
        a["share"] = 0
        self.assertEqual(fair.select([a, b]), b)
        b["blocked"] = True
        self.assertIsNone(fair.select([a, b]))

    def test_failure_is_charged_denied_cooldown_is_not(self):
        snap = self.capture()
        self.reply(snap, "failure")
        self.assertEqual(self.services(), [1, 0])
        self.assertIsNone(self.capture())
        self.assertEqual(self.services(), [1, 0])
        self.assertEqual(self.capture(102)["goal"]["id"], 2)

    def test_budget_denial_and_duplicate_capture_do_not_charge(self):
        self.store.policy(allowance=0)
        self.assertIsNone(self.capture())
        self.assertEqual(self.services(), [0, 0])
        self.store.policy(allowance=2)
        self.capture()
        self.assertIsNone(self.capture())
        self.assertEqual(self.services(), [1, 0])

    def test_charge_and_capture_rollback_together(self):
        original = self.store.event
        def crash(db, kind, **fields):
            if kind == "capture":
                raise RuntimeError("checkpoint fault")
            original(db, kind, **fields)
        with patch.object(self.store, "event", side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, "checkpoint"):
                self.capture()
        self.assertEqual(self.services(), [0, 0])
        self.assertEqual(self.store.projection()["meta"]["next_sample"], 0)
        self.assertFalse(any(e["kind"] == "goal-charge" for e in self.store.projection()["events"]))

    def test_restart_mode_and_compaction_preserve_service(self):
        self.capture()
        self.store = Ledger(self.store.path)
        self.store.restore()
        self.store.control(mode="coding")
        self.store.compact()
        self.assertEqual(self.services(), [1, 0])
        self.assertEqual(self.capture()["goal"]["id"], 2)

    def test_stale_inventory_and_wrong_focus_cannot_capture(self):
        view = fair.snapshot(self.store, 100)
        key = fair.accept_selected(self.store, view)
        lease = dict(id=1, round=0, service=0, inventory_digest=view["inventory_digest"])
        self.assertIsNone(self.store.capture("wrong", "prompt", ROUTER, 100, goal_lease=lease))
        self.defs[0]["note"] = "new constraint"
        fair.synchronize(self.store, self.defs, 100)
        self.assertIsNone(self.store.capture(key, "prompt", ROUTER, 100, goal_lease=lease))
        self.assertEqual(self.services(), [0, 0])

    def test_completion_needs_observation_and_preserves_charge(self):
        snap = self.capture()
        ticket = self.reply(snap, "command")
        self.assertFalse(self.store.finish(snap["focus"], ticket, {"operator": lambda *_: True}, now=100))
        self.observed(ticket)
        self.assertTrue(self.store.finish(snap["focus"], ticket, {"operator": lambda *_: True}, now=100))
        queue = fair.snapshot(self.store, 100)["queue"]
        self.assertEqual((queue[0]["finished"], queue[0]["round"], queue[0]["service"]), (True, 1, 1))
        self.assertEqual(fair.snapshot(self.store, 100)["selected"]["id"], 2)

    def test_recurring_cycle_rearms_without_refund(self):
        fair.synchronize(self.store, [self.defs[1]], 100)
        snap = self.capture()
        ticket = self.reply(snap, "command")
        self.observed(ticket)
        self.store.finish(snap["focus"], ticket, {"operator": lambda *_: True}, now=100)
        self.assertIsNone(fair.snapshot(self.store, 102)["selected"])
        goal = fair.snapshot(self.store, 103)["selected"]
        self.assertEqual((goal["round"], goal["service"]), (1, 1))
        self.assertNotEqual(fair.accept_selected(self.store, fair.snapshot(self.store, 103)), snap["focus"])

    def test_missing_goal_and_notes_do_not_finish_request(self):
        snap = self.capture(); self.reply(snap)
        self.defs[0]["note"] = "DONE"
        fair.synchronize(self.store, self.defs, 100)
        self.assertFalse(fair.snapshot(self.store, 100)["queue"][0]["finished"])
        fair.synchronize(self.store, self.defs[1:], 100)
        self.assertTrue(any(r["id"] == snap["focus"] for r in self.store.active()))
        fair.synchronize(self.store, self.defs, 100)
        self.assertEqual(self.services(), [1, 0])

    def test_new_goal_joins_at_current_service_floor(self):
        for i in range(12):
            self.reply(self.capture())
        fair.synchronize(self.store, self.defs + [definition("new")], 100)
        self.assertEqual(self.services(), [8, 4, 4])

    def test_duplicate_definition_fails_without_changing_state(self):
        with self.assertRaises(ValueError):
            fair.synchronize(self.store, self.defs * 2, 100)
        self.assertEqual(self.services(), [0, 0])

    def test_operator_cancellation_does_not_block_the_other_goals(self):
        snap = self.capture(); self.reply(snap)
        self.assertTrue(self.store.cancel(snap["focus"], operator_authorized=True))
        fair.synchronize(self.store, self.defs, 100)
        self.assertEqual(fair.snapshot(self.store, 100)["selected"]["id"], 2)
        self.assertEqual(self.services(), [1, 0])

    def test_blocked_goal_cannot_acknowledge_even_a_successful_receipt(self):
        snap = self.capture(); ticket = self.reply(snap, "command")
        self.observed(ticket)
        self.defs[0]["blocked"] = "waiting"
        fair.synchronize(self.store, self.defs, 100)
        self.assertFalse(self.store.finish(snap["focus"], ticket, {"operator": lambda *_: True}, now=100))

    def test_review_saves_full_inventory_then_observed_receipt_rearms(self):
        self.defs[1].update(checker="inventory-review", baseUrgency=2000)
        fair.synchronize(self.store, self.defs, 100)
        snap = self.capture(); ticket = self.reply(snap, "command")
        view = fair.snapshot(self.store, 100)
        entries = [dict(goal=g["name"], disposition="defer", assessment="Needs current evidence",
                        evidence="Existing goal note only; unverified", next_action="Inspect named source") for g in view["inventory"]]
        with self.assertRaises(ValueError):
            fair.save_review(self.store, ticket, view["inventory_digest"], entries[:-1], 100)
        marker = fair.save_review(self.store, ticket, view["inventory_digest"], entries, 100)
        self.store.report(ticket, marker)
        fair.finish_reviews(self.store, 100)
        self.assertEqual(len(self.store.active()), 1)
        result = self.store.protected(None)["results"][0]
        self.store.observe([ticket], {ticket: [result]})
        fair.finish_reviews(self.store, 100)
        self.assertEqual(len(self.store.active()), 0)
        self.assertEqual(self.services(), [0, 1])
        self.assertEqual(fair.snapshot(self.store, 100)["queue"][1]["round"], 1)


class FairHostTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "goals.metta"
        self.path.write_text('(goal research Work sti:0.5 lti:0.5 vibes:() blocked-by:none last-verified:never note:"Check a source")\n')
        env = patch.dict(os.environ, METTACLAW_FAIR_GOALS="1", METTACLAW_OBSERVED_STRATEGY="1",
                         METTACLAW_GOALS_PATH=str(self.path), METTACLAW_STRATEGY_PATH=str(self.root / "strategy.sqlite3"),
                         METTACLAW_LLM_LEDGER_PATH=str(self.root / "budget.sqlite3"), METTACLAW_LLM_POLICY="managed")
        env.start(); self.addCleanup(env.stop)
        state = patch.multiple(host, _ledger=None, _booted=False, _focus=None, _sample=None,
                               _goal_view=None, _input_turn=False, _prepared_capture=None)
        state.start(); self.addCleanup(state.stop)
        for context in (patch.object(host, "guard", return_value="control"),
                        patch.object(host, "_stimulus_free", return_value=True),
                        patch.dict(sys.modules, telegram=SimpleNamespace(_current_batch_update_ids=set())),
                        patch("lifecycle.cognition_enabled", return_value=1)):
            context.start(); self.addCleanup(context.stop)
        host.begin(1, "autonomous-trigger", "")

    def prepare(self):
        prompt = host.prepare("context")
        self.assertEqual(host.capture(prompt, 60), 1)

    def test_actual_policy_hook_charges_after_admission(self):
        self.prepare()
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)
        llm_policy.admit("fixture", 10)
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 1)
        self.assertIsNotNone(host._sample)

    def test_actual_quota_denial_does_not_charge(self):
        with patch.dict(os.environ, METTACLAW_LLM_OUTPUT_TOKENS_PER_DAY="1"):
            self.prepare()
            with self.assertRaises(llm_policy.AdmissionDenied):
                llm_policy.admit("fixture", 10)
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)

    def test_new_input_preempts_without_charging_autonomous_goal(self):
        # Delivery is still captured/correlated by the same protected ledger.
        from types import SimpleNamespace
        with patch.dict(sys.modules, telegram=SimpleNamespace(_current_batch_update_ids={100})):
            host.begin(2, "operator-event", "new request")
        self.prepare(); llm_policy.admit("fixture", 10)
        self.assertTrue(host._sample["focus"].startswith("input:"))
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)

    def test_input_arriving_during_preparation_withholds_provider(self):
        self.prepare()
        with patch.object(host, "_stimulus_free", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                llm_policy.admit("fixture", 10)
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)

    def test_existing_goal_writer_accepts_policy_without_replacing_goal(self):
        import goals
        self.assertIn("updated", goals.goal_write('(goal-policy research share:2 every:3600 urgency:5 checker:operator)', self.path))
        self.assertEqual(fair.definitions(self.path)[0]["share"], 2)
        self.assertEqual(fair.definitions(self.path)[0]["interval"], 3600)
        self.assertIn("Check a source", self.path.read_text())

    def test_removed_scheduler_projection_cannot_be_captured(self):
        prompt = host.prepare("context")
        protected = prompt[prompt.index("PROTECTED_STRATEGY "):]
        self.assertEqual(host.capture(protected, 60), 0)
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)

    def test_all_goals_blocked_does_not_keep_previous_goal_focus(self):
        self.prepare(); llm_policy.admit("fixture", 10)
        host.ledger().decide(host._sample["serial"], {"kind": "text"}, 0, "control")
        self.path.write_text(self.path.read_text().replace("blocked-by:none", "blocked-by:waiting"))
        host.begin(2, "continuation-tick", "")
        host.prepare("context")
        self.assertFalse(host._focus.startswith("goal:"))

    def test_upstream_provider_without_prepared_host_uses_no_scheduler(self):
        with patch.dict(os.environ, METTACLAW_LLM_POLICY="upstream"):
            llm_policy.admit("fixture", 10, mode="iter")
            llm_policy.admit("fixture", 10, mode="omega")
        self.assertIsNone(host._sample)
        self.assertEqual(host.goal_snapshot()["queue"][0]["service"], 0)


if __name__ == "__main__":
    unittest.main()
