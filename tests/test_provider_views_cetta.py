"""Native retained-call views against the existing SQLite-backed readers.

Recovery/storage selection are outside this pure projection boundary. The
reference runs its real queries over an isolated database; test input order
is deliberately shuffled to exercise immutable ordering through updates.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import native_lifetime

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT.parent / "CeTTa-ours"))
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from space_service_client import E, S
    from value_worker_client import Worker


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to a native JSON/rel build")
class ProviderViewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-views-")
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE native_calls (
          id TEXT PRIMARY KEY, epoch TEXT, pid INTEGER, process_start TEXT,
          created REAL, updated REAL, state TEXT, guard TEXT, transport INTEGER,
          soft_elapsed INTEGER, bytes INTEGER, quiet_ms INTEGER, result TEXT,
          reason TEXT, activity TEXT, lane TEXT, purpose TEXT, notified INTEGER)""")
        module = directory / "views.metta"
        module.write_text('!(import! &self '+json.dumps(str(ROOT / "src/provider_views_cetta.metta"))+')\n'
            '(: views-test (-> %Undefined% %Undefined%))\n'
            '(= (views-test (status $records)) (json:stringify-python (provider-view:status $records)))\n'
            '(= (views-test (notices $records)) (provider-view:notices $records))\n'
            '(= (views-test (result $records $id $offset)) (provider-view:result $records $id $offset))\n')
        with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                    "HOME": str(directory)}, clear=True):
            self.worker = Worker(WORKER, module, "views-test", language="petta")
        self.addCleanup(self.worker.close)
        @contextmanager
        def reference_transaction():
            yield self.db
        self.context = patch.object(native_lifetime, "transaction", reference_transaction)
        self.context.start(); self.addCleanup(self.context.stop)
        self.recovery = patch.object(native_lifetime, "recover", lambda: None)
        self.recovery.start(); self.addCleanup(self.recovery.stop)

    def insert(self, identity, *, created=1000., state="completed", purpose="turn",
               result=None, reason=None, notified=0, **fields):
        record = dict(id=identity, epoch="epoch", pid=1, process_start="born",
                      created=created, updated=created+.125, state=state, guard=None,
                      transport=3, soft_elapsed=0, bytes=100, quiet_ms=12, result=result,
                      reason=reason, activity="", lane="", purpose=purpose, notified=notified)
        record.update(fields)
        self.db.execute("INSERT INTO native_calls VALUES("+",".join("?" for _ in record)+")", tuple(record.values()))

    def records(self):
        def atom(value):
            return S("JsonNull") if value is None else value
        values = [E("ProviderCallV1", row[1], row[0], *(atom(value) for value in row[2:]))
                  for row in self.db.execute("SELECT rowid,* FROM native_calls ORDER BY rowid")]
        random.Random(7301).shuffle(values)
        return tuple(values)

    def test_status_and_notice_queries_keep_purpose_filters_and_timestamp_ties(self):
        rng = random.Random(20261026)
        for number in range(140):
            self.insert("call-"+str(number), created=rng.choice((999., 1000., 1001.25)),
                        state=rng.choice(("claimed", "submitted", "completed", "failed", "late", "uncertain")),
                        purpose=rng.choice(("turn", "coding", "compaction")), notified=rng.randrange(2),
                        reason=rng.choice((None, "", "λ\x00reason")), soft_elapsed=rng.randrange(2))
        records = self.records()
        self.assertEqual(self.worker.call(E("status", records)), json.dumps(native_lifetime.status()))
        expected = self.db.execute("SELECT id,state FROM native_calls WHERE state IN ('late','uncertain') "
                                   "AND notified=0 AND purpose='turn' ORDER BY created").fetchall()
        self.assertEqual(self.worker.call(E("notices", records)), tuple(expected))
        # Replacing a row changes physical position in the native space, but
        # its captured ordinal, and hence timestamp ties, stay unchanged.
        self.db.execute("UPDATE native_calls SET bytes=999999,updated=2000 WHERE id='call-1'")
        self.assertEqual(self.worker.call(E("status", self.records())), json.dumps(native_lifetime.status()))

    def test_retained_result_matches_rendering_slicing_and_digest(self):
        canonical = json.dumps({"items": [
            {"kind": "reasoning", "text": "opaque fixture"},
            {"kind": "message", "role": "assistant", "parts": [
                {"kind": "reasoning", "text": "hidden fixture"},
                {"kind": "text", "text": "visible λ🌱\x00"}, {"kind": "text", "text": "next"}]},
            {"kind": "message", "role": "user", "parts": [{"kind": "text", "text": "input"}]}]})
        texts = [None, "", "short", "a"*7000, "λ🌱\x00\n"*1100, canonical]
        for number, value in enumerate(texts):
            for purpose in ("turn", "coding", "compaction"):
                identity = f"r-{number}-{purpose}"
                self.insert(identity, result=value, purpose=purpose,
                            reason="command-output:invalid-native-tool" if value == canonical else None)
        records = self.records()
        for identity, in self.db.execute("SELECT id FROM native_calls"):
            for offset in (0, 1, 2399, 2400, 4400, 9000):
                with self.subTest(identity=identity, offset=offset):
                    self.assertEqual(self.worker.call(E("result", records, identity, offset)),
                                     native_lifetime.result(identity, offset))
        visible = json.loads(self.worker.call(E("result", records, "r-5-turn", 0)))
        self.assertEqual(visible["result"], "visible λ🌱\x00\nnext")
        self.assertFalse(visible["action_authority"])

    def test_missing_identity_and_invalid_offset_are_explicit(self):
        self.assertEqual(self.worker.call(E("status", ())), "[]")
        self.assertEqual(self.worker.call(E("notices", ())), ())
        self.assertEqual(self.worker.call(E("result", (), "absent", 0)), S("JsonNull"))
        self.assertEqual(self.worker.call(E("result", (), "absent", -1)),
                         E("ProviderViewError", "result offset must be nonnegative"))


if __name__ == "__main__":
    unittest.main()
