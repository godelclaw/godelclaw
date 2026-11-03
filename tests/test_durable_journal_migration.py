"""Journal conversion against both actual native readers, without live data."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate_durable_journal import convert, export_sqlite


class JournalConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_existing_destination_is_preserved(self):
        destination = self.directory / "existing"
        destination.write_bytes(b"retained")
        with self.assertRaisesRegex(ValueError, "already exists"):
            convert(self.directory / "absent", destination)
        self.assertEqual(destination.read_bytes(), b"retained")

    def test_bad_metadata_does_not_publish_a_partial_destination(self):
        source, destination = self.directory / "source", self.directory / "destination"
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE meta(id,epoch,revision,live_count)")
            db.execute("INSERT INTO meta VALUES(1,?,1,1)", ("a" * 32,))
            db.execute("CREATE TABLE records(space,key,value,revision,position)")
        with self.assertRaisesRegex(ValueError, "count"):
            convert(source, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.glob(".journal-conversion-*")), [])

    @unittest.skipUnless(os.environ.get("CETTA_OLD_BIN") and os.environ.get("CETTA_BIN"),
                         "set both journal reader binaries")
    def test_native_readers_agree_and_converted_store_accepts_a_new_commit(self):
        source, destination = self.directory / "source.sqlite3", self.directory / "converted.csl"
        old, new = os.environ["CETTA_OLD_BIN"], os.environ["CETTA_BIN"]

        def run(binary, path, extra=()):
            forms = ["!(import! &self durable)", "!(durable:open " + json.dumps(str(path)) + ")",
                     *extra, "!(durable:close)"]
            command = [binary, "--lang", "petta", "--durable-admin", "--quiet"]
            for form in forms:
                command += ["-e", form]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        epoch = re.search(r'"([0-9a-f]{32})"', run(old, source)).group(1)
        run(old, source, [f'!(durable:commit "{epoch}" 0 ((durable:insert "inbox" "a" (message "first")) '
                           '(durable:insert "inbox" "b" (message "漢🙂"))))'])
        run(old, source, [f'!(durable:commit "{epoch}" 1 ((durable:replace "inbox" "a" (message "new"))))'])
        expected = run(old, source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        receipt = convert(source, destination)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual((receipt["epoch"], receipt["revision"], receipt["records"]), (epoch, 2, 2))
        self.assertEqual(run(new, destination), expected)
        after = run(new, destination, [f'!(durable:commit "{epoch}" 2 ((durable:replace "inbox" "a" (continued "λ")) '
                                      '(durable:remove "inbox" "b") (durable:insert "outbox" "next" (sent 3))))',
                                      '!(durable:read "*")'])
        self.assertIn(f'(durable:committed "{epoch}" 3)', after)
        self.assertIn('(sent 3)', after)
        rollback = self.directory / "rollback.sqlite3"
        latest = run(new, destination)
        reversed_receipt = export_sqlite(destination, rollback)
        self.assertEqual((reversed_receipt["revision"], reversed_receipt["records"]), (3, 2))
        self.assertNotIn('(durable:record "inbox" "b"', latest)
        self.assertEqual(run(old, rollback), latest)
        damaged = self.directory / "damaged.csl"
        data = bytearray(destination.read_bytes())
        data[-1] ^= 1
        damaged.write_bytes(data)
        self.assertIn("durable:failure corrupt", run(new, damaged))
        with self.assertRaisesRegex(ValueError, "checksum"):
            export_sqlite(damaged, self.directory / "invalid.sqlite3")
        self.assertFalse((self.directory / "invalid.sqlite3").exists())
