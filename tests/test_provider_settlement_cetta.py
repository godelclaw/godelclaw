"""Atomic local settlement/recovery of native transport claims."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_provider_claims_cetta as claims
import test_provider_domain_cetta as domain
if claims.WORKER:
    from space_service_client import E, S, encode
    from value_worker_client import WorkerUnavailable


@unittest.skipUnless(claims.WORKER, "native storage/math worker required")
class SettlementFixture(unittest.TestCase):
    setUp = claims.ProviderClaimsTests.setUp
    make_worker = claims.ProviderClaimsTests.make_worker
    same = claims.ProviderClaimsTests.same
    integer = staticmethod(claims.ProviderClaimsTests.integer)
    admit_command = claims.ProviderClaimsTests.admit_command
    knowledge = claims.ProviderClaimsTests.knowledge
    application = claims.ProviderClaimsTests.application
    put = claims.ProviderClaimsTests.put
    value = claims.ProviderClaimsTests.value
    audit = claims.ProviderClaimsTests.audit
    policy = claims.ProviderClaimsTests.policy
    activity = staticmethod(claims.ProviderClaimsTests.activity)
    register = claims.ProviderClaimsTests.register
    claim = claims.ProviderClaimsTests.claim
    intent = claims.ProviderClaimsTests.intent
    execute = claims.ProviderClaimsTests.execute
    answer = claims.ProviderClaimsTests.answer
    usage = staticmethod(domain.ProviderDomainTests.usage)

    def ready(self, *, dispatch=True):
        self.register()
        self.answer(self.claim())
        if dispatch:
            self.answer(self.intent())

    def settle(self, *, process="process", transport="transport", usage=None, reference="answer"):
        return E("ProviderSettleV3", "call", "owner", process, transport,
                 E("AnswerRef", reference), self.usage(100, 7) if usage is None else usage, "evidence")

    def recover(self, observation="ProviderProcessExitedV3", *, process="process"):
        return E("ProviderRecoverClaimV3", "call", "owner", process, S(observation), "process-evidence")

    def abandon(self, *, process="process"):
        return E("ProviderAbandonClaimV3", "call", "owner", process, "cancel-evidence")

    def end_transport(self, outcome="ProviderFailureKnownV3", *, process="process",
                      transport="transport", usage=None):
        return E("ProviderEndTransportV3", "call", "owner", process,
                 E("ProviderTransportClosedV3", transport, S(outcome)),
                 S("ProviderNoUsageV3") if usage is None else usage, "closure-evidence")

    def settlement(self, command, receipt=None):
        self.sequence += 1
        return self.worker.call(E("Settlement", str(self.path),
                                  E("Settle", self.sequence) if receipt is None else receipt, command))

    def result(self, command, receipt=None):
        answer = self.settlement(command, receipt)
        self.assertIn(answer[0], (S("KnowledgeCommittedV1"), S("KnowledgeReplayedV1")), answer)
        return answer[1]

    def active(self):
        return self.value(E("ProviderActiveClaimsKeyV3", "pool"))

    def assert_released(self, phase):
        self.same(self.active(), E("KnowledgePresentV1", E("ProviderActiveClaimsV3", ())))
        self.same(self.value(E("ProviderActiveClaimKeyV3", "pool", "call")), S("KnowledgeAbsentV1"))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "owner", S(phase))))


class ProviderTerminalTests(SettlementFixture):
    def test_live_reservation_can_be_abandoned_without_a_death_report(self):
        self.ready(dispatch=False)
        self.same(self.result(self.recover("ProviderProcessLiveV3")),
                  E("ProviderClaimRetainedV3", "call", S("ProviderClaimReservedV3")))
        self.same(self.result(self.abandon(), "abandoned"), E("ProviderClaimReleasedUnsentV3", "call"))
        self.assert_released("ProviderLeaseFailedV2")
        for key in (E("ProviderDispatchKeyV3", "call"), E("ProviderAttemptKeyV2", "call", "owner"),
                    E("ProviderAnswerKeyV2", "call")):
            self.same(self.value(key), S("KnowledgeAbsentV1"))
        before = encode(self.audit())
        self.assertEqual(self.settlement(self.abandon(), "abandoned")[0], S("KnowledgeReplayedV1"))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.result(self.abandon()),
                  E("ProviderClaimAlreadyEndedV3", "call", S("ProviderClaimUnsentV3"), S("ProviderUsageUnreportedV3")))

    def test_abandonment_after_dispatch_is_refused_without_releasing(self):
        self.ready()
        before = encode(self.active())
        self.same(self.result(self.abandon()), E("ProviderSettlementRefusedV3", S("unsent-claim-required")))
        self.assertEqual(encode(self.active()), before)
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "owner", S("ProviderLeaseActiveV2"))))

    def test_stopped_failed_transport_records_usage_and_releases_in_one_commit(self):
        self.ready()
        command = self.end_transport(usage=self.usage(100, 7))
        expected = E("ProviderClaimEndedTransportV3", "call", "transport", S("ProviderClaimFailedV3"),
                     S("ProviderUsageAcceptedV2"))
        self.same(self.result(command, "failed"), expected)
        self.assert_released("ProviderLeaseFailedV2")
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")),
                  E("KnowledgePresentV1", self.usage(100, 7)))
        self.assertIn(E("KnowledgeReceiptV1", "failed", command, expected), self.audit())
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))
        self.same(self.answer(self.intent()), E("ProviderIntentAlreadyRegisteredV3", "call", "transport"))
        self.register("next")
        self.same(self.answer(self.claim("next")), E("ProviderClaimAcquiredV3", "next"))
        before = encode(self.active())
        self.same(self.result(command), E("ProviderClaimAlreadyEndedV3", "call", S("ProviderClaimFailedV3"),
                                         S("ProviderUsageAcceptedV2")))
        self.assertEqual(encode(self.active()), before)

    def test_closed_unknown_outcome_preserves_intent_and_has_no_invented_usage(self):
        self.ready()
        self.same(self.result(self.end_transport("ProviderFailureUnknownV3")),
                  E("ProviderClaimEndedTransportV3", "call", "transport", S("ProviderClaimUnknownV3"),
                    S("ProviderUsageUnreportedV3")))
        self.assert_released("ProviderLeaseUnknownV2")
        for key in (E("ProviderUsageKeyV2", "call", "transport"), E("ProviderAnswerKeyV2", "call")):
            self.same(self.value(key), S("KnowledgeAbsentV1"))
        self.same(self.value(E("ProviderDispatchKeyV3", "call")),
                  E("KnowledgePresentV1", E("ProviderDispatchIntentV3", "call", "owner", "process", "transport")))

    def test_running_or_unavailable_transport_is_not_a_closure(self):
        self.ready()
        before = encode(self.active())
        for observation in (E("ProviderTransportRunningV3", "transport"),
                            E("ProviderTransportUnavailableV3", "transport"),
                            E("ProviderTransportClosedV3", "transport", S("unknown-kind"))):
            command = E("ProviderEndTransportV3", "call", "owner", "process", observation,
                        S("ProviderNoUsageV3"), "evidence")
            self.same(self.settlement(command), E("ProviderSettlementRefusedV3", S("closed-transport-observation")))
        self.assertEqual(encode(self.active()), before)

    def test_wrong_transport_or_incarnation_does_not_release(self):
        self.ready()
        before = encode(self.active())
        for command in (self.end_transport(process="other"), self.end_transport(transport="other")):
            self.same(self.result(command), E("ProviderSettlementRefusedV3", S("registered-dispatch-required")))
        self.assertEqual(encode(self.active()), before)

    def test_physical_closure_preserves_successors_logical_lease(self):
        self.ready()
        successor = E("ProviderLeaseV2", "successor", S("ProviderLeaseActiveV2"))
        self.put([(E("ProviderLeaseKeyV2", "call"), successor)])
        self.result(self.end_transport(usage=self.usage(100, 7)))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")), E("KnowledgePresentV1", successor))
        self.same(self.active(), E("KnowledgePresentV1", E("ProviderActiveClaimsV3", ())))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")),
                  E("KnowledgePresentV1", self.usage(100, 7)))

    def test_abandonment_preserves_a_successors_lease(self):
        self.ready(dispatch=False)
        successor = E("ProviderLeaseV2", "successor", S("ProviderLeaseActiveV2"))
        self.put([(E("ProviderLeaseKeyV2", "call"), successor)])
        self.same(self.result(self.abandon()), E("ProviderClaimReleasedUnsentV3", "call"))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")), E("KnowledgePresentV1", successor))
        self.same(self.active(), E("KnowledgePresentV1", E("ProviderActiveClaimsV3", ())))

    def test_late_closure_keeps_a_completed_answer_and_can_enrich_usage(self):
        self.ready()
        self.result(self.settle())
        self.same(self.result(self.end_transport(usage=self.usage(100, 7))),
                  E("ProviderClaimAlreadyEndedV3", "call", S("ProviderClaimCompletedV3"), S("ProviderUsageAcceptedV2")))
        self.assert_released("ProviderLeaseCompletedV2")
        self.same(self.value(E("ProviderAnswerKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderAnswerV2", "transport", E("AnswerRef", "answer"))))

    def test_abandonment_and_dispatch_race_cannot_both_publish(self):
        self.ready(dispatch=False)
        reply = self.worker.call(E("LifecycleRace", str(self.path), "abandon", self.abandon(),
                                   "dispatch", self.intent()))
        self.assertEqual(reply[1][0], S("KnowledgeCommittedV1"), reply)
        self.same(reply[2], E("StoreRefused", S("conflict")))
        self.assert_released("ProviderLeaseFailedV2")
        self.same(self.value(E("ProviderDispatchKeyV3", "call")), S("KnowledgeAbsentV1"))

    def test_dispatch_wins_the_captured_view_race_against_abandonment(self):
        self.ready(dispatch=False)
        reply = self.worker.call(E("LifecycleRace", str(self.path), "dispatch", self.intent(),
                                   "abandon", self.abandon()))
        self.assertEqual(reply[1][0], S("KnowledgeCommittedV1"), reply)
        self.same(reply[2], E("StoreRefused", S("conflict")))
        self.assertEqual(len(self.active()[1][1]), 1)

    def test_terminal_failure_and_completion_conflict_on_one_captured_view(self):
        self.ready()
        reply = self.worker.call(E("LifecycleRace", str(self.path), "failed", self.end_transport(),
                                   "finished", self.settle()))
        self.assertEqual(reply[1][0], S("KnowledgeCommittedV1"), reply)
        self.same(reply[2], E("StoreRefused", S("conflict")))
        self.assert_released("ProviderLeaseFailedV2")
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_completion_wins_the_captured_view_race_against_failure(self):
        self.ready()
        reply = self.worker.call(E("LifecycleRace", str(self.path), "finished", self.settle(),
                                   "failed", self.end_transport()))
        self.assertEqual(reply[1][0], S("KnowledgeCommittedV1"), reply)
        self.same(reply[2], E("StoreRefused", S("conflict")))
        self.assert_released("ProviderLeaseCompletedV2")

    def test_conflicting_usage_is_retained_in_receipt_without_replacing_counts(self):
        self.ready()
        self.put([(E("ProviderUsageKeyV2", "call", "transport"), self.usage(99, 7))])
        command = self.end_transport(usage=self.usage(100, 7))
        answer = E("ProviderClaimEndedTransportV3", "call", "transport", S("ProviderClaimFailedV3"),
                   S("ProviderUsageConflictV2"))
        self.same(self.result(command, "disputed-failure"), answer)
        self.assert_released("ProviderLeaseFailedV2")
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")),
                  E("KnowledgePresentV1", self.usage(99, 7)))
        self.assertIn(E("KnowledgeReceiptV1", "disputed-failure", command, answer), self.audit())

    def test_inconsistent_index_does_not_publish_usage_or_half_release(self):
        self.ready()
        self.put([(E("ProviderActiveClaimKeyV3", "pool", "call"),
                   E("ProviderActiveClaimV3", "different-call", "owner", "process", self.activity()))])
        self.same(self.result(self.end_transport(usage=self.usage(100, 7))),
                  E("ProviderSettlementRefusedV3", S("active-claim-index")))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")), S("KnowledgeAbsentV1"))


class ProviderSettlementTests(SettlementFixture):
    def test_completion_publishes_answer_usage_release_terminal_lease_and_receipt(self):
        self.ready()
        command = self.settle()
        expected = E("ProviderSettlementV3", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")))
        self.same(self.result(command, "finished"), expected)
        self.assert_released("ProviderLeaseCompletedV2")
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")), E("KnowledgePresentV1", self.usage(100, 7)))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), E("KnowledgePresentV1",
                  E("ProviderAnswerV2", "transport", E("AnswerRef", "answer"))))
        self.assertIn(E("KnowledgeReceiptV1", "finished", command, expected), self.audit())
        before = encode(self.audit())
        self.same(self.settlement(command, "finished"), E("KnowledgeReplayedV1", expected))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.result(command), E("ProviderSettlementV3", E("ProviderAlreadyCompletedV2", S("ProviderUsageAcceptedV2"))))
        self.register("next")
        self.same(self.answer(self.claim("next")), E("ProviderClaimAcquiredV3", "next"))

    def test_known_exit_before_intent_releases_unsent_work_without_an_attempt(self):
        self.ready(dispatch=False)
        self.same(self.result(self.recover()), E("ProviderClaimReleasedUnsentV3", "call"))
        self.assert_released("ProviderLeaseFailedV2")
        for key in (E("ProviderAttemptKeyV2", "call", "owner"), E("ProviderDispatchKeyV3", "call"), E("ProviderAnswerKeyV2", "call")):
            self.same(self.value(key), S("KnowledgeAbsentV1"))
        self.same(self.answer(self.intent()), E("ProviderClaimRefusedV3", S("reserved-claim-required")))

    def test_known_exit_after_intent_preserves_uncertainty_and_accepts_late_usage_only(self):
        self.ready()
        self.same(self.result(self.recover()), E("ProviderClaimRecoveredUnknownV3", "call", "transport"))
        self.assert_released("ProviderLeaseUnknownV2")
        self.same(self.value(E("ProviderDispatchKeyV3", "call")), E("KnowledgePresentV1",
                  E("ProviderDispatchIntentV3", "call", "owner", "process", "transport")))
        self.same(self.result(self.settle()), E("ProviderSettlementV3", E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2"))))
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")), E("KnowledgePresentV1", self.usage(100, 7)))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))
        self.same(self.answer(self.intent()), E("ProviderIntentAlreadyRegisteredV3", "call", "transport"))

    def test_live_and_unavailable_observations_preserve_ownership_and_capacity(self):
        self.ready()
        before = encode(self.active())
        for observation in ("ProviderProcessLiveV3", "ProviderProcessUnavailableV3"):
            self.same(self.result(self.recover(observation)), E("ProviderClaimRetainedV3", "call", E("ProviderClaimDispatchV3", "transport")))
            self.assertEqual(encode(self.active()), before)
        self.same(self.result(self.recover(process="same-pid-new-incarnation")), E("ProviderSettlementRefusedV3", S("not-owned")))
        self.assertEqual(encode(self.active()), before)

    def test_wrong_process_or_submission_does_not_settle_or_release(self):
        self.ready()
        before = encode(self.active())
        for command in (self.settle(process="other"), self.settle(transport="other")):
            self.same(self.result(command), E("ProviderSettlementRefusedV3", S("registered-dispatch-required")))
        self.assertEqual(encode(self.active()), before)
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))

    def test_malformed_frontier_cannot_publish_half_a_completion(self):
        self.ready()
        self.put([(E("ProviderActiveClaimsKeyV3", "pool"), E("ProviderActiveClaimsV3", (S("bad"),)))])
        self.same(self.result(self.settle()), E("ProviderSettlementRefusedV3", S("frontier-format")))
        for key in (E("ProviderAnswerKeyV2", "call"), E("ProviderUsageKeyV2", "call", "transport")):
            self.same(self.value(key), S("KnowledgeAbsentV1"))

    def test_intent_and_attempt_must_identify_the_same_physical_submission(self):
        self.ready()
        self.put([(E("ProviderAttemptKeyV2", "call", "owner"), E("ProviderSubmittedAttemptV2", "different"))])
        self.same(self.result(self.settle()), E("ProviderSettlementRefusedV3", S("registered-dispatch-required")))
        for key in (E("ProviderAnswerKeyV2", "call"), E("ProviderUsageKeyV2", "call", "different")):
            self.same(self.value(key), S("KnowledgeAbsentV1"))
        self.assertEqual(len(self.active()[1][1]), 1)

    def test_older_completion_entry_cannot_bypass_claim_release(self):
        self.ready()
        command = E("ProviderFinishV2", "call", "owner", E("AnswerRef", "answer"), self.usage(100, 7), "evidence")
        result = self.worker.call(E("LegacyFinish", str(self.path), "legacy-finish", command))
        self.same(result[1], E("ProviderDomainRefusedV2", S("lifecycle-settlement-required")))
        self.same(self.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))
        self.same(self.value(E("ProviderLeaseKeyV2", "call")),
                  E("KnowledgePresentV1", E("ProviderLeaseV2", "owner", S("ProviderLeaseActiveV2"))))
        self.same(self.result(self.settle()), E("ProviderSettlementV3", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))))
        self.assert_released("ProviderLeaseCompletedV2")


@unittest.skipUnless(claims.WORKER and shutil.which("cc"), "native worker and C compiler required")
class ProviderSettlementFailureTests(SettlementFixture):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="settlement-fault-build-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.shim = Path(cls.build.name) / "fault.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-O2",
                        str(claims.ROOT / "tests/support/knowledge_store_fault.c"), "-ldl", "-o", str(cls.shim)],
                       check=True, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        self.ready()
        self.flag = self.directory / "fault"
        self.worker.close()
        self.restart()

    def restart(self):
        self.worker = self.make_worker(LD_PRELOAD=str(self.shim), GODELCLAW_TEST_STORE_FAULT=str(self.flag))

    def test_failed_write_keeps_claim_and_answer_for_local_retry(self):
        before = encode(self.audit())
        command = self.settle()
        self.flag.write_text("file")
        self.same(self.settlement(command, "complete"), E("StoreRefused", S("io")))
        self.assertFalse(self.flag.exists())
        self.assertEqual(encode(self.audit()), before)
        self.same(self.result(command, "complete"), E("ProviderSettlementV3", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))))
        self.assert_released("ProviderLeaseCompletedV2")

    def test_unknown_ack_is_resolved_with_released_capacity(self):
        command = self.settle()
        self.flag.write_text("directory")
        self.same(self.settlement(command, "complete"), E("StoreUncertain"))
        self.assertFalse(self.flag.exists())
        result = self.knowledge(E("KnowledgeResolveV1", "complete", command, 1048576))
        self.assertEqual(result[0], S("KnowledgeResolvedV1"), result)
        self.assert_released("ProviderLeaseCompletedV2")
        before = encode(self.audit())
        self.assertEqual(self.settlement(command, "complete")[0], S("KnowledgeReplayedV1"))
        self.assertEqual(encode(self.audit()), before)

    def test_crash_after_commit_recovers_answer_and_release_together(self):
        command = self.settle()
        process = self.worker.process
        self.flag.write_text("crash")
        with self.assertRaises(WorkerUnavailable):
            self.settlement(command, "complete")
        self.assertEqual(process.poll(), 86)
        self.worker.close()
        self.restart()
        self.assertEqual(self.knowledge(E("KnowledgeResolveV1", "complete", command, 1048576))[0], S("KnowledgeResolvedV1"))
        self.assert_released("ProviderLeaseCompletedV2")

    def test_failed_terminal_write_keeps_capacity_and_usage_for_local_retry(self):
        before = encode(self.audit())
        command = self.end_transport(usage=self.usage(100, 7))
        self.flag.write_text("file")
        self.same(self.settlement(command, "terminal"), E("StoreRefused", S("io")))
        self.assertFalse(self.flag.exists())
        self.assertEqual(encode(self.audit()), before)
        self.assertEqual(len(self.active()[1][1]), 1)
        self.result(command, "terminal")
        self.assert_released("ProviderLeaseFailedV2")
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")),
                  E("KnowledgePresentV1", self.usage(100, 7)))

    def test_unknown_terminal_ack_resolves_usage_and_release_together(self):
        command = self.end_transport("ProviderFailureUnknownV3", usage=self.usage(100, 7))
        self.flag.write_text("directory")
        self.same(self.settlement(command, "terminal"), E("StoreUncertain"))
        resolved = self.knowledge(E("KnowledgeResolveV1", "terminal", command, 1048576))
        self.assertEqual(resolved[0], S("KnowledgeResolvedV1"), resolved)
        self.assert_released("ProviderLeaseUnknownV2")
        before = encode(self.audit())
        self.assertEqual(self.settlement(command, "terminal")[0], S("KnowledgeReplayedV1"))
        self.assertEqual(encode(self.audit()), before)

    def test_terminal_crash_after_commit_recovers_usage_and_release(self):
        command = self.end_transport(usage=self.usage(100, 7))
        process = self.worker.process
        self.flag.write_text("crash")
        with self.assertRaises(WorkerUnavailable):
            self.settlement(command, "terminal")
        self.assertEqual(process.poll(), 86)
        self.worker.close(); self.restart()
        resolved = self.knowledge(E("KnowledgeResolveV1", "terminal", command, 1048576))
        self.assertEqual(resolved[0], S("KnowledgeResolvedV1"), resolved)
        self.assert_released("ProviderLeaseFailedV2")

    def test_terminal_crash_before_durability_keeps_the_claim_for_local_retry(self):
        command = self.end_transport(usage=self.usage(100, 7))
        process = self.worker.process
        self.flag.write_text("crash-before")
        with self.assertRaises(WorkerUnavailable):
            self.settlement(command, "terminal")
        self.assertEqual(process.poll(), 87)
        self.worker.close(); self.restart()
        resolved = self.knowledge(E("KnowledgeResolveV1", "terminal", command, 1048576))
        self.assertEqual(resolved[0], S("KnowledgeUnrecordedV1"), resolved)
        self.assertEqual(len(self.active()[1][1]), 1)
        self.same(self.value(E("ProviderUsageKeyV2", "call", "transport")), S("KnowledgeAbsentV1"))
        self.result(command, "terminal")
        self.assert_released("ProviderLeaseFailedV2")


if __name__ == "__main__":
    unittest.main()
