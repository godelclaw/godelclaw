"""Useful admission decisions against the offline Python policy reference.

SQL representation and identifier allocation are deliberately not compared.
No provider, strategy worker or live application database is used.
"""
import importlib.util
from contextlib import closing
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

import test_provider_admission_cetta as fixture
WORKER = fixture.WORKER
if WORKER:
    from space_service_client import E, S


@unittest.skipUnless(WORKER, "native store/math worker required")
class ProviderAdmissionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProviderAdmissionTests()
        self.f.setUp()
        self.addCleanup(lambda: self.assertTrue(self.f.doCleanups()))
        path = fixture.ROOT / "src/llm_policy.py"
        spec = importlib.util.spec_from_file_location("offline_admission_reference", path)
        self.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.policy)

    def reference(self, index, profile, now, deadline, maximum=100):
        path = self.f.directory / ("reference-" + str(index) + ".sqlite3")
        events = []
        strategy = types.SimpleNamespace(provider_admitted=lambda ticket: events.append(ticket))
        policy = self.policy
        with patch.object(policy, "ledger_path", return_value=path), \
             patch.object(policy, "configuration", return_value={"profile": profile, "quota_cooldown_seconds": 300}), \
             patch.dict(sys.modules, strategy_host=strategy):
            if deadline is not None:
                with policy.transaction() as connection:
                    connection.execute("INSERT INTO cooldowns(provider,until) VALUES(?,?)", ("provider", deadline))
            try:
                ticket = policy.admit("provider", maximum, now=now, mode="godelclaw")
            except policy.AdmissionDenied as error:
                self.assertEqual(events, [])
                return ("deferred", error.retry_after)
            except ValueError:
                self.assertEqual(events, [])
                return ("invalid-maximum", None)
        self.assertEqual(events, [ticket])
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute("SELECT status,reserved FROM requests").fetchall()
        self.assertEqual(rows, [("pending", maximum)])
        return ("eligible", None)

    def test_profiles_and_deadline_boundaries_match_the_actual_policy(self):
        cases = [(profile, now, deadline) for profile in ("managed", "upstream")
                 for now, deadline in ((10, None), (10, 11), (11, 11), (12, 11), (1234567890, 1234567891))]
        for index, (profile, now, deadline) in enumerate(cases):
            with self.subTest(profile=profile, now=now, deadline=deadline):
                expected = self.reference(index, profile, now, deadline)
                provider = "provider-" + str(index)
                if deadline is not None:
                    self.f.answer(self.f.defer(deadline * 1000000000, provider=provider))
                command = self.f.admit(call="call-" + str(index), provider=provider,
                                       profile="ProviderManagedV2" if profile == "managed" else "ProviderUpstreamV2",
                                       now=now * 1000000000)
                result = self.f.answer(command)
                if expected[0] == "eligible":
                    self.f.same(result, E("ProviderAdmittedV2", "call-" + str(index), "owner"))
                else:
                    self.assertEqual(result[0], S("ProviderAdmissionWaitingV2"), result)
                    self.assertEqual(result[3], int(expected[1] * 1000000000))
                    self.f.same(self.f.value(E("ProviderCallKeyV2", "call-" + str(index))), S("KnowledgeAbsentV1"))

    def test_zero_maximum_does_not_admit_in_either_profile(self):
        for index, profile in enumerate(("managed", "upstream")):
            with self.subTest(profile=profile):
                self.assertEqual(self.reference(index, profile, 10, None, maximum=0), ("invalid-maximum", None))
                before = self.f.audit()
                result = self.f.execute(self.f.admit(maximum=0, profile="ProviderManagedV2"
                                                     if profile == "managed" else "ProviderUpstreamV2"))
                self.assertEqual(result[0], S("ProviderAdmissionRefusedV2"), result)
                self.assertEqual(self.f.audit(), before)


if __name__ == "__main__":
    unittest.main()
