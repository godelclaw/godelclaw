"""Actual native publication failures, crash and receipt recovery.

Faults affect only a disposable worker and an isolated store. No provider
request or application database is used by these tests.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_provider_domain_cetta as domain_fixture
ROOT = domain_fixture.ROOT
WORKER = domain_fixture.WORKER
if WORKER:
    from space_service_client import E, S, encode
    from value_worker_client import WorkerUnavailable


@unittest.skipUnless(WORKER and shutil.which("cc"), "native worker and C compiler required")
class ProviderDomainFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="knowledge-fault-build-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.shim = Path(cls.build.name) / "knowledge-fault.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-O2",
                        str(ROOT / "tests/support/knowledge_store_fault.c"), "-ldl", "-o", str(cls.shim)],
                       check=True, capture_output=True, text=True)

    def setUp(self):
        self.fixture = domain_fixture.ProviderDomainTests()
        self.fixture.setUp()
        self.addCleanup(lambda: self.assertTrue(self.fixture.doCleanups()))
        self.assertEqual(self.fixture.worker.close(), 0)
        self.flag = self.fixture.directory / "fault"
        self.restart()

    def restart(self):
        self.fixture.worker = self.fixture.make_worker(
            LD_PRELOAD=str(self.shim), GODELCLAW_TEST_STORE_FAULT=str(self.flag))

    def resolve(self, command):
        return self.fixture.knowledge(E("KnowledgeResolveV1", "completion", command, 1048576))

    def state(self):
        return self.fixture.values([E("ProviderLeaseKeyV2", "call"), E("ProviderAnswerKeyV2", "call"),
                                    self.fixture.usage_key()])

    def test_failed_prepublication_write_leaves_all_facts_and_receipt_unpublished(self):
        f = self.fixture
        command = f.finish(f.usage(100, 7))
        before = encode(self.state())
        self.flag.write_text("file")
        result = f.execute(command, "completion")
        self.assertEqual(result, E("StoreRefused", S("io")), result)
        self.assertFalse(self.flag.exists(), "fault seam was not reached")
        self.assertEqual(encode(self.state()), before)
        self.assertEqual(self.resolve(command), E("KnowledgeUnrecordedV1", f.epoch, 1))
        result = f.execute(command, "completion")
        self.assertEqual(result[0], S("KnowledgeCommittedV1"), result)
        self.assertEqual(result[-1], 2)
        self.assertEqual(self.resolve(command),
                         E("KnowledgeResolvedV1", E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")), f.epoch, 2))

    def test_landed_commit_with_lost_ack_is_resolved_and_never_applied_twice(self):
        f = self.fixture
        command = f.finish(f.usage(100, 7))
        self.flag.write_text("directory")
        self.assertEqual(f.execute(command, "completion"), E("StoreUncertain"))
        self.assertFalse(self.flag.exists(), "fault seam was not reached")
        expected = E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))
        self.assertEqual(self.resolve(command), E("KnowledgeResolvedV1", expected, f.epoch, 2))
        before = encode(f.audit())
        self.assertEqual(f.execute(command, "completion"), E("KnowledgeReplayedV1", expected))
        self.assertEqual(encode(f.audit()), before)
        self.assertEqual(f.knowledge(S("KnowledgeProbeRevisionV1")), E("KnowledgeRevisionV1", f.epoch, 2))

    def test_crash_after_durable_publication_is_discovered_by_a_new_worker(self):
        f = self.fixture
        command = f.finish(f.usage(100, 7))
        process = f.worker.process
        self.flag.write_text("crash")
        with self.assertRaises(WorkerUnavailable):
            f.execute(command, "completion")
        self.assertEqual(process.poll(), 86)
        self.assertFalse(self.flag.exists(), "fault seam was not reached")
        f.worker.close()
        self.restart()
        expected = E("ProviderCompletedV2", S("ProviderUsageAcceptedV2"))
        self.assertEqual(self.resolve(command), E("KnowledgeResolvedV1", expected, f.epoch, 2))
        self.assertEqual(f.execute(command, "completion"), E("KnowledgeReplayedV1", expected))
        self.assertEqual(self.state()[1], E("KnowledgePresentV1", E("ProviderAnswerV2", E("Transport", "call", "attempt"), E("AnswerRef", "answer"))))

    def test_crash_before_publication_exposes_no_partial_completion(self):
        f = self.fixture
        command = f.finish(f.usage(100, 7))
        before = encode(self.state())
        process = f.worker.process
        self.flag.write_text("crash-before")
        with self.assertRaises(WorkerUnavailable):
            f.execute(command, "completion")
        self.assertEqual(process.poll(), 87)
        f.worker.close()
        self.restart()
        self.assertEqual(encode(self.state()), before)
        self.assertEqual(self.resolve(command), E("KnowledgeUnrecordedV1", f.epoch, 1))
        self.assertEqual(f.execute(command, "completion")[0], S("KnowledgeCommittedV1"))

    def test_failed_recovery_is_not_a_missing_receipt_or_replay_authority(self):
        f = self.fixture
        command = f.finish(f.usage(100, 7))
        f.execute(command, "completion")
        self.flag.write_text("readback")
        result = self.resolve(command)
        self.assertFalse(self.flag.exists(), "fault seam was not reached")
        self.assertIn(result[0], (S("StoreRefused"), S("StoreUncertain")), result)
        self.assertEqual(self.resolve(command), E("KnowledgeResolvedV1",
                         E("ProviderCompletedV2", S("ProviderUsageAcceptedV2")), f.epoch, 2))


if __name__ == "__main__":
    unittest.main()
