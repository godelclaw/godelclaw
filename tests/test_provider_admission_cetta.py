"""Application admission and transport deadlines in the native MeTTa store."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT.parent / "CeTTa-ours"))
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from value_worker_client import Worker
    from space_service_client import E, S, BigInt, FloatBits, encode


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to the native store/math worker")
class ProviderAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-provider-admission-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "store"
        self.path.mkdir(mode=0o700)
        self.sequence = 0
        self.worker = self.make_worker()
        self.addCleanup(lambda: self.worker.close())
        answer = self.worker.call(E("Create", str(self.path)))
        self.assertEqual(answer[0], S("KnowledgeReadyV1"), answer)
        self.epoch = answer[1]

    def make_worker(self, **kwargs):
        environment = {"HOME": str(self.directory), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        environment.update(kwargs)
        with patch.dict(os.environ, environment, clear=True):
            return Worker(WORKER, ROOT / "tests/support/provider_admission_probe.metta",
                          "provider-admission-probe:run", language="petta", import_mode="relative")

    def same(self, actual, expected):
        self.assertEqual(encode(actual), encode(expected))

    @staticmethod
    def integer(value):
        return value if -(1 << 63) <= value < (1 << 63) else BigInt(str(value))

    def admit(self, *, call="call", token="owner", profile="ProviderManagedV2", now=10,
              maximum=100, provider="provider", request=None, origin=None):
        info = E("ProviderCallInfoV2", provider, "model", "coding",
                 E("Origin", "operator", "session") if origin is None else origin)
        terms = E("ProviderAdmissionTermsV2", S(profile), "godelclaw", self.integer(now), maximum,
                  E("RequestRef", "request") if request is None else request, E("ProcessRef", "process"))
        return E("ProviderAdmitV2", call, token, info, terms)

    def defer(self, until, *, provider="provider", source="retry-after"):
        return E("ProviderDeferV2", provider, self.integer(until), source)

    def execute(self, command, receipt=None):
        self.sequence += 1
        return self.worker.call(E("At", str(self.path), E("Command", self.sequence) if receipt is None else receipt,
                                  command))

    def answer(self, command, receipt=None):
        result = self.execute(command, receipt)
        self.assertIn(result[0], (S("KnowledgeCommittedV1"), S("KnowledgeReplayedV1")), result)
        return result[1]

    def knowledge(self, operation):
        return self.worker.call(E("Knowledge", str(self.path), operation))

    def application(self, facts, *, command=None):
        self.sequence += 1
        changes = tuple(E("KnowledgePutV1", key, value) for key, value in facts)
        return E("KnowledgeApplyV1", (), E("Fixture", self.sequence),
                 E("FixtureChange", changes) if command is None else command, S("FixtureAccepted"), changes, 1048576)

    def put(self, facts):
        result = self.knowledge(self.application(facts))
        self.assertEqual(result[0], S("KnowledgeCommittedV1"), result)

    def value(self, identity):
        answer = self.knowledge(E("KnowledgeReadV1", (identity,), 1048576))
        self.assertEqual(answer[0], S("KnowledgeObservedV1"), answer)
        return answer[1][0][2]

    def audit(self):
        answer = self.knowledge(S("KnowledgeProbeAuditV1"))
        self.assertEqual(answer[0], S("KnowledgeAuditV1"), answer)
        return tuple(value for key, value in answer[1])

    def race(self, command, first, *, extra=(), receipt="prepared"):
        return self.worker.call(E("Race", str(self.path), receipt, command, first, tuple(extra)))

    def test_fresh_admission_commits_metadata_lease_terms_and_receipt_without_a_submission(self):
        command = self.admit()
        result = self.execute(command, "admission")
        self.same(result[1], E("ProviderAdmittedV2", "call", "owner"))
        self.same(self.value(E("ProviderCallKeyV2", "call")), E("KnowledgePresentV1", command[3]))
        self.same(self.value(E("ProviderAdmissionKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderAdmissionV2", "owner", command[3], command[4])))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "owner", S("ProviderLeaseActiveV2"))))
        self.same(self.value(E("ProviderAttemptKeyV2", "call", "owner")), S("KnowledgeAbsentV1"))
        self.assertEqual(len(self.audit()), 4)
        self.same(self.worker.call(E("FirstAdmission", result)), S("ProviderFirstAdmissionV2"))

    def test_managed_before_at_and_after_deadline_with_exact_large_timestamps(self):
        boundary = 10 ** 20 + 1
        self.answer(self.defer(boundary))
        self.same(self.answer(self.admit(call="before", now=boundary - 1)),
                  E("ProviderAdmissionWaitingV2", "before", BigInt(str(boundary)), 1, "retry-after"))
        self.same(self.value(E("ProviderCallKeyV2", "before")), S("KnowledgeAbsentV1"))
        for suffix, now in (("at", boundary), ("after", boundary + 1)):
            self.same(self.answer(self.admit(call=suffix, now=now)), E("ProviderAdmittedV2", suffix, "owner"))

    def test_upstream_bypasses_transport_cooldown_without_a_false_read_dependency(self):
        self.answer(self.defer(100))
        self.same(self.answer(self.admit(call="upstream", profile="ProviderUpstreamV2")),
                  E("ProviderAdmittedV2", "upstream", "owner"))
        changed = self.application([(E("ProviderCooldownKeyV2", "provider"), E("ProviderCooldownV2", 200, "later"))])
        result = self.race(self.admit(call="another", profile="ProviderUpstreamV2"), changed)
        self.same(result[2][1], E("ProviderAdmittedV2", "another", "owner"))

    def test_replay_and_same_call_redelivery_do_not_reset_a_transferred_or_completed_lease(self):
        command = self.admit()
        self.answer(command, "same")
        later = E("ProviderLeaseV2", "new-owner", S("ProviderLeaseCompletedV2"))
        self.put([(E("ProviderLeaseKeyV2", "call"), later),
                  (E("ProviderAnswerKeyV2", "call"), E("ProviderAnswerV2", "submission", "answer"))])
        before = encode(self.audit())
        result = self.execute(command, "same")
        self.same(result, E("KnowledgeReplayedV1", E("ProviderAdmittedV2", "call", "owner")))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.worker.call(E("FirstAdmission", result)), S("ProviderNoNewAdmissionV2"))
        result = self.execute(command, "another-receipt")
        self.same(result[1], E("ProviderAlreadyAdmittedV2", "call"))
        self.same(self.worker.call(E("FirstAdmission", result)), S("ProviderNoNewAdmissionV2"))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")), E("KnowledgePresentV1", later))

    def test_call_identity_and_receipt_collisions_are_distinct_and_do_not_overwrite(self):
        self.answer(self.admit(), "same")
        result = self.execute(self.admit(request="different"), "same")
        self.same(result, E("KnowledgeRefusedV1", S("receipt-collision")))
        self.same(self.answer(self.admit(request="different")),
                  E("ProviderAdmissionRefusedV2", S("call-identity-conflict")))
        self.same(self.value(E("ProviderAdmissionKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderAdmissionV2", "owner", self.admit()[3], self.admit()[4])))

    def test_invalid_commands_cannot_publish_partial_admission(self):
        valid = self.admit()
        bad_terms = [E("ProviderAdmissionTermsV2", S("ProviderManagedV2"), "godelclaw", 10, value, "r", "p")
                     for value in (0, -1, True, "100", FloatBits(0x3ff0000000000000))]
        invalid = [E("ProviderAdmitV2", "call", "owner", valid[3], terms) for terms in bad_terms]
        invalid += [E("ProviderAdmitV2", "", "owner", valid[3], valid[4]),
                    E("ProviderAdmitV2", "call", "", valid[3], valid[4]),
                    E("ProviderAdmitV2", "call", "owner", E("bad", "metadata"), valid[4]),
                    E("ProviderDeferV2", "provider", True, "source")]
        before = encode(self.audit())
        for command in invalid:
            with self.subTest(command=command):
                result = self.execute(command)
                self.assertEqual(result[0], S("ProviderAdmissionRefusedV2"), result)
                self.assertEqual(encode(self.audit()), before)

    def test_partial_call_records_are_reported_without_becoming_fresh_admission(self):
        self.put([(E("ProviderAnswerKeyV2", "call"), E("ProviderAnswerV2", "old", "answer"))])
        self.same(self.answer(self.admit()), E("ProviderAdmissionRefusedV2", S("inconsistent-call")))
        self.same(self.value(E("ProviderCallKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_conflicting_absence_prevents_two_captured_admissions_of_one_call(self):
        command = self.admit()
        first = E("AdmissionChange", "other", self.admit(token="other"))
        result = self.race(command, first)
        self.same(result[1][1], E("ProviderAdmittedV2", "call", "other"))
        self.same(result[2], E("StoreRefused", S("conflict")))
        resolved = self.knowledge(E("KnowledgeResolveV1", "prepared", command, 1048576))
        self.assertEqual(resolved[0], S("KnowledgeUnrecordedV1"), resolved)

    def test_two_native_admissions_of_distinct_calls_need_no_global_serialization(self):
        first = E("AdmissionChange", "first", self.admit(call="first", maximum=BigInt(str(1 << 100))))
        result = self.race(self.admit(call="second"), first)
        self.same(result[1][1], E("ProviderAdmittedV2", "first", "owner"))
        self.same(result[2][1], E("ProviderAdmittedV2", "second", "owner"))

    def test_relevant_cooldown_and_authority_changes_invalidate_a_prepared_admission(self):
        changed = E("AdmissionChange", "cooldown", self.defer(20))
        self.same(self.race(self.admit(), changed)[2], E("StoreRefused", S("conflict")))
        grant = E("GrantKey", "owner", "dispatch")
        self.put([(grant, E("Grant", 1, True))])
        changed = self.application([(grant, E("Grant", 2, False))])
        self.same(self.race(self.admit(call="authorized", profile="ProviderUpstreamV2"), changed,
                            extra=(grant,), receipt="authority")[2], E("StoreRefused", S("conflict")))
        self.same(self.value(E("ProviderCallKeyV2", "authorized")), S("KnowledgeAbsentV1"))

    def test_other_provider_deadlines_do_not_conflict(self):
        changed = E("AdmissionChange", "other-cooldown", self.defer(999, provider="another"))
        result = self.race(self.admit(), changed)
        self.same(result[2][1], E("ProviderAdmittedV2", "call", "owner"))

    def test_cooldown_extension_is_monotone_with_each_observation_retained(self):
        self.same(self.answer(self.defer(100, source="first")), E("ProviderCooldownRecordedV2", 100))
        self.same(self.answer(self.defer(50, source="shorter"), "shorter"), E("ProviderCooldownRecordedV2", 100))
        self.same(self.value(E("ProviderCooldownKeyV2", "provider")),
                  E("KnowledgePresentV1", E("ProviderCooldownV2", 100, "first")))
        receipt = E("KnowledgeReceiptV1", "shorter", self.defer(50, source="shorter"), E("ProviderCooldownRecordedV2", 100))
        self.assertIn(receipt, self.audit())
        self.same(self.answer(self.defer(200, source="later")), E("ProviderCooldownRecordedV2", 200))
        self.same(self.value(E("ProviderCooldownKeyV2", "provider")),
                  E("KnowledgePresentV1", E("ProviderCooldownV2", 200, "later")))

    def test_metadata_is_inert_and_an_admitted_call_completes_only_after_submission_registration(self):
        sentinel = self.directory / "not-executed"
        origin = E("system", "touch " + str(sentinel))
        command = self.admit(origin=origin)
        self.answer(command)
        self.assertFalse(sentinel.exists())
        usage = E("ProviderUsageV2", E("ProviderCountV2", 100), E("ProviderCountV2", 7),
                  S("ProviderCountUnknownV2"), S("ProviderCountUnknownV2"))
        finish = E("ProviderFinishV2", "call", "owner", E("AnswerRef", "answer"), usage, "provider")
        result = self.worker.call(E("Finish", str(self.path), "before-submit", finish))
        self.same(result[1], E("ProviderDomainRefusedV2", S("unregistered-attempt")))
        self.put([(E("ProviderAttemptKeyV2", "call", "owner"), E("ProviderSubmittedAttemptV2", "physical-submission"))])
        result = self.worker.call(E("Finish", str(self.path), "finish", finish))
        self.same(result[1], E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.answer(command), E("ProviderAlreadyAdmittedV2", "call"))

    def test_missing_decision_dependencies_are_not_interpreted_as_absent(self):
        result = self.worker.call(E("PurePlan", self.admit(), ()))
        self.same(result, E("ProviderAdmissionPlanV2", E("ProviderAdmissionRefusedV2", S("incomplete-observation")), ()))


if __name__ == "__main__":
    unittest.main()
