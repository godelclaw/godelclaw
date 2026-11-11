"""Atomic native provider indexes and bounded, revision-bound candidate pages."""
import hashlib
import os
from pathlib import Path
import random
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT.parent / "CeTTa-ours"))
CETTA_SOURCE = Path(os.environ.get("CETTA_SOURCE", ROOT.parent / "CeTTa-ours"))
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from space_service_client import E, S, encode, decode
    from value_worker_client import Worker


def raw(value):
    return value.encode("utf-8") if isinstance(value, str) else value


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to a native store build")
class ProviderIndexStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-index-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "native"
        self.path.mkdir(mode=0o700)
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.temp.name}
        with patch.dict(os.environ, environment, clear=True):
            self.writer = Worker(WORKER, ROOT / "src/provider_index_store_cetta.metta",
                                 "provider-index-store:call", language="petta")
            self.query = Worker(WORKER, ROOT / "src/provider_index_query_cetta.metta",
                                "provider-index-query:call", language="petta")
            self.legacy = Worker(WORKER, ROOT / "src/provider_store_cetta.metta",
                                 "provider-store:checkpoint-call", language="petta")
            self.bytes = Worker(WORKER, CETTA_SOURCE / "tests/support/store_fixture.metta",
                                "store-fixture:run", language="petta")
            self.probe = Worker(WORKER, ROOT / "tests/support/provider_index_probe.metta",
                                "provider-index-probe:call", language="petta", import_mode="relative")
        for worker in (self.writer, self.query, self.legacy, self.bytes, self.probe):
            self.addCleanup(worker.close)
        ready = self.writer.call(E("Create", str(self.path)))
        self.assertEqual(ready[0], S("ProviderIndexedReadyV1"))
        self.epoch, self.revision = ready[1:]

    @staticmethod
    def request(ordinal, identity=None, state="pending"):
        return E("ProviderRequestV1", "id-%d" % ordinal if identity is None else identity,
                 ordinal, 1.25, 20, state, S("JsonNull"), "fixture", "coding",
                 S("JsonNull"), "owner", "fingerprint")

    @staticmethod
    def suffix(ordinal):
        return struct.pack(">Q", (ordinal & ((1 << 64) - 1)) ^ (1 << 63))

    def apply(self, operations):
        result = self.writer.call(E("Apply", str(self.path), self.epoch, self.revision, tuple(operations)))
        self.assertEqual(result, E("ProviderIndexAppliedV1", self.epoch, self.revision + 1))
        self.revision += 1

    def page(self, value, *, column="id", cursor=None, limit=3, revision=None):
        return self.query.call(E("Query", str(self.path), self.epoch,
                                 self.revision if revision is None else revision,
                                 S("ProviderRequestV1"), column, value,
                                 S("NoBound") if cursor is None else cursor, limit))

    def all(self, value, *, column="id", limit=3):
        cursor, output = None, []
        while True:
            page = self.page(value, column=column, cursor=cursor, limit=limit)
            self.assertEqual(page[0], S("ProviderIndexPageV1"), page)
            output.extend(page[1])
            if page[2] == S("ProviderIndexDoneV1"):
                return tuple(output)
            self.assertEqual(page[2][0], S("ProviderIndexCursorV1"))
            cursor = page[2]

    def audit_requests(self, records):
        answer = self.probe.call(E("Audit", str(self.path)))
        self.assertEqual(answer[0], S("ProviderIndexAuditV1"))
        actual = {raw(key): raw(value) for key, value in answer[1]}
        expected = {b"layout/provider-index/v1": encode(E("ProviderIndexLayoutV1", 1))}
        for row in records:
            primary = b"requests/" + self.suffix(row[2])
            expected[primary] = encode(row)
            for name, value in zip(("id", "status", "provider", "mode", "owner"),
                                   (row[1], row[5], row[7], row[8], row[10])):
                prefix = ("index/v1/requests/" + name + "/" +
                          hashlib.sha256(encode(value)).hexdigest() + "/").encode()
                expected[prefix + self.suffix(row[2])] = encode(E("ProviderIndexV1", primary))
        self.assertEqual(actual, expected)

    def test_replace_and_remove_change_primary_and_all_derived_views_together(self):
        before = self.request(9, "first")
        middle = self.request(9, "middle", "completed")
        after = self.request(9, "last", "unknown")
        self.apply([E("put", before), E("put", middle), E("put", after)])
        self.audit_requests([after])
        self.assertEqual(self.all("first"), ())
        self.assertEqual(self.all("middle"), ())
        self.assertEqual(encode(self.all("last")), encode((after,)))
        self.assertEqual(self.all("pending", column="status"), ())
        self.assertEqual(encode(self.all("unknown", column="status")), encode((after,)))
        self.apply([E("remove", after)])
        self.audit_requests([])
        self.assertEqual(self.all("last"), ())
        self.assertEqual(self.all("unknown", column="status"), ())

    def test_rejected_batch_does_not_publish_its_valid_prefix(self):
        row = self.request(1)
        self.apply([E("add", row)])
        bad_old = self.request(1, "wrong-identity")
        answer = self.writer.call(E("Apply", str(self.path), self.epoch, self.revision,
                                     (E("add", self.request(2)), E("remove", bad_old))))
        self.assertEqual(answer, E("ProviderIndexRefusedV1", S("row-precondition")))
        self.assertEqual(self.all("id-2"), ())
        self.assertEqual(encode(self.all("id-1")), encode((row,)))
        self.assertEqual(self.writer.call(E("Create", str(self.path))),
                         E("ProviderIndexedReadyV1", self.epoch, self.revision))
        self.audit_requests([row])

    def test_updates_preserve_full_typed_field_identity(self):
        values = (0, 0.0, -0.0, 1, 1.0, True, False, S("true"), S("false"),
                  S("JsonNull"), "null", b"\xff\0")
        previous = None
        for value in values:
            row = self.request(1, value)
            self.apply([E("put", row)])
            self.audit_requests([row])
            self.assertEqual(encode(self.all(value)), encode((row,)))
            if previous is not None:
                self.assertEqual(self.all(previous), ())
            previous = value

    def test_duplicate_add_stale_plan_and_legacy_writer_are_refused(self):
        row = self.request(1)
        before = self.revision
        self.apply([E("add", row)])
        self.assertEqual(self.writer.call(E("Apply", str(self.path), self.epoch, self.revision, (E("add", row),))),
                         E("ProviderIndexRefusedV1", S("existing-row")))
        self.assertEqual(self.writer.call(E("Apply", str(self.path), self.epoch, before, (E("put", row),))),
                         E("ProviderStoreChanged", self.epoch, self.revision))
        self.assertEqual(self.legacy.call(E("Import", str(self.path), (self.request(2),))),
                         E("ProviderStoreRefused", S("indexed-store")))
        self.assertEqual(self.all("id-2"), ())

    def test_raw_checkpoint_is_not_a_schema_transaction_gateway(self):
        # Keep the unimplemented domain guard visible. Ordinal uniqueness
        # alone does not enforce the source table's nullable TEXT primary key.
        first, second = self.request(1, "same-id"), self.request(2, "same-id")
        self.apply([E("add", first), E("add", second)])
        self.assertEqual(encode(self.all("same-id")), encode((first, second)))
        reference = sqlite3.connect(":memory:")
        self.addCleanup(reference.close)
        reference.execute("CREATE TABLE requests(id TEXT PRIMARY KEY)")
        reference.execute("INSERT INTO requests VALUES('same-id')")
        with self.assertRaises(sqlite3.IntegrityError):
            reference.execute("INSERT INTO requests VALUES('same-id')")

    def test_pages_keep_signed_order_and_reconnect_without_recounting(self):
        ordinals = [-(1 << 63), -7, 0, 1, 2, 19, (1 << 63) - 1]
        records = [self.request(ordinal, S("JsonNull")) for ordinal in ordinals]
        self.apply([E("add", row) for row in reversed(records)])
        first = self.page("pending", column="status", limit=2)
        self.assertEqual(encode(first[1]), encode(tuple(records[:2])))
        self.query.close()
        self.query = Worker(WORKER, ROOT / "src/provider_index_query_cetta.metta",
                            "provider-index-query:call", language="petta")
        self.addCleanup(self.query.close)
        output, cursor = list(first[1]), first[2]
        while cursor != S("ProviderIndexDoneV1"):
            page = self.page("pending", column="status", cursor=cursor, limit=2)
            output.extend(page[1]); cursor = page[2]
        self.assertEqual(encode(tuple(output)), encode(tuple(records)))

    def test_cursor_cannot_cross_a_write_or_a_different_predicate(self):
        self.apply([E("add", self.request(i)) for i in range(4)])
        page = self.page("pending", column="status", limit=1)
        cursor, old = page[2], self.revision
        self.assertEqual(self.page("completed", column="status", cursor=cursor),
                         E("ProviderIndexRefusedV1", S("foreign-query-cursor")))
        self.apply([E("put", self.request(3, state="completed"))])
        self.assertEqual(self.page("pending", column="status", cursor=cursor, revision=old),
                         E("ProviderStoreChanged", self.epoch, self.revision))
        self.assertEqual(self.page("pending", column="status", cursor=cursor),
                         E("ProviderIndexRefusedV1", S("stale-query-cursor")))

    def test_colliding_candidate_is_checked_and_empty_page_is_not_end(self):
        other, wanted = self.request(1, "other"), self.request(2, "wanted")
        self.apply([E("add", other), E("add", wanted)])
        prefix = b"index/v1/requests/id/" + hashlib.sha256(encode("wanted")).hexdigest().encode() + b"/"
        forged = prefix + self.suffix(1)
        primary = b"requests/" + self.suffix(1)
        # Model a hash collision without assuming the digest is injective.
        answer = self.bytes.call(E("Apply", str(self.path), E("reads"), E("writes", E("point", forged)),
                                    E("ops", E("put", forged, encode(E("ProviderIndexV1", primary))))))
        self.assertEqual(answer, E("Applied", self.revision + 1)); self.revision += 1
        first = self.page("wanted", limit=1)
        self.assertEqual(first[1], ())
        self.assertEqual(first[2][0], S("ProviderIndexCursorV1"))
        self.assertIn(encode(E("point", primary)), [encode(x) for x in first[3][1:]])
        self.assertEqual(encode(self.all("wanted", limit=1)), encode((wanted,)))

    def test_full_row_reads_protect_a_hash_bucket_that_itself_does_not_change(self):
        first, second = self.request(1), self.request(2, state="unknown")
        self.apply([E("add", first), E("add", second)])
        prefix = b"index/v1/requests/status/" + hashlib.sha256(encode("unknown")).hexdigest().encode() + b"/"
        forged, primary = prefix + self.suffix(1), b"requests/" + self.suffix(1)
        result = self.bytes.call(E("Apply", str(self.path), E("reads"), E("writes", E("point", forged)),
                                    E("ops", E("put", forged, encode(E("ProviderIndexV1", primary))))))
        self.assertEqual(result, E("Applied", self.revision + 1)); self.revision += 1
        witness = self.probe.call(E("Witness", str(self.path), self.request(1, state="unknown")))
        self.last_witness = witness
        self.assertEqual(witness, E("ProviderIndexWitnessV1", 1, 2,
                                   E("StoreRefused", S("conflict")),
                                   E("WeakAcceptedV1", self.revision + 2)))
        self.revision += 2
        self.audit_requests([self.request(1, state="unknown"), second])

    def test_random_updates_match_sqlite_selected_live_records(self):
        database = sqlite3.connect(":memory:"); self.addCleanup(database.close)
        database.execute("CREATE TABLE records(ordinal INTEGER PRIMARY KEY,id TEXT,state TEXT,payload BLOB)")
        rng = random.Random(220127)
        for _ in range(60):
            ordinal = rng.randrange(-4, 12)
            old = database.execute("SELECT payload FROM records WHERE ordinal=?", (ordinal,)).fetchone()
            if old and rng.randrange(3) == 0:
                self.apply([E("remove", decode(old[0]))])
                database.execute("DELETE FROM records WHERE ordinal=?", (ordinal,))
            else:
                identity = rng.choice(("one", "two", "λ\0id", "long-" + "x" * 1000))
                state = rng.choice(("pending", "unknown", "completed"))
                row = self.request(ordinal, identity, state)
                self.apply([E("put", row)])
                database.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?)", (ordinal, identity, state, encode(row)))
            for state in ("pending", "unknown", "completed"):
                expected = tuple(decode(row[0]) for row in database.execute(
                    "SELECT payload FROM records WHERE state=? ORDER BY ordinal", (state,)))
                self.assertEqual(encode(self.all(state, column="status", limit=2)), encode(expected))
            self.audit_requests([decode(row[0]) for row in database.execute("SELECT payload FROM records")])


if __name__ == "__main__":
    unittest.main()
