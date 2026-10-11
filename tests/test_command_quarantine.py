"""A crash cannot automatically re-admit the command that was executing."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger, encoded


class CommandQuarantineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Ledger(Path(temporary.name) / "state.sqlite3")
        self.command = ["recent-messages", "room", 8]
        self.store.accept("work", "inspect messages")

    def reserve(self, result=None):
        router = {k: ["recent-messages"] for k in ("advertised", "executable", "permitted")}
        snap = self.store.capture("work", "prompt", router, 0)
        ticket = self.store.decide(snap["serial"], dict(
            kind="command", owner=snap["owner"], request="work",
            tool="recent-messages", command=self.command), 0)
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))')
            db.execute("INSERT INTO command_attempts VALUES (?,0,?,?)",
                       (ticket, encoded(self.command), result))
        return ticket

    def denied(self, command=None):
        with self.store.transaction() as db:
            return self.store.command_denial(db, self.command if command is None else command)

    def test_interrupted_command_is_blocked_but_distinct_reads_remain_available(self):
        ticket = self.reserve()
        self.store.restore()
        denied = self.denied()
        self.assertEqual(denied["interrupted_ticket"], ticket)
        self.assertEqual(denied["reason"], "command-quarantined")
        self.assertFalse(denied["execution"])
        self.assertIsNone(self.denied(["recent-messages", "different room", 8]))
        self.assertIsNone(self.denied(["help"]))
        self.assertEqual(self.store.projection()["actions"][0]["status"], "uncertain")

    def test_terminal_receipt_is_not_quarantined(self):
        self.reserve(encoded(dict(disposition="returned", result="messages")))
        self.store.restore()
        self.assertIsNone(self.denied())

    def test_repeated_restore_is_idempotent_and_manual_retries_are_bounded(self):
        self.reserve()
        self.store.restore()
        for _ in range(2):
            self.assertTrue(self.store.release_command(self.command, "checked a repair"))
            self.store.restore()
            self.assertIsNone(self.denied())
            self.assertFalse(self.store.release_command(self.command, "cannot stack permits"))
            with self.store.transaction() as db:
                db.execute("UPDATE command_quarantine SET allowed=0 WHERE command=?",
                           (encoded(self.command),))
            self.reserve()
            self.store.restore()
            self.assertIsNotNone(self.denied())
        self.assertFalse(self.store.release_command(self.command, "retry budget exhausted"))
        self.assertEqual(self.denied()["releases"], 2)


if __name__ == "__main__":
    unittest.main()
