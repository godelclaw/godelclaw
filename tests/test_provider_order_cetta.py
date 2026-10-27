"""Captured primary maxima, including absence and negative signed ordinals."""
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
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from space_service_client import E, S, encode
    from value_worker_client import Worker


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to a native store build with reverse scans")
class ProviderOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-order-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "native"
        self.path.mkdir(mode=0o700)
        environment = {"HOME": self.temp.name, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        with patch.dict(os.environ, environment, clear=True):
            self.writer = Worker(WORKER, ROOT / "src/provider_index_store_cetta.metta", "provider-index-store:call", language="petta")
            self.order = Worker(WORKER, ROOT / "src/provider_order_cetta.metta", "provider-order:call", language="petta")
            self.probe = Worker(WORKER, ROOT / "tests/support/provider_order_probe.metta", "provider-order-probe:call", language="petta", import_mode="relative")
        for worker in (self.writer, self.order, self.probe):
            self.addCleanup(worker.close)
        self.epoch, self.revision = self.writer.call(E("Create", str(self.path)))[1:]

    @staticmethod
    def request(ordinal, state="pending"):
        return E("ProviderRequestV1", "id-%d" % ordinal, ordinal, 1.0, 20, state,
                 S("JsonNull"), "provider", "mode", S("JsonNull"), "owner", "")

    @staticmethod
    def key(ordinal):
        return b"requests/" + struct.pack(">Q", (ordinal & ((1 << 64) - 1)) ^ (1 << 63))

    def apply(self, mutations):
        result = self.writer.call(E("Apply", str(self.path), self.epoch, self.revision, tuple(mutations)))
        self.assertEqual(result, E("ProviderIndexAppliedV1", self.epoch, self.revision + 1))
        self.revision += 1

    def maximum(self, kind="ProviderRequestV1", revision=None):
        return self.order.call(E("Maximum", str(self.path), self.epoch,
                                 self.revision if revision is None else revision, S(kind)))

    def witness(self, mutations):
        answer = self.probe.call(E("Witness", str(self.path), S("ProviderRequestV1"), tuple(mutations)))
        self.assertEqual(answer[0], S("ProviderMaximumWitnessV1"), answer)
        ready = self.writer.call(E("Create", str(self.path)))
        self.assertEqual(ready[:2], (S("ProviderIndexedReadyV1"), self.epoch))
        self.revision = ready[2]
        return answer

    def test_empty_is_distinct_from_zero_and_negative_maximum(self):
        empty = self.maximum()
        self.assertEqual(empty[1], S("ProviderNoRowsV1"))
        self.assertEqual(empty[2][2][1], "requests/")
        for ordinal in (-(1 << 63), -7, 0, 1, (1 << 63) - 1):
            self.apply([E("put", self.request(ordinal))])
            result = self.maximum()
            self.assertEqual(encode(result[1]), encode(E("ProviderMaximumOrdinalV1", ordinal, self.key(ordinal))))
            self.assertEqual(result[2][2][1].encode() if isinstance(result[2][2][1], str) else result[2][2][1], self.key(ordinal))
        self.apply([E("remove", self.request((1 << 63) - 1))])
        self.assertEqual(self.maximum()[1], E("ProviderMaximumOrdinalV1", 1, self.key(1)))

    def test_maximum_scope_accepts_lower_changes_and_rejects_new_higher_key(self):
        reference = sqlite3.connect(":memory:")
        self.addCleanup(reference.close)
        reference.execute("CREATE TABLE records(ordinal INTEGER PRIMARY KEY,state TEXT)")
        reference.executemany("INSERT INTO records VALUES(?,?)", ((1, "pending"), (9, "pending")))
        def suffix():
            return reference.execute("SELECT ordinal,state FROM records WHERE ordinal>=9 ORDER BY ordinal").fetchall()
        captured = suffix()
        self.apply([E("put", self.request(1)), E("put", self.request(9))])
        below = self.witness([E("put", self.request(1, "completed"))])
        reference.execute("UPDATE records SET state='completed' WHERE ordinal=1")
        after_below = suffix()
        self.assertEqual(captured, after_below)
        self.assertEqual(below[1], E("ProviderMaximumOrdinalV1", 9, self.key(9)))
        self.assertEqual(below[2][0], S("MaximumAcceptedV1"))
        self.assertEqual(below[3][0], S("MaximumAcceptedV1"))
        above = self.witness([E("put", self.request(10))])
        reference.execute("INSERT INTO records VALUES(10,'pending')")
        self.assertNotEqual(captured, suffix())
        self.assertEqual(reference.execute("SELECT ordinal,state FROM records WHERE ordinal=9").fetchall(), captured)
        self.assertEqual(above[2], E("StoreRefused", S("conflict")))
        self.assertEqual(above[3][0], S("MaximumAcceptedV1"))
        self.assertEqual(self.maximum()[1], E("ProviderMaximumOrdinalV1", 10, self.key(10)))
        def status(answer):
            return "accepted" if answer[0] == S("MaximumAcceptedV1") else answer[1].name
        self.last_trace = "MAXIMUM_SCOPE_TRACE %d %s %s %s" % (
            below[1][1], status(below[2]), status(above[2]), status(above[3]))
        self.last_sqlite_trace = "MAXIMUM_SCOPE_TRACE %d %s %s %s" % (
            captured[-1][0], "accepted" if captured == after_below else "conflict",
            "accepted" if captured == suffix() else "conflict",
            "accepted" if reference.execute("SELECT ordinal,state FROM records WHERE ordinal=9").fetchall() == captured else "conflict")

    def test_deleted_maximum_and_empty_range_invalidate_the_observation(self):
        empty = self.witness([E("put", self.request(-1))])
        self.assertEqual(empty[1], S("ProviderNoRowsV1"))
        self.assertEqual(empty[2], E("StoreRefused", S("conflict")))
        self.assertEqual(empty[3][0], S("MaximumAcceptedV1"))
        removed = self.witness([E("remove", self.request(-1))])
        self.assertEqual(removed[2], E("StoreRefused", S("conflict")))
        self.assertEqual(removed[3], E("StoreRefused", S("conflict")))
        self.assertEqual(self.maximum()[1], S("ProviderNoRowsV1"))

    def test_stale_revision_and_non_table_kind_are_explicit(self):
        old = self.revision
        self.apply([E("put", self.request(1))])
        self.assertEqual(self.maximum(revision=old), E("ProviderStoreChanged", self.epoch, self.revision))
        self.assertEqual(self.maximum("ProviderSequenceV1"), E("ProviderIndexRefusedV1", S("unknown-table")))

    def test_each_primary_table_uses_its_own_ordinal_range(self):
        rows = (
            ("ProviderCooldownV1", "cooldowns/", -3,
             E("ProviderCooldownV1", "fixture", -3, 5.0)),
            ("ProviderCallV1", "native_calls/", 4,
             E("ProviderCallV1", "call", 4, "epoch", 1, "born", 1.0, 2.0,
               "submitted", S("JsonNull"), 7, 0, 0, 10, S("JsonNull"), S("JsonNull"),
               "activity", "lane", "turn", 0)),
            ("ProviderEventV1", "llm_call_history/", -1,
             E("ProviderEventV1", "call", "usage", -1, 1.25, "{}")),
            ("ProviderAdmissionRecoveryV1", "admission_recovery/", -4,
             E("ProviderAdmissionRecoveryV1", "request", -4, 5.0, "reason", 6.0)),
        )
        self.apply([E("put", row) for _, _, _, row in rows])
        for kind, prefix, ordinal, row in rows:
            expected_key = prefix.encode() + self.key(ordinal)[len(b"requests/"):]
            self.assertEqual(encode(self.maximum(kind)[1]),
                             encode(E("ProviderMaximumOrdinalV1", ordinal, expected_key)))
        self.assertEqual(self.maximum()[1], S("ProviderNoRowsV1"))

    def test_generated_maxima_match_sqlite_with_updates_and_deletions(self):
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.execute("CREATE TABLE records(ordinal INTEGER PRIMARY KEY,state TEXT)")
        rng = random.Random(42927)
        for _ in range(100):
            ordinal = rng.choice((-(1 << 63), -17, -1, 0, 1, 9, (1 << 63) - 1, rng.randrange(-20, 21)))
            old = database.execute("SELECT state FROM records WHERE ordinal=?", (ordinal,)).fetchone()
            if old and rng.randrange(3) == 0:
                self.apply([E("remove", self.request(ordinal, old[0]))])
                database.execute("DELETE FROM records WHERE ordinal=?", (ordinal,))
            else:
                state = rng.choice(("pending", "unknown", "completed"))
                self.apply([E("put", self.request(ordinal, state))])
                database.execute("INSERT OR REPLACE INTO records VALUES(?,?)", (ordinal, state))
            expected = database.execute("SELECT MAX(ordinal) FROM records").fetchone()[0]
            actual = self.maximum()[1]
            if expected is None:
                self.assertEqual(actual, S("ProviderNoRowsV1"))
            else:
                self.assertEqual(encode(actual), encode(E("ProviderMaximumOrdinalV1", expected, self.key(expected))))


if __name__ == "__main__":
    unittest.main()
