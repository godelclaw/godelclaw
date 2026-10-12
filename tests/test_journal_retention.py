"""Journal retention: which received records go, the archive check, and unit restore."""

import argparse
import gzip
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import telegram_journal_retention as retention  # noqa: E402


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.journal = self.root / "journal.db"
        with sqlite3.connect(str(self.journal)) as db:
            db.execute("CREATE TABLE meta (epoch TEXT, revision INTEGER, live_bytes INTEGER)")
            db.execute("INSERT INTO meta VALUES ('E', 40, 0)")
            db.execute("CREATE TABLE records (space TEXT, key TEXT, revision INTEGER, "
                       "position INTEGER, value BLOB)")
            for n in range(10):
                db.execute("INSERT INTO records VALUES ('host.received', ?, ?, 0, ?)",
                           ("telegram-1/%d" % (100 + n), n + 1, b"update %d" % n))

    def tearDown(self):
        self.directory.cleanup()

    def keys(self, records):
        return [key for key, _, _, _ in records]

    def test_keeps_the_newest(self):
        epoch, revision, old = retention.candidates(self.journal, 4)
        self.assertEqual((epoch, revision), ("E", 40))
        self.assertEqual(self.keys(old), ["telegram-1/%d" % n for n in range(100, 106)])

    def test_keeps_everything_from_the_oldest_pending_update(self):
        with sqlite3.connect(str(self.journal)) as db:
            db.execute("INSERT INTO records VALUES ('host.inbox', 'telegram-1/103', 50, 0, x'00')")
        _, _, old = retention.candidates(self.journal, 4)
        self.assertEqual(self.keys(old), ["telegram-1/100", "telegram-1/101", "telegram-1/102"])

    def test_archive_reads_back_with_raw_values(self):
        _, _, old = retention.candidates(self.journal, 7)
        target, digest, _ = retention.write_archive(self.root / "archive", old)
        self.assertEqual(target.name, "received-%s.jsonl.gz" % digest[:16])
        with gzip.open(str(target), "rt", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle]
        self.assertEqual([line["key"] for line in lines], self.keys(old))
        self.assertEqual(lines[0]["value_b64"], "dXBkYXRlIDA=")

    def test_units_are_started_again_when_the_run_fails(self):
        calls = []

        def systemctl(command, **_):
            calls.append(command[2:])
            return mock.Mock(returncode=0)

        args = ["--journal", str(self.journal), "--archive", str(self.root / "archive"),
                "--cetta", "cetta", "--unit", "channel.socket", "--unit", "channel.service"]
        with mock.patch.object(sys, "argv", ["retention"] + args), \
                mock.patch.object(retention.subprocess, "run", systemctl), \
                mock.patch.object(retention, "run", side_effect=RuntimeError("removal failed")):
            with self.assertRaises(RuntimeError):
                retention.main()
        self.assertEqual(calls[-2:], [["stop", "channel.socket", "channel.service"],
                                      ["start", "channel.socket", "channel.service"]])


if __name__ == "__main__":
    unittest.main()
