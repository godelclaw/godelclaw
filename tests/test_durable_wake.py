"""Wake hints from an open SQLite WAL, as used by the native service."""
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from durable_telegram import Changes


class DurableWakeTests(unittest.TestCase):
    def test_commit_on_open_wal_wakes_snapshot_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(pathlib.Path(directory) / "journal.sqlite3")
            wake = Changes([directory])
            try:
                if wake.fd < 0:
                    self.skipTest("inotify is unavailable")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA wal_autocheckpoint=0")
                db.execute("CREATE TABLE events (body TEXT)")
                db.commit()
                # No creation or close occurs after arming: the long-lived
                # service appends to the WAL it already has open.
                wake.arm()
                db.execute("INSERT INTO events VALUES ('owned completion')")
                db.commit()
                self.assertEqual(wake.wait(1), 1)
            finally:
                wake.close()
                db.close()

    def test_ignored_bookkeeping_write_does_not_self_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = pathlib.Path(directory) / "owner.lock"
            lock.write_text("")
            wake = Changes([directory], ignore_names=[lock.name])
            try:
                if wake.fd < 0:
                    self.skipTest("inotify is unavailable")
                wake.arm()
                with lock.open("a") as stream:
                    stream.write("bookkeeping")
                    stream.flush()
                self.assertEqual(wake.wait(.02), 0)
            finally:
                wake.close()


if __name__ == "__main__":
    unittest.main()
