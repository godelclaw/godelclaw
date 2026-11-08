"""The receipt-driven notice cleanup (channels/telegram_notice_cleanup.metta) on the native ledger.

The cleanup's statements are the ledger's notice-* verbs. Notices are registered through input_ack, a
MeTTa program then drives the cleanup's send and delete receipts, withdrawal, delete-key adoption, a
definite non-delivery and the deletion selection, and every printed answer and the final notice rows
are checked. Delete keys are made per attempt from the clock, so only their shape and reuse are checked.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import ENGINE, LANE, available, oracle_environment
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

    def side(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            keys = {}
            for number, task in enumerate(("t1", "t2", "t3", "t4", "t5")):
                keys[task] = runner.apply(("register", task, "u%d" % number, LANE, 1000.0 + number))
                self.assertTrue(keys[task], task)
                self.assertTrue(runner.apply(("submitted", task)))
            runner.apply(("mark", ["u0", "u1", "u4"], 1100.0))    # t1, t2 and t5 processed
            input_ack.close_ledger()
            lines = self.run_guest(runner.path, self.server.socket, program(keys))
            rows, _ = self.dump()
            # Delete keys are made from the clock: compare whether one is held, not its digits.
            key = base.COLUMNS.index("delete_key")
            rows = [row[:key] + (row[key] is not None,) + row[key + 1:] for row in rows]
            return lines, rows

    def test_the_cleanup_reads_and_writes_its_notices(self):
        printed, rows = self.side()
        lines = {line.split(" ", 2)[0] + (" " + line.split(" ", 2)[1] if line.startswith("(OUT") else ""): line
                 for line in printed}
        self.assertEqual(lines["(OUT 0"], "(OUT 0 true)")
        self.assertEqual(lines["(OUT 5"], "(OUT 5 false)")             # a second message id is refused
        self.assertEqual(lines["(KEY1"], "(KEY1 true true true)")       # adopted, the lane's, deleted
        self.assertEqual(lines["(KEY2"], "(KEY2 true false)")           # not sent: a new key next time
        self.assertEqual(lines["(KEY4"], "(KEY4 false true)")           # only its own message
        self.assertEqual(lines["(KEY5"], "(KEY5 true none)")
        # t1, t4 deleted; t3 unprocessed. A row carries the stored delete key and the creation time.
        self.assertRegex(lines["(AFTER"], r'^\(AFTER \(\("t2" "%s" 102 "%s\.\d{20}" 1001\.0\)\)\)$' % (LANE, LANE))
        states = {row[0]: row[base.COLUMNS.index("state")] for row in rows}
        self.assertEqual(states, {"t1": "deleted", "t2": "submitted", "t3": "submitted",
                                  "t4": "deleted", "t5": "withdrawing"})


    def test_a_stored_key_is_used_and_a_notice_past_48_hours_expires_once(self):
        import native_ledger
        guest = lambda body: "\n".join([
            '!(import! &self (library lib_import))', '!(import! &self ./channels/telegram_notice_cleanup.metta)',
            body, '!(println! NOTICES_OK)']) + "\n"
        delivered = lambda key, number: (
            '(nc:receipt (JsonArray ((JsonString "delivery") (JsonString "%s") (JsonNumber "0") (JsonNumber "1")'
            ' (JsonArray ((JsonString "delivered") (JsonNumber "%d"))))))' % (key, number))
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            keys = {}
            for number, task in enumerate(("t1", "t2", "t3")):
                keys[task] = runner.apply(("register", task, "u%d" % number, LANE, 1000.0 + number))
                self.assertTrue(runner.apply(("submitted", task)))
            runner.apply(("mark", ["u0", "u1", "u2"], 1100.0))
            input_ack.close_ledger()
            setup = " ".join(delivered(keys[task], 101 + number) for number, task in enumerate(("t1", "t2", "t3")))
            self.run_guest(runner.path, self.server.socket, guest(
                '!(println! (SETUP %s (nc:delete-key "t1" "%s")))' % (setup, LANE)))
            # Through a ledger that refuses every request for a delete key: t1 holds one, t2 does not.
            refusing, faults = native_ledger.faulty(self, directory, self.server.socket, marker=b"notice-cleanup-key")
            self.run_guest(runner.path, refusing, guest('!(println! (T1 (nc:delete (car-atom (nc:deletable)) 1100.0)))'))
            self.assertEqual(faults, [])
            self.run_guest(runner.path, refusing,
                           guest('!(println! (T2 (nc:delete (car-atom (cdr-atom (nc:deletable))) 1100.0)))'))
            self.assertTrue(faults)
            # Past Telegram's 48 hours every notice is expired, once, and none is deletable.
            printed = self.run_guest(runner.path, self.server.socket, guest(
                '!(println! (PUMP (nc:delete-rows (nc:deletable) %r)))\n!(println! (AFTER (nc:deletable)))'
                % (1002.0 + 172801)))
            self.assertIn("(PUMP 0)", printed)
            self.assertIn("(AFTER ())", printed)
            rows, _ = self.dump()
            self.assertEqual({row[0]: row[base.COLUMNS.index("state")] for row in rows},
                             {"t1": "expired", "t2": "expired", "t3": "expired"})


if __name__ == "__main__":
    unittest.main()
