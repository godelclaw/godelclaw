"""Native application invariants, independent of SQL representation quirks."""
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
class ProviderDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-provider-domain-")
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
        self.register()

    def make_worker(self, *, import_mode="relative", **kwargs):
        environment = {"HOME": str(self.directory), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        environment.update(kwargs)
        with patch.dict(os.environ, environment, clear=True):
            return Worker(WORKER, ROOT / "tests/support/provider_domain_probe.metta",
                          "provider-domain-probe:run", language="petta", import_mode=import_mode)

    def same(self, actual, expected):
        self.assertEqual(encode(actual), encode(expected))

    def knowledge(self, operation):
        return self.worker.call(E("Knowledge", str(self.path), operation))

    def application(self, changes, ids=()):
        self.sequence += 1
        return E("KnowledgeApplyV1", tuple(ids), E("FixtureReceipt", self.sequence),
                 E("FixtureChange", tuple(changes)), S("FixtureAccepted"), tuple(changes), 1048576)

    def put(self, facts):
        result = self.knowledge(self.application([E("KnowledgePutV1", key, value) for key, value in facts]))
        self.assertEqual(result[0], S("KnowledgeCommittedV1"), result)

    def register(self, call="call", attempt="attempt", *, lease=True, submitted=True):
        facts = [(E("ProviderCallKeyV2", call), E("ProviderCallInfoV2", "provider", "model", "coding", E("Origin", "user"))),
                 (E("ProviderAttemptKeyV2", call, attempt),
                  E("ProviderSubmittedAttemptV2", E("Transport", call, attempt)) if submitted else S("ProviderUnsubmittedAttemptV2"))]
        if lease:
            facts.append((E("ProviderLeaseKeyV2", call), E("ProviderLeaseV2", attempt, S("ProviderLeaseActiveV2"))))
        self.put(facts)

    def values(self, identities):
        answer = self.knowledge(E("KnowledgeReadV1", tuple(identities), 1048576))
        self.assertEqual(answer[0], S("KnowledgeObservedV1"), answer)
        return tuple(point[2] for point in answer[1])

    def value(self, identity):
        return self.values([identity])[0]

    def audit(self):
        answer = self.knowledge(S("KnowledgeProbeAuditV1"))
        self.assertEqual(answer[0], S("KnowledgeAuditV1"), answer)
        return tuple(value for key, value in answer[1])

    @staticmethod
    def usage(input=None, output=None, cached=None, written=None):
        return E("ProviderUsageV2", *(S("ProviderCountUnknownV2") if count is None else E("ProviderCountV2", count)
                                      for count in (input, output, cached, written)))

    def finish(self, usage=None, *, call="call", attempt="attempt", reference=None, source="provider-observation"):
        return E("ProviderFinishV2", call, attempt, E("AnswerRef", "answer") if reference is None else reference,
                 self.usage() if usage is None else usage, source)

    def record(self, usage, *, call="call", attempt="attempt", source="provider-observation"):
        return E("ProviderRecordUsageV2", call, attempt, usage, source)

    def execute(self, command, receipt=None):
        self.sequence += 1
        if receipt is None:
            receipt = E("Observation", self.sequence)
        return self.worker.call(E("At", str(self.path), receipt, command))

    def answer(self, command, receipt=None):
        result = self.execute(command, receipt)
        self.assertIn(result[0], (S("KnowledgeCommittedV1"), S("KnowledgeReplayedV1")), result)
        return result[1]

    def test_completion_publishes_result_usage_terminal_lease_and_receipt_together(self):
        usage = self.usage(100, 7, 80, 12)
        command = self.finish(usage)
        self.same(self.answer(command, "complete"), E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.values([E("ProviderLeaseKeyV2", "call"), E("ProviderAnswerKeyV2", "call"),
                               E("ProviderUsageKeyV2", "call", "attempt")]),
                  (E("KnowledgePresentV1", E("ProviderLeaseV2", "attempt", S("ProviderLeaseCompletedV2"))),
                   E("KnowledgePresentV1", E("ProviderAnswerV2", "attempt", E("AnswerRef", "answer"))),
                   E("KnowledgePresentV1", usage)))
        self.assertIn(E("KnowledgeReceiptV1", "complete", command,
                        E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))), self.audit())

    def test_replay_is_stable_even_after_another_owner_changes_the_lease(self):
        command = self.finish(self.usage(100, 7))
        first = self.answer(command, "same")
        self.put([(E("ProviderLeaseKeyV2", "call"), E("ProviderLeaseV2", "another", S("ProviderLeaseActiveV2")))])
        before = encode(self.audit())
        self.same(self.execute(command, "same"), E("KnowledgeReplayedV1", first))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.execute(self.finish(self.usage(101, 7)), "same"), E("KnowledgeRefusedV1", S("receipt-collision")))
        self.assertEqual(encode(self.audit()), before)

    def test_stale_attempt_keeps_late_usage_but_cannot_complete_the_current_lease(self):
        self.register(attempt="new-owner")
        usage = self.usage(51, 6)
        self.same(self.answer(self.finish(usage)), E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", usage))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "new-owner", S("ProviderLeaseActiveV2"))))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_unknown_counters_are_not_zero_and_late_usage_enriches_one_tally(self):
        self.answer(self.finish())
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage()))
        for incoming in (self.usage(0, 7), self.usage(None, None, 0, 0), self.usage()):
            self.same(self.answer(self.record(incoming)), E("ProviderUsageRecordedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(0, 7, 0, 0)))
        usage_facts = [fact for fact in self.audit()
                       if fact[0] == S("KnowledgeFactV1") and fact[1] == E("ProviderUsageKeyV2", "call", "attempt")]
        self.assertEqual(len(usage_facts), 1)

    def test_duplicate_usage_with_new_delivery_identity_does_not_add_another_tally(self):
        usage = self.usage(100, 7)
        for _ in range(8):
            self.same(self.answer(self.record(usage)), E("ProviderUsageRecordedV2", S("ProviderUsageAcceptedV2")))
        usage_facts = [fact for fact in self.audit() if fact[0] == S("KnowledgeFactV1")
                       and fact[1] == E("ProviderUsageKeyV2", "call", "attempt")]
        self.same(tuple(usage_facts), (E("KnowledgeFactV1", E("ProviderUsageKeyV2", "call", "attempt"), usage),))

    def test_a_new_delivery_does_not_grant_first_completion_twice(self):
        command = self.finish(self.usage(100, 7))
        first = self.execute(command, "first")
        replay = self.execute(command, "first")
        another_delivery = self.execute(command, "second")
        self.same(another_delivery[1], E("ProviderAlreadyCompletedV2", S("ProviderUsageAcceptedV2")))
        for result, expected in ((first, "ProviderFirstCompletionV2"), (replay, "ProviderNoNewCompletionV2"),
                                 (another_delivery, "ProviderNoNewCompletionV2")):
            self.same(self.worker.call(E("FirstCompletion", result)), S(expected))

    def test_conflicting_usage_is_retained_as_evidence_and_does_not_overwrite_counts(self):
        self.answer(self.record(self.usage(100, 7)))
        command = self.record(self.usage(101, 7), source=E("EvidenceRef", "conflicting-report"))
        result = self.answer(command, "conflict")
        self.same(result, E("ProviderUsageRecordedV2", S("ProviderUsageConflictV2")))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(100, 7)))
        self.assertIn(E("KnowledgeReceiptV1", "conflict", command, result), self.audit())

    def test_usage_conflict_does_not_discard_a_received_answer(self):
        self.answer(self.record(self.usage(100, 7)))
        command = self.finish(self.usage(101, 7))
        self.same(self.answer(command), E("ProviderCompletedV2", S("ProviderUsageConflictV2")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderAnswerV2", "attempt", E("AnswerRef", "answer"))))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(100, 7)))

    def test_another_answer_cannot_replace_an_established_result(self):
        self.answer(self.finish(self.usage(100, 7)))
        command = self.finish(self.usage(100, 7), reference=E("AnswerRef", "different-answer"))
        result = self.answer(command, "different")
        self.same(result, E("ProviderAnswerConflictV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderAnswerV2", "attempt", E("AnswerRef", "answer"))))
        self.assertIn(E("KnowledgeReceiptV1", "different", command, result), self.audit())

    def test_unknown_failed_and_absent_leases_do_not_rearm_completion(self):
        for phase in (S("ProviderLeaseUnknownV2"), S("ProviderLeaseFailedV2")):
            self.put([(E("ProviderLeaseKeyV2", "call"), E("ProviderLeaseV2", "attempt", phase))])
            self.same(self.answer(self.finish(self.usage(100, 7))), E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2")))
            self.same(self.value(E("ProviderLeaseKeyV2", "call")), E("KnowledgePresentV1", E("ProviderLeaseV2", "attempt", phase)))
        self.knowledge(self.application([E("KnowledgeRemoveV1", E("ProviderLeaseKeyV2", "call"))]))
        self.same(self.answer(self.finish(self.usage(100, 7))), E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_unregistered_and_unsubmitted_attempts_do_not_gain_authority_or_usage(self):
        self.register(attempt="unsubmitted", submitted=False)
        for call, attempt, reason in (("call", "missing", "unregistered-attempt"),
                                      ("missing", "attempt", "unregistered-call"),
                                      ("call", "unsubmitted", "submitted-attempt-required")):
            with self.subTest(call=call, attempt=attempt):
                command = self.finish(self.usage(9, 2), call=call, attempt=attempt)
                self.same(self.answer(command), E("ProviderDomainRefusedV2", S(reason)))
                self.same(self.value(E("ProviderUsageKeyV2", call, attempt)), S("KnowledgeAbsentV1"))

    def test_count_types_are_explicit_and_large_integers_are_supported(self):
        wide = BigInt(str(1 << 100))
        self.same(self.answer(self.record(self.usage(wide, 0))), E("ProviderUsageRecordedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(wide, 0)))
        for invalid in (-1, True, 1.0, "1", S("one"), E("+", 1, 2)):
            with self.subTest(invalid=invalid):
                command = self.record(self.usage(invalid, 0))
                self.same(self.answer(command), E("ProviderDomainRefusedV2", S("usage-format")))
                self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(wide, 0)))

    def test_opaque_sources_and_result_references_are_inert(self):
        sentinel = self.directory / "never-created"
        reference = E("system", "touch " + str(sentinel))
        source = E("+", 2, 3)
        command = self.finish(self.usage(1, 0), reference=reference, source=source)
        self.same(self.answer(command, "inert"), E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), E("KnowledgePresentV1", E("ProviderAnswerV2", "attempt", reference)))
        self.assertFalse(sentinel.exists())
        self.assertIn(E("KnowledgeReceiptV1", "inert", command,
                        E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))), self.audit())

    def test_missing_observations_are_not_assumed_absent(self):
        command = self.finish(self.usage(1, 0))
        keys = [E("ProviderCallKeyV2", "call"), E("ProviderAttemptKeyV2", "call", "attempt"),
                E("ProviderUsageKeyV2", "call", "attempt"), E("ProviderLeaseKeyV2", "call"), E("ProviderAnswerKeyV2", "call")]
        observed = self.knowledge(E("KnowledgeReadV1", tuple(keys), 1048576))[1]
        reasons = ["call-not-observed", "attempt-not-observed", "usage-not-observed", "lease-not-observed", "answer-not-observed"]
        for position, reason in enumerate(reasons):
            with self.subTest(missing=keys[position]):
                points = tuple(value for index, value in enumerate(observed) if index != position)
                result = self.worker.call(E("PurePlan", command, points))
                self.same(result, E("ProviderDomainRefusedV2", S(reason)))

    def test_ownership_change_invalidates_the_whole_prepared_completion(self):
        command = self.finish(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderLeaseKeyV2", "call"),
                                    E("ProviderLeaseV2", "another", S("ProviderLeaseActiveV2")))])
        result = self.worker.call(E("Race", str(self.path), "stale", command, other))
        self.assertEqual(result[1][0], S("KnowledgeCommittedV1"), result)
        self.same(result[2], E("StoreRefused", S("conflict")))
        self.same(self.values([E("ProviderAnswerKeyV2", "call"), E("ProviderUsageKeyV2", "call", "attempt")]),
                  (S("KnowledgeAbsentV1"), S("KnowledgeAbsentV1")))
        self.same(self.knowledge(E("KnowledgeResolveV1", "stale", command, 1048576)),
                  E("KnowledgeUnrecordedV1", self.epoch, 2))
        self.same(self.answer(command), E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2")))

    def test_late_usage_does_not_depend_on_a_changing_active_lease(self):
        command = self.record(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderLeaseKeyV2", "call"),
                                    E("ProviderLeaseV2", "another", S("ProviderLeaseActiveV2")))])
        result = self.worker.call(E("Race", str(self.path), "late", command, other))
        self.assertEqual(result[2][0], S("KnowledgeCommittedV1"), result)
        self.same(result[2][1], E("ProviderUsageRecordedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "another", S("ProviderLeaseActiveV2"))))

    def test_attempt_registration_is_a_read_dependency_even_when_it_is_not_written(self):
        command = self.finish(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderAttemptKeyV2", "call", "attempt"),
                                    S("ProviderUnsubmittedAttemptV2"))])
        result = self.worker.call(E("Race", str(self.path), "revoked", command, other))
        self.same(result[2], E("StoreRefused", S("conflict")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_an_unchanged_usage_tally_is_still_an_influencing_read(self):
        self.answer(self.record(self.usage(100, 7)))
        command = self.finish(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderUsageKeyV2", "call", "attempt"), self.usage(101, 7))])
        result = self.worker.call(E("Race", str(self.path), "counts-changed", command, other))
        self.same(result[2], E("StoreRefused", S("conflict")))
        self.same(self.answer(command), E("ProviderCompletedV2", S("ProviderUsageConflictV2")))

    def test_an_unrelated_call_does_not_block_completion(self):
        command = self.finish(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderLeaseKeyV2", "other-call"),
                                    E("ProviderLeaseV2", "other-attempt", S("ProviderLeaseActiveV2")))])
        result = self.worker.call(E("Race", str(self.path), "independent", command, other))
        self.assertEqual([value[0] for value in result[1:]], [S("KnowledgeCommittedV1")] * 2, result)
        self.same(result[2][1], E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")))

    def test_current_counts_invalidate_a_stale_usage_merge(self):
        command = self.record(self.usage(100, 7))
        other = self.application([E("KnowledgePutV1", E("ProviderUsageKeyV2", "call", "attempt"), self.usage(100, None, 80))])
        result = self.worker.call(E("Race", str(self.path), "usage", command, other))
        self.same(result[2], E("StoreRefused", S("conflict")))
        self.answer(command)
        self.same(self.value(E("ProviderUsageKeyV2", "call", "attempt")), E("KnowledgePresentV1", self.usage(100, 7, 80)))

    def test_result_and_receipt_survive_restart_with_the_production_import_policy(self):
        command = self.finish(self.usage(100, 7))
        self.answer(command, "completed")
        self.assertEqual(self.worker.close(), 0)
        self.worker = self.make_worker(import_mode="ancestor-walk")
        self.same(self.execute(command, "completed"), E("KnowledgeReplayedV1", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))))
        result = self.knowledge(E("KnowledgeResolveV1", "completed", command, 1048576))
        self.same(result, E("KnowledgeResolvedV1", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")), self.epoch, 2))


if __name__ == "__main__":
    unittest.main()
