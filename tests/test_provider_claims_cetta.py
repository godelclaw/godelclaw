"""Native transport scheduling and dispatch intent without provider I/O."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_provider_admission_cetta as admission
ROOT, WORKER, CETTA_ROOT = admission.ROOT, admission.WORKER, admission.CETTA_ROOT
if WORKER:
    from value_worker_client import Worker
    from space_service_client import E, S, encode


@unittest.skipUnless(WORKER, "native storage/math worker required")
class ProviderClaimsTests(unittest.TestCase):
    same = admission.ProviderAdmissionTests.same
    integer = staticmethod(admission.ProviderAdmissionTests.integer)
    admit_command = admission.ProviderAdmissionTests.admit
    knowledge = admission.ProviderAdmissionTests.knowledge
    application = admission.ProviderAdmissionTests.application
    put = admission.ProviderAdmissionTests.put
    value = admission.ProviderAdmissionTests.value
    audit = admission.ProviderAdmissionTests.audit

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-provider-claims-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "store"
        self.path.mkdir(mode=0o700)
        self.sequence = 0
        self.worker = self.make_worker()
        self.addCleanup(lambda: self.worker.close())
        result = self.worker.call(E("Create", str(self.path)))
        self.assertEqual(result[0], S("KnowledgeReadyV1"), result)
        self.epoch = result[1]
        self.policy("pool", 2, 1)

    def make_worker(self, **kwargs):
        environment = {"HOME": str(self.directory), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        environment.update(kwargs)
        with patch.dict(os.environ, environment, clear=True):
            return Worker(WORKER, ROOT / "tests/support/provider_claims_probe.metta",
                          "provider-claims-probe:run", language="petta", import_mode="relative")

    def policy(self, scope, capacity, reserve):
        self.put([(E("ProviderSchedulerPolicyKeyV3", scope), E("ProviderSchedulerPolicyV3", capacity, reserve))])

    @staticmethod
    def activity(name="activity", lane="ProviderInteractiveV3"):
        return S("ProviderExclusiveActivityV3") if name is None else E("ProviderNamedActivityV3", name, S(lane))

    def register(self, call="call", owner="owner"):
        self.sequence += 1
        result = self.worker.call(E("Admit", str(self.path), E("Admission", self.sequence),
                                    self.admit_command(call=call, token=owner)))
        self.assertEqual(result[0], S("KnowledgeCommittedV1"), result)
        return result

    def claim(self, call="call", *, scope="pool", owner="owner", process="process", name="activity", lane="ProviderInteractiveV3"):
        return E("ProviderAcquireV3", scope, call, owner, process, self.activity(name, lane))

    def intent(self, call="call", *, owner="owner", process="process", transport="transport"):
        return E("ProviderBeginDispatchV3", call, owner, process, transport)

    def execute(self, command, receipt=None):
        self.sequence += 1
        return self.worker.call(E("At", str(self.path), E("ClaimCommand", self.sequence) if receipt is None else receipt, command))

    def answer(self, command, receipt=None):
        result = self.execute(command, receipt)
        self.assertIn(result[0], (S("KnowledgeCommittedV1"), S("KnowledgeReplayedV1")), result)
        return result[1]

    def test_acquisition_commits_frontier_index_claim_and_receipt(self):
        self.register()
        command = self.claim()
        self.same(self.answer(command, "claim"), E("ProviderClaimAcquiredV3", "call"))
        entry = E("ProviderActiveClaimV3", "call", "owner", "process", self.activity())
        self.same(self.value(E("ProviderActiveClaimsKeyV3", "pool")),
                  E("KnowledgePresentV1", E("ProviderActiveClaimsV3", (entry,))))
        self.same(self.value(E("ProviderActiveClaimKeyV3", "pool", "call")), E("KnowledgePresentV1", entry))
        self.same(self.value(E("ProviderClaimKeyV3", "call")), E("KnowledgePresentV1",
                  E("ProviderClaimV3", "pool", "owner", "process", self.activity(), S("ProviderClaimReservedV3"))))
        self.same(self.value(E("ProviderAttemptKeyV2", "call", "owner")), S("KnowledgeAbsentV1"))
        before = encode(self.audit())
        self.same(self.execute(command, "claim"), E("KnowledgeReplayedV1", E("ProviderClaimAcquiredV3", "call")))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.answer(command), E("ProviderClaimAlreadyOwnedV3", "call", S("ProviderClaimReservedV3")))

    def test_background_leaves_an_interactive_slot_and_total_capacity_holds(self):
        for call in ("a", "b", "c", "d"):
            self.register(call)
        self.same(self.answer(self.claim("a", name="A", lane="ProviderBackgroundV3")), E("ProviderClaimAcquiredV3", "a"))
        self.same(self.answer(self.claim("b", name="B", lane="ProviderBackgroundV3")), E("ProviderClaimBusyV3", "b"))
        self.same(self.answer(self.claim("c", name="C")), E("ProviderClaimAcquiredV3", "c"))
        self.same(self.answer(self.claim("d", name="D")), E("ProviderClaimBusyV3", "d"))

    def test_same_activity_and_unnamed_activity_are_exclusive(self):
        for call in ("a", "b", "c"):
            self.register(call)
        self.answer(self.claim("a", name="same"))
        self.same(self.answer(self.claim("b", name="same")), E("ProviderClaimBusyV3", "b"))
        self.same(self.answer(self.claim("c", name=None)), E("ProviderClaimBusyV3", "c"))
        self.policy("other", 2, 1)
        self.same(self.answer(self.claim("b", scope="other", name=None)), E("ProviderClaimAcquiredV3", "b"))
        self.same(self.answer(self.claim("c", scope="other", name="C")), E("ProviderClaimBusyV3", "c"))

    def test_no_arbitrary_capacity_or_identifier_length_ceiling(self):
        self.policy("pool", 32, 1)
        for number in range(20):
            call = "call-" + str(number)
            self.register(call)
            self.same(self.answer(self.claim(call, name=("long-activity-" * 10) + str(number))),
                      E("ProviderClaimAcquiredV3", call))
        self.assertEqual(len(self.value(E("ProviderActiveClaimsKeyV3", "pool"))[1][1]), 20)

    def test_same_pool_races_conflict_but_separate_pools_commute(self):
        self.register("a")
        self.register("b")
        result = self.worker.call(E("Race", str(self.path), "a", self.claim("a", name="A"), "b", self.claim("b", name="B")))
        self.same(result[1][1], E("ProviderClaimAcquiredV3", "a"))
        self.same(result[2], E("StoreRefused", S("conflict")))
        self.policy("other", 2, 1)
        self.register("c")
        result = self.worker.call(E("Race", str(self.path), "b-new", self.claim("b", name="B"),
                                    "c", self.claim("c", scope="other", name="C")))
        self.assertEqual([x[0] for x in result[1:]], [S("KnowledgeCommittedV1")] * 2, result)

    def test_dispatch_registers_an_intent_without_external_io_and_never_replaces_it(self):
        self.register()
        self.answer(self.claim())
        command = self.intent()
        self.same(self.answer(command, "intent"), E("ProviderIntentRegisteredV3", "call", "transport"))
        self.same(self.value(E("ProviderDispatchKeyV3", "call")), E("KnowledgePresentV1",
                  E("ProviderDispatchIntentV3", "call", "owner", "process", "transport")))
        self.same(self.value(E("ProviderAttemptKeyV2", "call", "owner")),
                  E("KnowledgePresentV1", E("ProviderSubmittedAttemptV2", "transport")))
        before = encode(self.audit())
        self.same(self.execute(command, "intent"), E("KnowledgeReplayedV1", E("ProviderIntentRegisteredV3", "call", "transport")))
        self.assertEqual(encode(self.audit()), before)
        self.same(self.answer(command), E("ProviderIntentAlreadyRegisteredV3", "call", "transport"))
        self.same(self.answer(self.intent(transport="other")), E("ProviderClaimRefusedV3", S("dispatch-identity-conflict")))

    def test_nonowners_and_missing_indexes_cannot_register_dispatch(self):
        self.register()
        self.same(self.answer(self.intent()), E("ProviderClaimRefusedV3", S("reserved-claim-required")))
        self.answer(self.claim())
        self.same(self.answer(self.intent(process="reused-pid")), E("ProviderClaimRefusedV3", S("not-owned")))
        self.put([(E("ProviderActiveClaimKeyV3", "pool", "call"), S("corrupt"))])
        self.same(self.answer(self.intent()), E("ProviderClaimRefusedV3", S("active-claim-index")))
        self.same(self.value(E("ProviderDispatchKeyV3", "call")), S("KnowledgeAbsentV1"))

    def test_dispatch_registration_for_different_calls_has_no_global_false_conflict(self):
        self.register("a")
        self.register("b")
        self.answer(self.claim("a", name="A"))
        self.answer(self.claim("b", name="B"))
        result = self.worker.call(E("Race", str(self.path), "a-intent", self.intent("a"), "b-intent", self.intent("b")))
        self.assertEqual([x[0] for x in result[1:]], [S("KnowledgeCommittedV1")] * 2, result)

    def test_invalid_policy_and_corrupt_frontier_are_not_empty_capacity(self):
        self.register()
        self.policy("pool", 1, 1)
        self.same(self.answer(self.claim()), E("ProviderClaimRefusedV3", S("scheduler-policy-format")))
        self.policy("pool", 2, 1)
        self.put([(E("ProviderActiveClaimsKeyV3", "pool"), E("ProviderActiveClaimsV3", (S("bad"),)))])
        self.same(self.answer(self.claim()), E("ProviderClaimRefusedV3", S("frontier-format")))


if __name__ == "__main__":
    unittest.main()
