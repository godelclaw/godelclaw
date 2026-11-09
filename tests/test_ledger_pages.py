"""A read whose answer is larger than one wire value carries is read in pages (input-ledger:page).

One wire value holds at most 65536 nodes. The ledger's server answers LedgerPage with one page of a
read: a list answer's rows [start, start + count), or a record whose expression fields are cut so.
The MeTTa client turns to pages when a call's answer is too large (ledger-client:call) and halves a
page that is still too large; input_ack's whole reads always read in pages, since its worker passes
each answer on as one value again.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from observed_strategy import Ledger
from execution_gateway.cetta_worker import E, S
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import ENGINE, DbServer, available, literal, oracle_environment
import test_native_cognition_ledger as cognition

KEYS = 3000
FRAME = 65536  # a server taking values of 64 KiB: the meta keys below are about 200 KiB


@unittest.skipUnless(available(), SKIP)
class PagesTest(NativeCase):
    def client(self, code, value_bytes=None):
        source = self.directory / "pages.metta"
        source.write_text("!(import! &self %s)\n%s\n!(println! PAGES_OK)\n" % (ROOT / "src/ledger_client", code))
        cmd = [str(ENGINE)]
        if value_bytes is not None:
            cmd.extend(["--value-bytes", str(value_bytes)])
        cmd.extend(["--lang", "petta", str(source)])
        done = subprocess.run(cmd, cwd=self.directory,
                              capture_output=True, text=True, timeout=600)
        self.assertIn("PAGES_OK", done.stdout, done.stdout[-3000:] + done.stderr[-3000:])
        return [line for line in done.stdout.splitlines() if line.startswith("(")]

    def test_an_answer_larger_than_one_wire_value_is_read_whole(self):
        # A wire value holds at most 65536 nodes and the server's allowance of bytes; a reply larger than either
        # fails alike (reply-limit). This server's allowance is small, so a few thousand keys exceed it.
        self.server.stop()
        (self.directory / "small").mkdir()
        self.server = DbServer(self.directory / "small", value_bytes=FRAME)
        self.server.start()
        self.session()
        keys = [("page:%05d" % n, '{"n":%d,"text":"%s"}' % (n, "x" * 40)) for n in range(KEYS)]
        expect = "empty"
        for start in range(0, KEYS, 500):
            atoms = tuple(E("ledger:meta", key, text) for key, text in keys[start:start + 500])
            self.assertIs(input_ack._ledger_call("import", (S(expect), atoms), 120), True)
            expect = "any"
        self.assertEqual(input_ack.meta_prefix("page:"), keys)
        socket = literal(str(self.server.socket))
        lines = self.client(
            '!(let $r (ledger-client:call %s "meta-prefix" ("page:"))\n'
            '   (println! (WHOLE (size-atom $r) (size-atom (unique-atom $r)) (== $r (sort-atom $r))\n'
            '                    (index-atom $r 0) (index-atom $r %d))))\n'
            '!(println! (ONE (ledger-client:page %s "meta-prefix" ("page:") %d 5)))\n'
            '!(println! (PLAIN (ledger-client:session %s LedgerCall (LedgerCallV1 "meta-prefix" ("page:")) 3)))\n'
            '!(println! (WRITE (ledger-client:page %s "ensure" () 0 5)))\n'
            % (socket, KEYS - 1, socket, KEYS - 2, socket, socket))
        text = lambda n: '("page:%05d" "{\\"n\\":%d,\\"text\\":\\"%s\\"}")' % (n, n, "x" * 40)
        self.assertEqual(lines[0], "(WHOLE %d %d true %s %s)" % (KEYS, KEYS, text(0), text(KEYS - 1)))
        self.assertEqual(lines[1], "(ONE (page last (%s %s)))" % (text(KEYS - 2), text(KEYS - 1)))
        # The whole answer in one value is refused once, not tried again as a lost reply.
        self.assertEqual(lines[2], "(PLAIN (Error ledger-client:call (unanswered ((ServiceFailedV1 reply-limit)))))")
        self.assertEqual(lines[3], '(WRITE (Error input-ledger:page (not-a-read "ensure")))')

    def test_pages_of_any_size_join_to_the_whole_answer(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            legacy = cognition.scenario(store)
            whole = (input_ack.task_view("all"), input_ack.turn_dump(), input_ack.request_rows("all"),
                     input_ack.actions("all"))
            for rows in (1, 2, 3, 7):
                with mock.patch.object(input_ack, "PAGE_ROWS", rows):
                    self.assertEqual((input_ack.task_view("all"), input_ack.turn_dump(), input_ack.request_rows("all"),
                                      input_ack.actions("all")), whole, rows)
            input_ack.close_ledger()
        socket = literal(str(self.server.socket))
        reads = [('"task-view"', "(all)"), ('"turn-dump"', "()"), ('"request-rows"', "(all)"),
                 ('"conversation-rows"', '("task-1" 0 40)'), ('"conversation-rows"', '("task-1" 2 0)'),
                 ('"conversation-history"', "(1 0)"), ('"actions"', '("all")'), ('"meta-prefix"', '("")')]
        code = []
        for number, (verb, arguments) in enumerate(reads):
            plain = "(ledger-client:call %s %s %s)" % (socket, verb, arguments)
            for count in (1, 2, 5):
                code.append("!(println! (SAME %d %d (== %s (ledger-client:pages %s %s %s 0 %d none))))"
                            % (number, count, plain, socket, verb, arguments, count))
        lines = self.client("\n".join(code))
        self.assertEqual(lines, ["(SAME %d %d true)" % (number, count) for number in range(len(reads))
                                 for count in (1, 2, 5)])
        self.assertTrue(legacy)

    def test_client_byte_limit_pages_from_cached_answer_and_never_pages_writes(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            cognition.scenario(store)
            for number in range(3, 15):
                snap = store.capture("task-1", "b%d" % number, cognition.ROUTER, 0)
                commands = [["shell", "ls %d" % number]]
                ticket = store.decide(snap["serial"], {"kind": "command", "tool": "batch",
                                                       "command": {"commands": commands, "limit": 1}}, 0)
                cognition.task_context.proposal(store, snap["serial"], "task-1", commands, "admitted")
                cognition.record_attempt(store, ticket, 0, commands[0],
                                         {"disposition": "returned", "result": ("row %d é\n" % number) * 350})
                store.report(ticket, "batch %d" % number)
            input_ack.close_ledger()

        socket = literal(str(self.server.socket))
        probe = self.client(
            '!(let* (($ans (ledger-client:call %s "conversation-rows" ("task-1" 0 6000)))\n'
            '        ((StoreEncoded $bytes) (store:encode (LedgerAnswersV1 ($ans)))))\n'
            '   (println! (NBYTES (str:byte-length $bytes))))\n' % socket
        )
        total_bytes = int(probe[0].split()[1].rstrip(")"))
        allowance = total_bytes // 4
        self.assertGreater(allowance, 4096)

        before = self.client('!(println! (ledger-client:call %s "page-cache-stats" ()))' % socket)[0]
        _, c0, h0, _ = before.strip("()").split()
        c0, h0 = int(c0), int(h0)

        paged = self.client(
            '!(println! (ROWSDATA (ledger-client:call %s "conversation-rows" ("task-1" 0 5000))))\n' % socket,
            value_bytes=allowance,
        )
        after = self.client(
            '!(println! (ledger-client:call %s "page-cache-stats" ()))\n'
            '!(println! (ROWSDATA (ledger-client:call %s "conversation-rows" ("task-1" 0 5000))))\n' % (socket, socket)
        )
        self.assertEqual(paged[0], after[1])
        _, c1, h1, _ = after[0].strip("()").split()
        c1, h1 = int(c1), int(h1)
        # Computed once on the initial LedgerCall (never recomputed across LedgerPage slices),
        # and completed in ~4 slices from the server page cache.
        self.assertEqual(c1 - c0, 1)
        self.assertIn(h1 - h0, (4, 5))

        # 100 conversation-rows calls over the large ledger leave server RSS flat within a tight bound.
        def rss_kb(pid):
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
            return 0

        warmup_and_100 = (
            '(= (repeat-rows $s $n)\n'
            '   (if (<= $n 0) ok\n'
            '       (let $_ (ledger-client:call $s "conversation-rows" ("task-1" 0 5000))\n'
            '         (repeat-rows $s (- $n 1)))))\n'
        )
        self.client(warmup_and_100 + '!(println! (WARM (repeat-rows %s 5)))\n' % socket)
        rss_before = rss_kb(self.server.process.pid)
        self.client(warmup_and_100 + '!(println! (HUNDRED (repeat-rows %s 100)))\n' % socket)
        rss_after = rss_kb(self.server.process.pid)
        self.assertLessEqual(rss_after - rss_before, 4096, f"RSS grew from {rss_before} KB to {rss_after} KB")

        # A write whose reply exceeds the client's byte allowance is never paged and never retried as lost.
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            for idx in range(80):
                store.accept("retire-big:%04d" % idx, "r")
            input_ack.close_ledger()
        stats_pre_write = self.client('!(println! (ledger-client:call %s "page-cache-stats" ()))' % socket)[0]
        write_lines = self.client(
            '!(println! (WRITE_OVER (ledger-client:call %s "request-retire" ("retire-big:" "retired" "req-retire-over"))))\n'
            % socket,
            value_bytes=1024,
        )
        stats_post_write = self.client('!(println! (ledger-client:call %s "page-cache-stats" ()))' % socket)[0]
        self.assertEqual(
            write_lines[0],
            "(WRITE_OVER (Error ledger-client:call (unanswered ((ServiceFailedV1 reply-limit)))))",
        )
        self.assertEqual(stats_pre_write, stats_post_write)

        # Client decode-capacity path: ((StoreRemoteFailed limit)) pages a pageable read once and never pages a write.
        expected_post_write = self.client(
            '!(println! (ROWSDATA (ledger-client:call %s "conversation-rows" ("task-1" 0 5000))))\n' % socket
        )[0]
        decode_limit_lines = self.client(
            "!(match &self (= (ledger-client:attempt $s $route $req) $body)\n"
            "   (progn\n"
            "     (add-atom &self (= (fixture:actual-attempt $s $route $req) $body))\n"
            "     (remove-atom &self (= (ledger-client:attempt $s $route $req) $body))))\n"
            "(: fixture:actual-attempt (-> %%Undefined%% %%Undefined%% %%Undefined%% %%Undefined%%))\n"
            "(= (ledger-client:attempt $s $route $req)\n"
            "   (if (== $route LedgerCall)\n"
            "       (let $_ (println! (DECODE_LIMIT_ATTEMPT $req))\n"
            "         (Error ledger-client:call (unanswered ((StoreRemoteFailed limit)))))\n"
            "       (fixture:actual-attempt $s $route $req)))\n"
            '!(println! (ROWSDATA (ledger-client:call %s "conversation-rows" ("task-1" 0 5000))))\n'
            '!(println! (WRITE_LIMIT (ledger-client:call %s "request-retire" ("retire-big:" "retired" "req-retire-limit"))))\n'
            % (socket, socket)
        )
        attempts = [line for line in decode_limit_lines if line.startswith("(DECODE_LIMIT_ATTEMPT ")]
        rows_out = [line for line in decode_limit_lines if line.startswith("(ROWSDATA ")]
        write_out = [line for line in decode_limit_lines if line.startswith("(WRITE_LIMIT ")]
        self.assertEqual(
            attempts,
            [
                '(DECODE_LIMIT_ATTEMPT (LedgerCallV1 "conversation-rows" ("task-1" 0 5000)))',
                '(DECODE_LIMIT_ATTEMPT (LedgerCallV1 "request-retire" ("retire-big:" "retired" "req-retire-limit")))',
            ],
        )
        self.assertEqual(rows_out, [expected_post_write])
        self.assertEqual(
            write_out,
            ["(WRITE_LIMIT (Error ledger-client:call (unanswered ((StoreRemoteFailed limit)))))"],
        )


if __name__ == "__main__":
    unittest.main()
