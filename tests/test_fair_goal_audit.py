import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_fair_goals import read, export
from tests import test_fair_goals as fixtures


class FairAuditTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.FairGoalsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for _ in range(6):
            fixture.reply(fixture.capture())
        self.view = read(fixture.store.path)

    def test_exports_numeric_decisions_without_private_names(self):
        source, summary = export(self.view)
        self.assertEqual(summary["charges"], 6)
        self.assertNotIn("research", source)
        self.assertNotIn("review", source)
        self.assertEqual(source.count("by decide"), 12)

    def test_detects_duplicate_missing_and_reset_charges(self):
        charge = next(e for e in self.view["events"] if json.loads(e["event"])["kind"] == "goal-charge")
        variants = []
        duplicate = copy.deepcopy(self.view)
        duplicate["events"].append(charge)
        variants.append(duplicate)
        missing = copy.deepcopy(self.view)
        missing["events"].remove(charge)
        variants.append(missing)
        reset = copy.deepcopy(self.view)
        state = json.loads(reset["goal_accounts"][0]["state"])
        state["service"] = 0
        reset["goal_accounts"][0]["state"] = json.dumps(state)
        variants.append(reset)
        for view in variants:
            with self.assertRaises(ValueError):
                export(view)

    def test_detects_charge_without_matching_provider_snapshot(self):
        self.view["samples"][0]["snapshot"] = json.dumps({"serial": 0})
        with self.assertRaises(ValueError):
            export(self.view)


if __name__ == "__main__":
    unittest.main()
