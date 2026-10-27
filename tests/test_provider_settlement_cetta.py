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


if __name__ == "__main__":
    unittest.main()
