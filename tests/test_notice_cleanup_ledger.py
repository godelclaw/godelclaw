"""The receipt-driven notice cleanup (channels/telegram_notice_cleanup.metta) on the native ledger.

The cleanup's statements run as SQL on the SQLite path and as the ledger's notice-* verbs on the native
one. The same notices are registered through input_ack on both paths, the same MeTTa program then drives
the cleanup's send and delete receipts, withdrawal, delete-key adoption, a definite non-delivery and the
deletion selection, and every printed answer and the final notice rows must agree. Delete keys are made
per attempt from the clock, so only their shape and their reuse are compared.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import ENGINE, LANE, Oracle, available, oracle_environment
import test_input_ledger as base


def program(keys):
    def delivery(key, outcome):
        return '(nc:receipt (json-parse "%s"))' % json.dumps(["delivery", key, 0, 1, outcome]).replace('"', '\\"')
    lines = [
        '(nc:ready?)',
        delivery(keys["t1"], ["delivered", 101]),
        delivery(keys["t2"], ["delivered", 102]),
        delivery(keys["t3"], ["delivered", 103]),
        delivery(keys["t4"], ["delivered", 104]),
        delivery(keys["t1"], ["delivered", 999]),      # another message id for the same send: refused
        delivery(keys["t3"], ["failed"]),              # no message, no change
        delivery("%s.%020d" % (LANE, 7), ["delivered", 1]),   # no such key
        '(nc:withdraw "t4")',                          # an operator control's notice
        '(nc:withdraw "t5")',
        '(nc:withdraw "nobody")',
        '(nc:deletable)',
    ]
    code = ['!(import! &self (library lib_import))', '!(import! &self ./channels/telegram_notice_cleanup.metta)']
    for number, line in enumerate(lines):
        code.append('!(println! (OUT %d %s))' % (number, line))
    def receipt_of(key, outcome):
        return ('(nc:receipt (JsonArray ((JsonString "delivery") (JsonString %s) (JsonNumber "0") (JsonNumber "1") %s)))'
                % (key, outcome))
    delivered = lambda number: '(JsonArray ((JsonString "delivered") (JsonNumber "%d")))' % number
    not_sent = '(JsonArray ((JsonString "not-sent")))'
    code += [
        # A delete key is made once and adopted after; a delete of the message closes the notice.
        '!(let* (($k1 (nc:delete-key "t1" "%s")) ($k2 (nc:delete-key "t1" "%s")))'
        ' (println! (KEY1 (== $k1 $k2) (str:starts-with $k1 "%s.") %s)))' % (LANE, LANE, LANE, receipt_of("$k1", delivered(0))),
        # A definite non-delivery clears the key; the next attempt makes another.
        '!(let* (($k1 (nc:delete-key "t2" "%s")) ($sent %s) ($k2 (nc:delete-key "t2" "%s")))'
        ' (println! (KEY2 $sent (== $k1 $k2))))' % (LANE, receipt_of("$k1", not_sent), LANE),
        # A withdrawn notice is deleted unprocessed, only for its own message id.
        '!(let* (($k (nc:delete-key "t4" "%s")) ($wrong %s) ($right %s))'
        ' (println! (KEY4 $wrong $right)))' % (LANE, receipt_of("$k", delivered(999)), receipt_of("$k", delivered(104))),
        # An open notice without a message gets a key too; a task without a notice has none.
        '!(let $k (nc:delete-key "t5" "%s") (println! (KEY5 (str:starts-with $k "%s.") (nc:delete-key "nobody" "%s"))))'
        % (LANE, LANE, LANE),
        '!(println! (AFTER (nc:deletable)))',
        '!(println! NOTICES_OK)',
    ]
    return "\n".join(code) + "\n"


@unittest.skipUnless(available(), SKIP)
class NoticeCleanupNativeTest(NativeCase):
    def run_guest(self, path, socket, code):
        import test_command_dispatch_engine as dispatch
        env = dispatch.CommandDispatchEngineTest()._cetta_env()
        env.update(METTACLAW_STRATEGY_PATH=str(path), METTACLAW_ENGINE_STATE_PATH=str(Path(path).parent / "engine"))
        env.pop(input_ack.LEDGER_SOCKET, None)
        if socket:
            env[input_ack.LEDGER_SOCKET] = str(socket)
        source = ROOT / ("zz-notice-ledger-%d.metta" % os.getpid())
        source.write_text(code)
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        done = subprocess.run([str(ENGINE), "--lang", "petta", "--import-mode", "ancestor-walk", str(source)],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertIn("NOTICES_OK", done.stdout, done.stdout[-3000:] + done.stderr[-3000:])
        return [line for line in done.stdout.splitlines() if line.startswith("(")]

    def side(self, native):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket) if native else Oracle.create(directory)
            keys = {}
            for number, task in enumerate(("t1", "t2", "t3", "t4", "t5")):
                keys[task] = runner.apply(("register", task, "u%d" % number, LANE, 1000.0 + number))
                self.assertTrue(keys[task], task)
                self.assertTrue(runner.apply(("submitted", task)))
            runner.apply(("mark", ["u0", "u1", "u4"], 1100.0))    # t1, t2 and t5 processed
            input_ack.close_ledger()
            lines = self.run_guest(runner.path, self.server.socket if native else None, program(keys))
            if native:
                rows, _ = self.dump()
            else:
                with closing(sqlite3.connect(runner.path)) as db:
                    rows = sorted(tuple(row) for row in db.execute("SELECT %s FROM input_notices" % ", ".join(base.COLUMNS)))
            # Delete keys are made from the clock: compare whether one is held, not its digits.
            key = base.COLUMNS.index("delete_key")
            rows = [row[:key] + (row[key] is not None,) + row[key + 1:] for row in rows]
            return lines, rows

    def test_the_cleanup_reads_and_writes_the_same_on_both_paths(self):
        oracle_lines, oracle_rows = self.side(False)
        native_lines, native_rows = self.side(True)
        self.assertEqual(native_lines, oracle_lines)
        self.assertEqual(native_rows, oracle_rows)
        lines = {line.split(" ", 2)[0] + (" " + line.split(" ", 2)[1] if line.startswith("(OUT") else ""): line
                 for line in oracle_lines}
        self.assertEqual(lines["(OUT 0"], "(OUT 0 true)")
        self.assertEqual(lines["(OUT 5"], "(OUT 5 false)")             # a second message id is refused
        self.assertEqual(lines["(KEY1"], "(KEY1 true true true)")       # adopted, the lane's, deleted
        self.assertEqual(lines["(KEY2"], "(KEY2 true false)")           # not sent: a new key next time
        self.assertEqual(lines["(KEY4"], "(KEY4 false true)")           # only its own message
        self.assertEqual(lines["(KEY5"], "(KEY5 true sqlite:null)")
        self.assertEqual(lines["(AFTER"], '(AFTER (("t2" "%s" 102)))' % LANE)   # t1, t4 deleted; t3 unprocessed
        states = {row[0]: row[base.COLUMNS.index("state")] for row in oracle_rows}
        self.assertEqual(states, {"t1": "deleted", "t2": "submitted", "t3": "submitted",
                                  "t4": "deleted", "t5": "withdrawing"})


if __name__ == "__main__":
    unittest.main()
