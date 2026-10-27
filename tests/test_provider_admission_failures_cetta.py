"""Native admission remains local across failed writes and worker death."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_provider_admission_cetta as fixture
ROOT, WORKER = fixture.ROOT, fixture.WORKER
if WORKER:
    from space_service_client import E, S, encode
    from value_worker_client import WorkerUnavailable


@unittest.skipUnless(WORKER and shutil.which("cc"), "native worker and C compiler required")
class ProviderAdmissionFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="admission-fault-build-")
        cls.addClassCleanup(cls.build.cleanup)
        cls.shim = Path(cls.build.name) / "knowledge-fault.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-O2",
                        str(ROOT / "tests/support/knowledge_store_fault.c"), "-ldl", "-o", str(cls.shim)],
                       check=True, capture_output=True, text=True)

    def setUp(self):
        self.f = fixture.ProviderAdmissionTests()
        self.f.setUp()
        self.addCleanup(lambda: self.assertTrue(self.f.doCleanups()))
        self.assertEqual(self.f.worker.close(), 0)
        self.flag = self.f.directory / "fault"
        self.restart()

    def restart(self):
        self.f.worker = self.f.make_worker(LD_PRELOAD=str(self.shim),
                                          GODELCLAW_TEST_STORE_FAULT=str(self.flag))

    def resolve(self, command):
        return self.f.knowledge(E("KnowledgeResolveV1", "admit", command, 1048576))

    def test_failed_prepublication_write_retains_no_admission_prefix_or_receipt(self):
        command = self.f.admit()
        before = encode(self.f.audit())
        self.flag.write_text("file")
        self.f.same(self.f.execute(command, "admit"), E("StoreRefused", S("io")))
        self.assertFalse(self.flag.exists(), "fault did not reach the publication seam")
        self.assertEqual(encode(self.f.audit()), before)
        self.f.same(self.resolve(command), E("KnowledgeUnrecordedV1", self.f.epoch, 0))
        self.f.same(self.f.answer(command, "admit"), E("ProviderAdmittedV2", "call", "owner"))
        self.assertEqual(len(self.f.audit()), 4)

    def test_landed_unknown_write_resolves_without_granting_another_first_admission(self):
        command = self.f.admit()
        self.flag.write_text("directory")
        self.f.same(self.f.execute(command, "admit"), E("StoreUncertain"))
        self.assertFalse(self.flag.exists(), "fault did not reach the publication seam")
        resolved = self.resolve(command)
        expected = E("ProviderAdmittedV2", "call", "owner")
        self.f.same(resolved, E("KnowledgeResolvedV1", expected, self.f.epoch, 1))
        self.f.same(self.f.worker.call(E("FirstAdmission", resolved)), S("ProviderNoNewAdmissionV2"))
        before = encode(self.f.audit())
        replay = self.f.execute(command, "admit")
        self.f.same(replay, E("KnowledgeReplayedV1", expected))
        self.f.same(self.f.worker.call(E("FirstAdmission", replay)), S("ProviderNoNewAdmissionV2"))
        self.assertEqual(encode(self.f.audit()), before)
        self.f.same(self.f.value(E("ProviderAttemptKeyV2", "call", "owner")), S("KnowledgeAbsentV1"))

    def test_crash_before_publication_keeps_the_request_unadmitted(self):
        command = self.f.admit()
        process = self.f.worker.process
        self.flag.write_text("crash-before")
        with self.assertRaises(WorkerUnavailable):
            self.f.execute(command, "admit")
        self.assertEqual(process.poll(), 87)
        self.assertFalse(self.flag.exists(), "fault did not reach the publication seam")
        self.f.worker.close()
        self.restart()
        self.assertEqual(self.f.audit(), ())
        self.f.same(self.resolve(command), E("KnowledgeUnrecordedV1", self.f.epoch, 0))

    def test_crash_after_publication_retains_all_three_facts_and_the_receipt(self):
        command = self.f.admit()
        process = self.f.worker.process
        self.flag.write_text("crash")
        with self.assertRaises(WorkerUnavailable):
            self.f.execute(command, "admit")
        self.assertEqual(process.poll(), 86)
        self.assertFalse(self.flag.exists(), "fault did not reach the publication seam")
        self.f.worker.close()
        self.restart()
        self.f.same(self.resolve(command), E("KnowledgeResolvedV1", E("ProviderAdmittedV2", "call", "owner"),
                                             self.f.epoch, 1))
        self.assertEqual(len(self.f.audit()), 4)
        self.f.same(self.f.value(E("ProviderAttemptKeyV2", "call", "owner")), S("KnowledgeAbsentV1"))

    def test_unavailable_readback_cannot_be_interpreted_as_an_absent_admission(self):
        command = self.f.admit()
        self.f.answer(command, "admit")
        self.flag.write_text("readback")
        result = self.resolve(command)
        self.assertFalse(self.flag.exists(), "fault did not reach the recovery seam")
        self.assertIn(result[0], (S("StoreRefused"), S("StoreUncertain")), result)
        self.f.same(self.resolve(command), E("KnowledgeResolvedV1", E("ProviderAdmittedV2", "call", "owner"),
                                             self.f.epoch, 1))


if __name__ == "__main__":
    unittest.main()
