"""Native provider checkpoint boundary; no active storage selection changes."""
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT.parent / "CeTTa-ours"))
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from space_service_client import E, S, encode
    from value_worker_client import Worker


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to a build with the native store library")
class ProviderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-store-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "native"
        self.path.mkdir(mode=0o700)
        with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                     "HOME": str(self.directory)}, clear=True):
            self.worker = Worker(WORKER, ROOT / "src/provider_store_cetta.metta",
                                 "provider-store:checkpoint-call", language="petta")
        self.addCleanup(self.worker.close)
        answer = self.worker.call(E("Create", str(self.path)))
        self.assertEqual(answer[0], S("ProviderStoreReady"), answer)
        self.assertEqual(answer[2], 0)
        self.epoch, self.revision = answer[1:]

    def same(self, actual, expected):
        self.assertEqual(encode(actual), encode(expected))

    def put(self, records):
        self.revision += 1
        self.same(self.worker.call(E("Import", str(self.path), tuple(records))),
                  E("ProviderStoreWritten", self.epoch, self.revision))

    def read(self, keys, *, epoch=None, revision=None):
        return self.worker.call(E("Read", str(self.path), self.epoch if epoch is None else epoch,
                                  self.revision if revision is None else revision, tuple(keys)))

    @staticmethod
    def key(prefix, ordinal):
        return prefix.encode() + struct.pack(">Q", (ordinal & ((1 << 64) - 1)) ^ (1 << 63))

    @staticmethod
    def request(ordinal, identity="request", output=None):
        null = S("JsonNull")
        return E("ProviderRequestV1", identity, ordinal, 1.25, 20, "unknown",
                 null if output is None else output, "fixture", "coding", null, "owner", "fingerprint")

    def test_nullable_identities_and_duplicate_payloads_keep_sqlite_occurrences(self):
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.execute("CREATE TABLE requests(id TEXT PRIMARY KEY,started REAL,reserved INTEGER,"
                         "status TEXT,output,provider TEXT,mode TEXT,finish,owner TEXT,fingerprint TEXT)")
        ordinals = [-(1 << 63), -1, 0, (1 << 63) - 1]
        for ordinal, identity in zip(ordinals, (None, None, "Unicode-λ\0", "literal (+ 2 3)")):
            database.execute("INSERT INTO requests(rowid,id,started,reserved,status,output,provider,"
                             "mode,finish,owner,fingerprint) VALUES(?,?,1.25,20,'unknown',NULL,"
                             "'fixture','coding',NULL,'owner','fingerprint')", (ordinal, identity))
        records = [E("ProviderRequestV1", S("JsonNull") if row[1] is None else row[1], row[0],
                     *(S("JsonNull") if item is None else item for item in row[2:]))
                   for row in database.execute("SELECT rowid,* FROM requests ORDER BY rowid")]
        self.put(records)
        keys = [self.key("requests/", ordinal) for ordinal in ordinals]
        self.same(self.read(keys), E("ProviderStoreRows", tuple(records)))
        self.same(self.read(keys[::-1]), E("ProviderStoreRows", tuple(records[::-1])))

    def test_assignments_apply_in_input_order_without_moving_identity(self):
        before, after = self.request(9, output="before"), self.request(9, output="after")
        self.put([before, after])
        self.same(self.read([self.key("requests/", 9)]), E("ProviderStoreRows", (after,)))

    def test_epoch_and_revision_both_guard_separately_read_batches(self):
        self.put([self.request(1)])
        self.same(self.read([], epoch="foreign-epoch"), E("ProviderStoreChanged", self.epoch, 1))
        self.same(self.read([], revision=0), E("ProviderStoreChanged", self.epoch, 1))
        self.same(self.read([]), E("ProviderStoreRows", ()))

    def test_malformed_batch_cannot_partially_import_its_valid_prefix(self):
        for malformed in (S("not-a-record"), (), E("UnknownRecord", 1),
                          E("ProviderRequestV1", "too-short")):
            with self.subTest(malformed=malformed):
                answer = self.worker.call(E("Import", str(self.path), (self.request(1), malformed)))
                self.assertEqual(answer[0], S("ProviderStoreRefused"), answer)
                self.same(self.read([self.key("requests/", 1)]), S("StoreAbsent"))
                self.same(self.read([]), E("ProviderStoreRows", ()))

    def test_frontier_and_event_sequences_are_distinct_from_record_positions(self):
        event = E("ProviderEventV1", "call", "usage", 17, 1.5, '{"known":false}')
        frontier = E("ProviderSequenceV1", "llm_call_history", 17)
        recovery = E("ProviderAdmissionRecoveryV1", "id", -2, 1.25, "same payload", 0.5)
        self.put([event, frontier, recovery])
        keys = [self.key("llm_call_history/", 17), b"frontier/llm_call_history",
                self.key("admission_recovery/", -2)]
        self.same(self.read(keys), E("ProviderStoreRows", (event, frontier, recovery)))


if __name__ == "__main__":
    unittest.main()
