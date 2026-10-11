"""Recovery at the provider/effect boundary, with the real durable ledger."""
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import admission_frontier as frontier
from observed_strategy import Ledger


class FrontierContractTests(unittest.TestCase):
    def test_only_the_admitted_prefix_has_execution_authority(self):
        proposal = {"commands": [["recent-messages", "room", 6], ["rest", 900]], "limit": 1}
        self.assertTrue(frontier.observational(json.dumps(proposal)))
        self.assertFalse(frontier.observational({**proposal, "limit": 2}))
        self.assertFalse(frontier.observational({"commands": [["shell", "read-looking"]], "limit": 1}))
        for value in (None, "Error", {}, [], {"commands": [], "limit": 1},
                      {**proposal, "limit": 0}, {**proposal, "limit": True},
                      {"commands": [None], "limit": 1}):
            with self.subTest(value=value):
                self.assertFalse(frontier.observational(value))

    def test_thought_gate_is_independent_of_outstanding_effects(self):
        for available, paused, pending, allowance, cooldown in itertools.product(
                (False, True), (False, True), (None, 0), (None, 0, 1), (0, 2)):
            code = frontier.thought_denial(available=available, paused=paused,
                pending=pending, allowance=allowance, cooldown=cooldown, now=1)
            expected = available and not paused and pending is None and allowance != 0 and cooldown <= 1
            self.assertEqual(code is None, expected)

    def test_every_outstanding_effect_blocks_new_effects_but_allows_observations(self):
        for command, status, receipt, observed in itertools.product(
                (["help"], ["shell", "change"]), ("issued", "uncertain", "returned"),
                (None, "receipt"), (0, 1)):
            row = dict(ticket="old", command=command, status=status, receipt=receipt, observed=observed)
            expected = status in ("issued", "uncertain") or (receipt is not None and not observed)
            self.assertEqual(frontier.may_issue([row], ["shell", "new"]),
                             command[0] == "help" or not expected)
            self.assertTrue(frontier.may_issue([row], ["help"]))
            self.assertTrue(frontier.may_issue([row], ["shell", "new"], exclude="old"))

    def test_diagnostics_do_not_trust_messages_or_arbitrary_error_attributes(self):
        error = RuntimeError("credential-containing response body")
        error.reason_code = "private-data"
        self.assertEqual(frontier.failure_code(error), "RuntimeError")
        self.assertEqual(frontier.failure_code(frontier.AdmissionDenied("provider-in-flight")),
                         "provider-in-flight")


class DurableFrontierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Ledger(Path(temporary.name) / "ledger.sqlite3")
        self.store.accept("task", "work")
        self.router = {key: ["help", "shell", "recent-messages"]
                       for key in ("advertised", "executable", "permitted")}

    def capture(self):
        value = self.store.capture("task", "context", self.router, 0)
        self.assertIsNotNone(value)
        return value

    def decide(self, snap, command):
        prefix = frontier.admitted_prefix(command)
        return self.store.decide(snap["serial"], dict(kind="command", owner=snap["owner"],
            request="task", tool=prefix[0][0], command=command), 0)

    def issue(self, command):
        return self.decide(self.capture(), command)

    def observe(self, ticket):
        result = next(r for r in self.store.protected("task")["results"] if r["ticket"] == ticket)
        self.store.observe([ticket], {ticket: [result]})

    def test_missing_read_receipt_survives_restart_without_stopping_thought_or_effects(self):
        proposal = {"commands": [["recent-messages", "room", 6], ["shell", "deferred"]], "limit": 1}
        old = self.issue(proposal)
        self.store.restore()
        self.store = Ledger(self.store.path)
        context = self.store.protected("task")
        self.assertEqual(context["uncertainty"][0]["kind"], "observation")
        self.assertFalse(context["uncertainty"][0]["replay"])
        new = self.issue(["shell", "new decision"])
        self.assertNotIn(new, ("blocked-effects", "ignored"))
        actions = self.store.projection()["actions"]
        self.assertEqual([(r["ticket"], r["status"]) for r in actions], [(old, "uncertain"), (new, "issued")])
        self.assertIsNone(actions[0]["receipt"])
        self.assertEqual(actions[0]["observed"], 0)

    def test_missing_effect_receipt_allows_fresh_reads_and_thought_but_no_mutation(self):
        old = self.issue(["shell", "mutation"])
        self.store.restore()
        self.assertEqual(self.decide(self.capture(), ["shell", "another mutation"]), "blocked-effects")
        read = self.issue(["help"])
        self.assertTrue(self.store.report(read, "information", domain="success"))
        self.observe(read)
        self.assertFalse(self.store.finish("task", read, {"operator": lambda *a: True}))
        self.assertEqual(self.store.projection()["actions"][0]["ticket"], old)
        self.assertIsNone(self.store.projection()["actions"][0]["receipt"])

    def test_late_effect_result_invalidates_pending_plan_and_requires_exact_observation(self):
        old = self.issue(["shell", "mutation"])
        self.store.restore()
        snap = self.capture()
        self.assertTrue(self.store.report(old, "late receipt", domain="success"))
        self.assertEqual(self.decide(snap, ["help"]), "stale")
        self.assertEqual(self.decide(self.capture(), ["shell", "new mutation"]), "blocked-effects")
        self.store.observe([old], {old: [{"ticket": old, "payload_digest": "forged"}]})
        self.assertEqual(self.decide(self.capture(), ["shell", "new mutation"]), "blocked-effects")
        self.observe(old)
        self.assertNotEqual(self.issue(["shell", "fresh plan"]), "blocked-effects")
        self.assertFalse(self.store.report(old, "duplicate"))

    def test_single_provider_budget_pause_and_cooldown_remain_enforced(self):
        self.capture()
        self.assertEqual(self.store.admission_denial("task", 0), "provider-in-flight")
        self.assertIsNone(self.store.capture("task", "duplicate", self.router, 0))
        self.store.restore()
        self.store.control(paused=True)
        self.assertEqual(self.store.admission_denial("task", 0), "cognition-paused")
        self.store.control(paused=False)
        self.store.policy(allowance=0)
        self.assertEqual(self.store.admission_denial("task", 0), "allowance-exhausted")
        self.store.policy(allowance=1, cooldown=2)
        self.assertEqual(self.store.admission_denial("task", 1), "admission-cooldown")
        self.assertIsNotNone(self.store.capture("task", "ready", self.router, 2))

