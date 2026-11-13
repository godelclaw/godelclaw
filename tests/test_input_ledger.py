"""The native input ledger module, src/input_ledger.metta, run directly: each case runs one
operation sequence in one cetta process on a temporary library-db store and checks every
answer and the final records."""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import contextlib
from contextlib import closing
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import input_ack
from observed_strategy import Ledger

MODULE = ROOT / "src/input_ledger.metta"
ENGINE = os.environ.get("CETTA_BIN")
ROW = re.compile(r'\(row "((?:[^"\\]|\\.)*)" ([-0-9.e+]+)\)', re.S)
ANSWER = re.compile(r'^\(answer (\d+) (\S+)\)$', re.M)


def literal(text):
    """A MeTTa string literal: backslash, double quote and newline are escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def unprint(text):
    return re.sub(r"\\(.)", r"\1", text, flags=re.S)


def mark(ids, now):
    return ("mark", tuple(ids), now)


def committed(ids):
    return ("committed", tuple(ids))


REOPEN = ("reopen",)


class sqlite_environment(mock.patch.dict):
    """mock.patch.dict(os.environ, values) for the SQLite oracle: also without
    METTACLAW_INPUT_LEDGER_SOCKET, which would send input_ack.py to the native ledger."""
    def __init__(self, values):
        super().__init__(os.environ, values)

    def __enter__(self):
        result = super().__enter__()
        os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
        return result

    def start(self):
        result = super().start()
        os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
        return result



def idlist(ids):
    return "(" + " ".join(literal(value) for value in ids) + ")"


def script(path, operations, *, ensure=True):
    """One cetta program: per-step answers, then the final rows of `inputs`."""
    steps, handle = ['($db1 (db:open %s))' % literal(path)], 1
    if ensure:
        steps.append('($_ (println! (ensure (input-ledger:ensure $db1))))')
    for number, operation in enumerate(operations):
        current = "$db%d" % handle
        if operation[0] == "mark":
            steps.append('($_ (println! (answer %d (input-ledger:mark-processed %s %s %r))))'
                         % (number, current, idlist(operation[1]), operation[2]))
        elif operation[0] == "committed":
            steps.append('($_ (println! (answer %d (input-ledger:committed %s %s))))'
                         % (number, current, idlist(operation[1])))
        else:
            handle += 1
            steps.append('($_ (db:close %s))' % current)
            steps.append('($db%d (db:open %s))' % (handle, literal(path)))
            steps.append('($_ (input-ledger:ensure $db%d))' % handle)
    current = "$db%d" % handle
    steps.append('($view (db:snapshot %s inputs))' % current)
    steps.append('($_ (println! (all (collapse (match $view (processed-input $id $time) (row $id $time))))))')
    steps.append('($_ (db:close $view))')
    return ("!(import! &self %s)\n!(let* (%s)\n   (db:close %s))\n"
            % (MODULE.with_suffix(""), "\n        ".join(steps), current))


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class InputLedgerTest(unittest.TestCase):
    def setUp(self):
        # db:open creates the store directory itself, in any ordinary directory.
        self.directory = tempfile.mkdtemp(prefix="input-ledger-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.store = str(Path(self.directory) / "store")

    def cetta(self, operations, name="case", *, ensure=True):
        program = Path(self.directory) / (name + ".metta")
        program.write_text(script(self.store, operations, ensure=ensure), encoding="utf-8")
        done = subprocess.run([ENGINE, "--lang", "petta", str(program)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("(Error", done.stdout, done.stdout[:2000])
        answers = {int(number): value for number, value in ANSWER.findall(done.stdout)}
        wanted = [n for n, op in enumerate(operations) if op[0] != "reopen"]
        self.assertEqual(sorted(answers), wanted, "an operation returned nothing:\n" + done.stdout[:2000])
        for value in answers.values():
            self.assertIn(value, ("true", "false"))
        rows = {unprint(identity): float(time) for identity, time in ROW.findall(done.stdout)}
        self.assertEqual(len(rows), done.stdout.count("(row "), "unparsed rows:\n" + done.stdout[:2000])
        return [answers[n] == "true" for n in wanted], rows

    def same(self, operations):
        """The answers and the final id->time rows of one native run."""
        return self.cetta(operations)

    def closed_handle_answers(self):
        """Answers of mark-processed and committed on a closed handle, then on a reopened one."""
        program = Path(self.directory) / "closed.metta"
        program.write_text(
            "!(import! &self %s)\n"
            "!(let* (($db (db:open %s))\n"
            "        ($_ (input-ledger:ensure $db))\n"
            "        ($_ (db:close $db))\n"
            '        ($_ (println! (marked (collapse (input-ledger:mark-processed $db ("u1") 100.0)))))\n'
            '        ($_ (println! (committed (collapse (input-ledger:committed $db ("u1"))))))\n'
            "        ($again (db:open %s))\n"
            '        ($_ (println! (reopened (collapse (input-ledger:committed $again ("u1"))))))\n'
            "        ($_ (db:close $again)))\n"
            "   done)\n" % (MODULE.with_suffix(""), literal(self.store), literal(self.store)), encoding="utf-8")
        done = subprocess.run([ENGINE, "--lang", "petta", str(program)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return dict(re.findall(r"^\((marked|committed|reopened) (.*)\)$", done.stdout, re.M))

    def test_mark_processed_on_a_closed_handle_returns_the_transaction_error(self):
        # input_ack raises there and its caller takes success as durably marked.
        answers = self.closed_handle_answers()
        self.assertEqual(answers.get("marked"), "((Error db:transaction (StoreRefused closed-or-busy)))")
        self.assertEqual(answers.get("reopened"), "(false)", "the refused mark must not have been written")

    def test_committed_on_a_closed_handle_returns_the_snapshot_error(self):
        # An unavailable checkpoint must not mean "not processed" (input_ack.committed).
        answers = self.closed_handle_answers()
        self.assertEqual(answers.get("committed"), "((Error db:snapshot (StoreRefused closed-or-busy)))")

    def test_a_batch_records_each_id_once_at_its_time(self):
        answers, rows = self.same([mark(["one", "two"], 10.5), committed(["one"]), committed(["one", "two"]),
                                   committed(["one", "three"]), committed(["three"])])
        self.assertEqual(answers, [True, True, True, False, False])
        self.assertEqual(rows, {"one": 10.5, "two": 10.5})

    def test_duplicates_in_a_batch_and_across_batches_keep_the_first_time(self):
        answers, rows = self.same([
            mark(["a", "b", "a", "a"], 100.0), mark(["b", "c", "c"], 200.0),
            mark(["a", "c", "d"], 300.5), committed(["a", "b", "c", "d"]), committed(["a", "e"])])
        self.assertEqual(rows, {"a": 100.0, "b": 100.0, "c": 200.0, "d": 300.5})
        self.assertEqual(answers[-2:], [True, False])

    def test_empty_string_ids_are_skipped_everywhere(self):
        answers, rows = self.same([
            mark([""], 1.0), committed([""]), committed([]), mark(["", "x", ""], 2.0),
            committed(["", "x"]), committed(["", "x", "y"]), committed(["", ""])])
        self.assertEqual(rows, {"x": 2.0})
        self.assertEqual(answers, [True, True, True, True, True, False, True])

    def test_an_empty_batch_and_an_empty_committed_list(self):
        answers, rows = self.same([mark([], 5.0), committed([]), mark(["k"], 6.0), mark([], 7.0), committed([])])
        self.assertEqual(rows, {"k": 6.0})
        self.assertEqual(answers, [True, True, True, True, True])

    def test_nothing_recorded_gives_empty_set_and_vacuous_truth(self):
        self.assertEqual(self.same([committed([]), committed(["a"])])[1], {})

    def test_quotes_backslashes_and_non_ascii_ids(self):
        awkward = ['say "hi"', 'back\\slash', '\\"', '"', '\\', 'trailing\\', 'tab\tnew\nline',
                   "é", "日本語", "🙂 emoji", "naïve ñ", "x y (z) ;c $v 'k", "a\\\\b", "1", "True", "()"]
        answers, rows = self.same([mark(awkward, 42.25), mark(list(reversed(awkward)), 43.0),
                                   committed(awkward), committed(awkward + ["say hi"]),
                                   committed(['say "hi"', "é"]), committed(["back\\\\slash"])])
        self.assertEqual(rows, {identity: 42.25 for identity in awkward})
        self.assertEqual(answers, [True, True, True, False, True, False])

    def test_a_reopen_gives_the_same_answers(self):
        answers, rows = self.same([
            mark(["a", "é", 'q"'], 10.0), committed(["a", "é", 'q"']), REOPEN,
            committed(["a", "é", 'q"']), committed(["a", "b"]), mark(["a", "b"], 20.0), REOPEN, REOPEN,
            committed(["a", "b", "é"])])
        self.assertEqual(rows, {"a": 10.0, "é": 10.0, 'q"': 10.0, "b": 20.0})
        self.assertEqual(answers, [True, True, True, False, True, True])

    def test_a_second_process_sees_the_first_ones_records(self):
        first = [mark(["one", "two", "ünï"], 10.0)]
        self.assertEqual(self.cetta(first, "first")[1], {"one": 10.0, "two": 10.0, "ünï": 10.0})
        second = [committed(["one", "ünï"]), committed(["one", "three"]), mark(["two", "three"], 30.0)]
        actual = self.cetta(second, "second", ensure=False)
        self.assertEqual(actual[0], [True, False, True])
        self.assertEqual(actual[1], {"one": 10.0, "two": 10.0, "ünï": 10.0, "three": 30.0})

    def test_two_hundred_ids_in_one_batch(self):
        ids = ["id-%03d" % n for n in range(200)]
        ids += ids[:20] + [""] * 3
        answers, rows = self.same([mark(ids, 77.5), committed(ids), committed(ids[:200] + ["id-200"]),
                                   mark(["id-200", "id-000"], 88.0), committed(ids + ["id-200"])])
        self.assertEqual(len(rows), 201)
        self.assertEqual(rows["id-000"], 77.5)
        self.assertEqual(rows["id-200"], 88.0)
        self.assertEqual(answers, [True, True, False, True, True])


# ---- input notices: key / submitted / receipt / processed_update_ids ----

COLUMNS = ("task", "update_id", "lane", "send_key", "created", "message_id", "processed",
           "delete_key", "state", "checked")


def register(task, update_id, lane, now):
    return ("register", task, update_id, lane, now)


def submitted(task):
    return ("submitted", task)


def receipt(key, outcome):
    """key: a literal string, or ("send", task, lane) for the notice's computed send key."""
    return ("receipt", key, outcome)


def set_delete_key(task, key):
    return ("set-delete-key", task, key)


def set_state(task, state):
    return ("set-state", task, state)


class FakeHash:
    """A sha256 whose digest is constant: two tasks then derive the same send key."""
    def __init__(self, data=b""):
        pass

    def digest(self):
        return bytes(range(1, 33))


def send_key_for(task, lane):
    """The send key of a task's receipt notice (key_identity: instance and task), as hashlib
    gives it; the responder derives it natively (src/notice_key.metta)."""
    identity = os.environ.get("METTACLAW_INSTANCE", "default") + ":input-receipt:" + task
    number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    return lane + ".%020d" % number


def native_operations(operations, *, collide=False):
    """The operations as the native module takes them: a register carries its send key, and a
    receipt names its key itself where the test wrote ("send", task, lane)."""
    def native(op):
        if op[0] == "register":
            return ("register", op[1], op[2], op[3], send_key_for(op[1], op[3]), op[4])
        if op[0] == "receipt" and isinstance(op[1], tuple):
            return ("receipt", send_key_for(op[1][1], op[1][2]), op[2])
        return op
    with mock.patch.object(hashlib, "sha256", FakeHash) if collide else contextlib.nullcontext():
        return [native(op) for op in operations]


def atom(value):
    """A MeTTa atom for a Python value of a delivery outcome."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return literal(value)
    return "(" + " ".join(atom(item) if n else str(item) for n, item in enumerate(value)) + ")"


TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([()])|([^\s()"]+)', re.S)


def parse(text):
    """The atoms in `text` as nested lists: strings, numbers, `none` -> None, other symbols as str tuples."""
    stack, current = [], []
    for string, paren, bare in TOKEN.findall(text):
        if paren == "(":
            stack.append(current)
            current = []
        elif paren == ")":
            done, current = current, stack.pop()
            current.append(done)
        elif bare:
            if bare == "none":
                current.append(None)
            elif re.fullmatch(r"-?\d+", bare):
                current.append(int(bare))
            elif re.fullmatch(r"-?[\d.]+(e[-+]?\d+)?", bare):
                current.append(float(bare))
            else:
                current.append(("symbol", bare))
        else:
            current.append(unprint(string))
    return current


SET_FIELD_HELPERS = """
; Test-only edits of one field of a notice's record.
(= (input-ledger-test:set-field $db $task $field $value)
   (db:transaction $db inputs
     (|-> ($space)
        (let* (((input-notice $t $u $l $s $c $m $p $d $st $ch)
                (car-atom (input-ledger:notices-of-task $space $task)))
               ($_ (remove-atom $space (input-notice $t $u $l $s $c $m $p $d $st $ch))))
          (let $new (if (== $field delete-key)
                        (input-notice $t $u $l $s $c $m $p $value $st $ch)
                        (input-notice $t $u $l $s $c $m $p $d $value $ch))
            (add-atom $space $new))))))
(= (input-ledger-test:set-delete-key $db $task $key) (input-ledger-test:set-field $db $task delete-key $key))
(= (input-ledger-test:set-state $db $task $state) (input-ledger-test:set-field $db $task state $state))
"""


def notice_script(path, operations):
    steps, handle = ['($db1 (db:open %s))' % literal(path), '($_ (println! (ensure (input-ledger:ensure $db1))))'], 1
    for number, operation in enumerate(operations):
        current, kind = "$db%d" % handle, operation[0]
        show = '($_ (println! (answer %d (collapse %%s))))' % number
        if kind == "register":
            _, task, update_id, lane, key, now = operation
            steps.append(show % ("(input-ledger:register %s %s %s %s %s %r)" % (
                current, literal(task), literal(update_id), literal(lane), literal(key), now)))
        elif kind == "submitted":
            steps.append(show % ("(input-ledger:submitted %s %s)" % (current, literal(operation[1]))))
        elif kind == "receipt":
            steps.append(show % ("(input-ledger:receipt %s %s %s)" % (current, literal(operation[1]), atom(operation[2]))))
        elif kind == "mark":
            steps.append(show % ("(input-ledger:mark-processed %s %s %r)" % (current, idlist(operation[1]), operation[2])))
        elif kind == "set-delete-key":
            steps.append('($_ (input-ledger-test:set-delete-key %s %s %s))' % (current, literal(operation[1]), literal(operation[2])))
        elif kind == "set-state":
            steps.append('($_ (input-ledger-test:set-state %s %s %s))' % (current, literal(operation[1]), operation[2]))
        else:
            handle += 1
            steps.append('($_ (db:close %s))' % current)
            steps.append('($db%d (db:open %s))' % (handle, literal(path)))
            steps.append('($_ (input-ledger:ensure $db%d))' % handle)
    current = "$db%d" % handle
    steps.append('($view (db:snapshot %s inputs))' % current)
    steps.append('($_ (println! (notices (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) '
                 '(input-notice $a $b $c $d $e $f $g $h $i $j))))))')
    steps.append('($_ (println! (processed (collapse (match $view (processed-input $id $time) (processed-input $id $time))))))')
    steps.append('($_ (db:close $view))')
    helpers = SET_FIELD_HELPERS
    return ("!(import! &self %s)\n%s!(let* (%s)\n   (db:close %s))\n"
            % (MODULE.with_suffix(""), helpers, "\n        ".join(steps), current))


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class InputNoticeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="input-notices-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.store = str(Path(self.directory) / "store")

    def cetta(self, operations):
        program = Path(self.directory) / "case.metta"
        program.write_text(notice_script(self.store, operations), encoding="utf-8")
        done = subprocess.run([ENGINE, "--lang", "petta", str(program)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("(Error", done.stdout, done.stdout[:3000])
        answers = {}
        for number, text in re.findall(r"^\(answer (\d+) (.*)\)$", done.stdout, re.M):
            answers[int(number)] = parse(text)
        wanted = [n for n, op in enumerate(operations) if op[0] in ("register", "submitted", "receipt", "mark")]
        self.assertEqual(sorted(answers), wanted, done.stdout[:3000])
        for number in wanted:
            self.assertEqual(len(answers[number][0]), 1, "not exactly one answer: %r" % answers[number])
        values = []
        for number in wanted:
            value = answers[number][0][0]
            values.append(True if value == ("symbol", "true") else value)
        lines = dict(re.findall(r"^\((notices|processed) (.*)\)$", done.stdout, re.M))
        notices = []
        for row in parse(lines["notices"])[0]:
            self.assertEqual(row[0], ("symbol", "input-notice"))
            self.assertIsInstance(row[9], tuple, "the state is a symbol: %r" % (row,))
            notices.append(tuple(row[1:9]) + (row[9][1], row[10]))
        notices.sort()
        processed = {row[1]: row[2] for row in parse(lines["processed"])[0]}
        return values, notices, processed

    def same(self, operations, *, collide=False):
        """The answers and the final notices of one native run."""
        values, notices, _ = self.cetta(native_operations(operations, collide=collide))
        self.assertEqual(len(values), sum(1 for op in operations if op[0] in ("register", "submitted", "receipt", "mark")))
        return values, notices

    def test_a_registered_notice_returns_its_key_and_keeps_the_first_creation_time(self):
        answers, notices = self.same([register("t1", "u1", "42.0", 100.5), register("t1", "u1", "42.0", 200.5)])
        self.assertTrue(answers[0].startswith("42.0.") and answers[1] == answers[0])
        self.assertEqual([(row[0], row[4], row[5], row[6], row[7], row[8]) for row in notices],
                         [("t1", 100.5, None, 0, None, "registered")])

    def test_a_lane_change_is_refused_and_leaves_the_notice(self):
        answers, notices = self.same([register("t1", "u1", "42.0", 10.0), register("t1", "u1", "43.0", 20.0),
                                      register("t1", "u1", "42.0", 30.0)])
        self.assertEqual(answers[1], "")
        self.assertEqual(answers[2], answers[0])
        self.assertEqual([(row[2], row[4]) for row in notices], [("42.0", 10.0)])

    def test_a_send_key_collision_between_two_tasks_returns_the_key_and_stores_one_notice(self):
        answers, notices = self.same([register("t1", "u1", "42.0", 10.0), register("t2", "u2", "42.0", 20.0),
                                      register("t2", "u2", "43.0", 30.0), register("t3", "u3", "44.0", 40.0)],
                                     collide=True)
        self.assertEqual(answers[0], answers[1])
        self.assertTrue(answers[0] and answers[2] and answers[2] != answers[0])
        # t2 collided at lane 42.0 (ignored, key still returned); at lane 43.0 its key is its own
        self.assertEqual([(row[0], row[2]) for row in notices], [("t1", "42.0"), ("t2", "43.0"), ("t3", "44.0")])

    def test_processed_before_register_gets_no_notice(self):
        answers, notices = self.same([mark(["u1"], 5.0), register("t1", "u1", "42.0", 10.0),
                                      register("t2", "u2", "42.0", 11.0), mark(["u2"], 12.0),
                                      register("t2", "u2", "42.0", 13.0), register("t3", "u2", "42.0", 14.0)])
        self.assertEqual(answers[1], "")
        self.assertEqual(answers[3:], [True, "", ""])
        self.assertEqual([(row[0], row[6]) for row in notices], [("t2", 1)])

    def test_marking_processed_marks_every_notice_of_the_update_id(self):
        answers, notices = self.same([
            register("t1", "u1", "42.0", 1.0), register("t2", "u1", "43.0", 2.0), register("t3", "u2", "42.0", 3.0),
            register("t4", "", "42.0", 4.0), mark(["u1", "", "u9", "u1"], 5.0), mark([""], 6.0)])
        self.assertEqual([(row[0], row[6]) for row in notices], [("t1", 1), ("t2", 1), ("t3", 0), ("t4", 0)])

    def test_a_repeated_register_after_a_restart_gives_the_same_key(self):
        answers, notices = self.same([register("t1", "u1", "42.0", 10.0), submitted("t1"), REOPEN,
                                      register("t1", "u1", "42.0", 500.0), REOPEN, register("t1", "u1", "42.0", 900.0)])
        self.assertEqual(answers[0], answers[2])
        self.assertEqual(answers[0], answers[3])
        self.assertEqual([(row[4], row[8]) for row in notices], [(10.0, "submitted")])

    def test_a_closed_handle_gives_the_transaction_error_from_every_notice_verb(self):
        program = Path(self.directory) / "closed.metta"
        program.write_text(
            "!(import! &self %s)\n"
            "!(let* (($db (db:open %s))\n        ($_ (input-ledger:ensure $db))\n        ($_ (db:close $db))\n"
            '        ($_ (println! (r (collapse (input-ledger:register $db "t" "u" "42.0" "K" 1.0)))))\n'
            '        ($_ (println! (s (collapse (input-ledger:submitted $db "t")))))\n'
            '        ($_ (println! (c (collapse (input-ledger:receipt $db "K" (delivered 5)))))))\n   done)\n'
            % (MODULE.with_suffix(""), literal(self.store)), encoding="utf-8")
        done = subprocess.run([ENGINE, "--lang", "petta", str(program)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        error = "((Error db:transaction (StoreRefused closed-or-busy)))"
        self.assertEqual(re.findall(r"^\((r|s|c) (.*)\)$", done.stdout, re.M), [("r", error), ("s", error), ("c", error)])

    def test_the_same_send_key_under_another_lane_is_refused(self):
        # Not reachable through key() (the lane is the key's prefix); the SQL rule still refuses it.
        values, notices, _ = self.cetta([
            ("register", "t1", "u1", "42.0", "K", 1.0), ("register", "t1", "u1", "43.0", "K", 2.0),
            ("register", "t1", "u1", "42.0", "K", 3.0), ("register", "t1", "u1", "42.0", "K2", 4.0)])
        self.assertEqual(values, ["K", "", "K", ""])
        self.assertEqual([(row[2], row[3], row[4]) for row in notices], [("42.0", "K", 1.0)])

    def test_submitted_only_moves_registered_forward(self):
        answers, notices = self.same([
            submitted("nobody"), register("t1", "u1", "42.0", 1.0), register("t2", "u2", "42.0", 2.0),
            register("t3", "u3", "42.0", 3.0), register("t4", "u4", "42.0", 4.0),
            set_state("t2", "deleted"), set_state("t3", "expired"),
            submitted("t1"), submitted("t1"), submitted("t2"), submitted("t3"), submitted("t4")])
        self.assertEqual([row[8] for row in notices], ["submitted", "deleted", "expired", "submitted"])

    def test_two_receipts_with_different_message_ids(self):
        key = ("send", "t1", "42.0")
        answers, notices = self.same([
            register("t1", "u1", "42.0", 1.0), receipt(key, ["delivered", 55]), receipt(key, ["delivered", 56]),
            receipt(key, ["delivered", 55]), receipt(key, ["delivered", 0]), receipt(key, ["delivered", -3]),
            register("t2", "u2", "42.0", 2.0), receipt(("send", "t2", "42.0"), ["delivered", 0]),
            receipt(("send", "t2", "42.0"), ["delivered", -1])])
        self.assertEqual(answers[1:6], [1, 0, 1, 0, 0])
        self.assertEqual(answers[7:], [0, 0])
        self.assertEqual([row[5] for row in notices], [55, None])

    def test_failed_and_malformed_outcomes_change_nothing(self):
        key = ("send", "t1", "42.0")
        outcomes = [["failed", "x"], ["delivered"], ["delivered", 1, 2], ["delivered", "7"], ["delivered", 7.0],
                    ["delivered", 7.5], ["delivered", True], ["delivered", None], "delivered", None, ["uncertain", 7]]
        answers, notices = self.same([register("t1", "u1", "42.0", 1.0)]
                                     + [receipt(key, outcome) for outcome in outcomes])
        self.assertEqual(answers[1:], [1] * len(outcomes))
        self.assertEqual([row[5] for row in notices], [None])

    def test_a_delete_receipt_before_and_after_processing(self):
        key = ("send", "t1", "42.0")
        answers, notices = self.same([
            register("t1", "u1", "42.0", 1.0), receipt(key, ["delivered", 55]), set_delete_key("t1", "42.0.del"),
            receipt("42.0.del", ["delivered", 55]), receipt("42.0.del", ["delivered", 0]), mark(["u1"], 2.0),
            receipt("42.0.del", ["delivered", 99]), receipt("42.0.del", ["failed"]), receipt("42.0.del", ["delivered", 55])])
        self.assertEqual(answers[2:4], [0, 0])
        self.assertEqual(answers[4:], [True, 0, 1, 1])
        self.assertEqual([(row[5], row[6], row[8]) for row in notices], [(55, 1, "deleted")])

    def test_a_delete_receipt_for_a_notice_without_a_message_id(self):
        answers, notices = self.same([
            register("t1", "u1", "42.0", 1.0), register("t2", "u2", "42.0", 2.0), mark(["u1", "u2"], 3.0),
            set_delete_key("t1", "d1"), set_delete_key("t2", "d2"),
            receipt("d1", ["delivered", 5]), receipt("d1", ["delivered", 0]), receipt("d2", ["delivered", 0])])
        self.assertEqual(answers[3:], [0, 1, 1])
        self.assertEqual([row[8] for row in notices], ["deleted", "deleted"])

    def test_an_unknown_key_gives_zero(self):
        answers, notices = self.same([
            receipt("nope", ["delivered", 5]), receipt("nope", ["failed"]), register("t1", "u1", "42.0", 1.0),
            receipt("nope", ["delivered", 5]), receipt("", ["delivered", 5]), receipt("42.0", ["delivered", 5])])
        self.assertEqual(answers[0:2] + answers[3:], [0, 0, 0, 0, 0])
        self.assertEqual([row[5] for row in notices], [None])

    def test_awkward_strings_and_a_second_registered_lifecycle_across_reopens(self):
        lane = '4"2\\.0 é 日本'
        key = ("send", 't"1 \\', lane)
        answers, notices = self.same([
            register('t"1 \\', 'u"1', lane, 1.25), REOPEN, receipt(key, ["delivered", 9]), submitted('t"1 \\'),
            mark(['u"1'], 2.5), set_delete_key('t"1 \\', 'del "k"'), REOPEN, receipt('del "k"', ["delivered", 9])])
        self.assertEqual([(row[0], row[2], row[5], row[6], row[7], row[8]) for row in notices],
                         [('t"1 \\', lane, 9, 1, 'del "k"', "deleted")])


# ---- keys, outcome normalization, due and check (the store half of pump) ----

def python_key(lane, identity):
    """lane + "." + "%020d" of the first 8 bytes of sha256(identity): the reference the native
    derivation (src/notice_key.metta) is checked against."""
    number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    return lane + ".%020d" % number


def send_key_of(instance, lane, task):
    return python_key(lane, instance + ":input-receipt:" + task)


def delete_key_of(lane, send_key):
    return python_key(lane, "notice-delete:" + send_key)


def name_with_digest(make_identity, accept):
    """The first 'n<counter>' whose identity's sha256 digest satisfies `accept`."""
    counter = 0
    while True:
        name = "n%d" % counter
        if accept(hashlib.sha256(make_identity(name).encode()).digest()):
            return name
        counter += 1


def run_cetta(directory, name, program):
    path = Path(directory) / (name + ".metta")
    path.write_text(program, encoding="utf-8")
    done = subprocess.run([ENGINE, "--lang", "petta", str(path)], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class InputKeyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="input-keys-")
        self.addCleanup(shutil.rmtree, self.directory, True)

    def derive(self, send_cases, delete_cases):
        steps = []
        for number, (instance, lane, task) in enumerate(send_cases):
            steps.append('($_ (println! (sk %d (input-ledger:send-key %s %s %s))))'
                         % (number, literal(instance), literal(lane), literal(task)))
        for number, (lane, send_key) in enumerate(delete_cases):
            steps.append('($_ (println! (dk %d (input-ledger:delete-key %s %s))))'
                         % (number, literal(lane), literal(send_key)))
        out = run_cetta(self.directory, "keys", "!(import! &self %s)\n!(let* (%s)\n   done)\n"
                        % (MODULE.with_suffix(""), "\n        ".join(steps)))
        self.assertNotIn("(Error", out, out[:2000])
        found = {(kind, int(number)): unprint(value) for kind, number, value in
                 re.findall(r'^\((sk|dk) (\d+) "((?:[^"\\]|\\.)*)"\)$', out, re.M)}
        return ([found.get(("sk", n)) for n in range(len(send_cases))],
                [found.get(("dk", n)) for n in range(len(delete_cases))])

    def test_keys_equal_the_python_derivation_over_awkward_strings(self):
        zero_byte = name_with_digest(lambda n: "default:input-receipt:" + n, lambda d: d[0] == 0)
        two_zero_bytes = name_with_digest(lambda n: "default:input-receipt:" + n, lambda d: d[:2] == b"\0\0")
        top_bit = name_with_digest(lambda n: "default:input-receipt:" + n, lambda d: d[0] >= 0x80)
        top_set_low = name_with_digest(lambda n: "default:input-receipt:" + n, lambda d: d[0] == 0xFF)
        send_cases = [("default", "42.0", "t1"), ("default", "42.0", zero_byte), ("default", "42.0", two_zero_bytes),
                      ("default", "42.0", top_bit), ("default", "42.0", top_set_low),
                      ("", "", ""), ("default", "", "t"), ("", "42.0", ""), ("é", "日本", "🙂"),
                      ("instance-é", "4\u0301.0", "naïve ñ 🙂 日本語"), ("i" * 300, "l" * 128, "t" * 1000),
                      ('q"uote', 'back\\slash', "tab\there"), ("x y (z) ;c $v 'k", "42.0", "()"),
                      ("default", "-100.5", "1"), ("default", "42.0", "t1")]
        delete_cases = [("42.0", "42.0.13380835847708692071"), ("", ""), ("日本", "日本.é"), ("l" * 128, "k" * 1000),
                        ('q"', 'b\\s'), ("42.0", name_with_digest(lambda n: "notice-delete:" + n, lambda d: d[0] == 0)),
                        ("42.0", name_with_digest(lambda n: "notice-delete:" + n, lambda d: d[:2] == b"\0\0")),
                        ("42.0", name_with_digest(lambda n: "notice-delete:" + n, lambda d: d[0] >= 0x80)),
                        ("42.0", name_with_digest(lambda n: "notice-delete:" + n, lambda d: d[0] == 0xFF))]
        sends, deletes = self.derive(send_cases, delete_cases)
        self.assertEqual(sends, [send_key_of(*case) for case in send_cases])
        self.assertEqual(deletes, [delete_key_of(*case) for case in delete_cases])
        # the cases cover the padding and the unsigned reading
        digits = lambda key: key.rsplit(".", 1)[1]
        for key in sends[1:3] + deletes[5:7]:
            self.assertTrue(digits(key).startswith("0"), key)
        self.assertTrue(digits(sends[1]).startswith("00"), sends[1])
        self.assertTrue(digits(sends[2]).startswith("00000"), sends[2])
        for key in (sends[3], sends[4], deletes[7], deletes[8]):
            self.assertGreaterEqual(int(digits(key)), 2 ** 63, key)
        self.assertGreater(int(digits(sends[4])), 2 ** 64 - 2 ** 56)
        self.assertTrue(all(len(digits(key)) == 20 for key in sends + deletes))


NORMALIZED = [
    # (expression, the answers printed for it)
    ('(input-ledger:register-answer "K" "K")', '("K")'),
    ('(input-ledger:register-answer "" "K")', '("")'),
    ('(input-ledger:register-answer (Error db:transaction (StoreRefused closed-or-busy)) "K")',
     "((Error db:transaction (StoreRefused closed-or-busy)))"),
    ('(input-ledger:register-answer (db:uncertain rid inputs op) "K")',
     "((Error input-ledger:register (db:uncertain rid inputs op)))"),
    ('(input-ledger:register-answer (db:uncertain-command rid (DbTransactionV1 inputs op)) "K")',
     "((Error input-ledger:register (db:uncertain rid inputs op)))"),
    ('(input-ledger:register-answer "another key" "K")', '((Error input-ledger:register "another key"))'),
    ("(input-ledger:register-answer 7 \"K\")", '((Error input-ledger:register 7))'),
    ("(input-ledger:register-answer True \"K\")", '((Error input-ledger:register true))'),
    ("(input-ledger:register-answer done \"K\")", '((Error input-ledger:register done))'),
    ("(input-ledger:receipt-answer 1)", "(1)"),
    ("(input-ledger:receipt-answer 0)", "(0)"),
    ("(input-ledger:receipt-answer (Error db:transaction (StoreRefused closed-or-busy)))",
     "((Error db:transaction (StoreRefused closed-or-busy)))"),
    ("(input-ledger:receipt-answer (db:uncertain rid inputs op))",
     "((Error input-ledger:receipt (db:uncertain rid inputs op)))"),
    ("(input-ledger:receipt-answer 2)", "((Error input-ledger:receipt 2))"),
    ("(input-ledger:receipt-answer -1)", "((Error input-ledger:receipt -1))"),
    ("(input-ledger:receipt-answer 1.5)", "((Error input-ledger:receipt 1.5))"),
    ("(input-ledger:receipt-answer True)", "((Error input-ledger:receipt true))"),
    ('(input-ledger:receipt-answer "1")', '((Error input-ledger:receipt "1"))'),
    ("(input-ledger:receipt-answer done)", "((Error input-ledger:receipt done))"),
    ("(input-ledger:done-answer input-ledger:submitted done)", "(true)"),
    ("(input-ledger:done-answer input-ledger:submitted (Error db:transaction (StoreRefused closed-or-busy)))",
     "((Error db:transaction (StoreRefused closed-or-busy)))"),
    ("(input-ledger:done-answer input-ledger:submitted (db:uncertain rid inputs op))",
     "((Error input-ledger:submitted (db:uncertain rid inputs op)))"),
    ("(input-ledger:done-answer input-ledger:mark-processed True)", "((Error input-ledger:mark-processed true))"),
    ('(input-ledger:done-answer input-ledger:mark-processed "done")', '((Error input-ledger:mark-processed "done"))'),
    ("(input-ledger:done-answer input-ledger:mark-processed 0)", "((Error input-ledger:mark-processed 0))"),
    ("(input-ledger:check-answer expired)", "(expired)"),
    ("(input-ledger:check-answer wait)", "(wait)"),
    ('(input-ledger:check-answer (delete "k" 5))', '((delete "k" 5))'),
    ("(input-ledger:check-answer (Error db:transaction (StoreRefused closed-or-busy)))",
     "((Error db:transaction (StoreRefused closed-or-busy)))"),
    ("(input-ledger:check-answer (db:uncertain rid inputs op))",
     "((Error input-ledger:check (db:uncertain rid inputs op)))"),
    ("(input-ledger:check-answer (delete 1 5))", "((Error input-ledger:check (delete 1 5)))"),
    ("(input-ledger:check-answer (delete \"k\"))", '((Error input-ledger:check (delete "k")))'),
    ("(input-ledger:check-answer done)", "((Error input-ledger:check done))"),
    ('(input-ledger:check-answer "wait")', '((Error input-ledger:check "wait"))'),
]


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class OutcomeNormalizationTest(unittest.TestCase):
    """An uncertain command cannot be provoked from a local store, so the normalizers run on synthetic answers."""
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="input-outcomes-")
        self.addCleanup(shutil.rmtree, self.directory, True)

    def test_every_verb_has_one_failure_shape(self):
        steps = ['($_ (println! (r %d (collapse %s))))' % (n, expression) for n, (expression, _) in enumerate(NORMALIZED)]
        out = run_cetta(self.directory, "norm", "!(import! &self %s)\n!(let* (%s)\n   done)\n"
                        % (MODULE.with_suffix(""), "\n        ".join(steps)))
        printed = dict((int(n), text) for n, text in re.findall(r"^\(r (\d+) (.*)\)$", out, re.M))
        for number, (expression, expected) in enumerate(NORMALIZED):
            self.assertEqual(printed.get(number), expected, expression)

    def test_the_verbs_are_built_from_the_normalizers_not_beside_them(self):
        source = MODULE.read_text(encoding="utf-8")
        for verb, normalizer in (("register", "register-answer"), ("receipt", "receipt-answer"),
                                 ("check", "check-answer"), ("submitted", "done-answer"),
                                 ("mark-processed", "done-answer")):
            body = source[source.index("(= (input-ledger:%s $db" % verb):]
            self.assertIn("(input-ledger:" + normalizer, body.split("\n\n")[0], verb)
        self.assertNotRegex(re.sub(r";[^\n]*", "", source), r"input-ledger:[a-z-]+\?")

    def test_a_closed_handle_gives_the_engines_error_from_check_and_due(self):
        out = run_cetta(self.directory, "closed", (
            "!(import! &self %s)\n"
            "!(let* (($db (db:open %s))\n        ($_ (input-ledger:ensure $db))\n        ($_ (db:close $db))\n"
            '        ($_ (println! (c (collapse (input-ledger:check $db "t" 1.0 172800)))))\n'
            '        ($_ (println! (d (collapse (input-ledger:due $db 32))))))\n   done)\n')
            % (MODULE.with_suffix(""), literal(str(Path(self.directory) / "store"))))
        self.assertEqual(re.findall(r"^\((c|d) (.*)\)$", out, re.M),
                         [("c", "((Error db:transaction (StoreRefused closed-or-busy)))"),
                          ("d", "((Error db:snapshot (StoreRefused closed-or-busy)))")])


# ---- due and check, in input_ack.pump()'s order ----

NOTICE_TABLE = ("CREATE TABLE IF NOT EXISTS input_notices (task TEXT PRIMARY KEY, update_id TEXT NOT NULL,"
                " lane TEXT NOT NULL, send_key TEXT UNIQUE NOT NULL, created REAL NOT NULL,"
                " message_id INTEGER, processed INTEGER NOT NULL DEFAULT 0, delete_key TEXT,"
                " state TEXT NOT NULL DEFAULT 'registered', checked REAL NOT NULL DEFAULT 0)")
NOW = 1000000.0
MAX_AGE = 172800


def row(task, *, created, message=None, processed=0, delete=None, state="registered", checked=0.0,
        lane="42.0", update="u", send_key=None):
    """One input_notices row in column order; the send key is the real derivation unless given."""
    return (task, update, lane, send_key if send_key is not None else send_key_of("default", lane, task),
            created, message, processed, delete, state, checked)


def notice_atom(values):
    task, update, lane, send_key, created, message, processed, delete, state, checked = values
    return "(input-notice %s %s %s %s %r %s %d %s %s %r)" % (
        literal(task), literal(update), literal(lane), literal(send_key), created,
        "none" if message is None else message, processed, "none" if delete is None else literal(delete),
        state, checked)


def delivery(key, outcome):
    return ["delivery", key, 0, 1, outcome]


class FakeChannel:
    """Serves pending delivery receipts until acknowledged; records every SUBMIT."""
    def __init__(self, pending, on_first_submit):
        self.waiting, self.acknowledged, self.submits = list(pending), [], []
        self.on_first_submit = on_first_submit

    def pending(self, limit=64, after=""):
        return [item for item in self.waiting if item[0] not in self.acknowledged][:limit]

    def acknowledge(self, task):
        self.acknowledged.append(task)

    def rpc(self, code, name="", body=""):
        import durable_telegram as protocol
        assert code == protocol.SUBMIT, code
        self.submits.append((name, body))
        if len(self.submits) == 1 and self.on_first_submit:
            self.on_first_submit()
        return (protocol.STORED, "", "")


PUMP_HELPERS = SET_FIELD_HELPERS + """
(= (input-ledger-test:seed $db $notice)
   (db:transaction $db inputs (|-> ($space) (let $_ (add-atom $space $notice) done))))

; Edits (task field value) run once, right after the first check that answers delete.
(= (input-ledger-test:apply $db $edits)
   (if (== $edits ())
       done
       (let* ((($head $tail) (decons-atom $edits))
              (($task $field $value) $head)
              ($_ (input-ledger-test:set-field $db $task $field $value)))
         (input-ledger-test:apply $db $tail))))
(= (input-ledger-test:after $db $answer $edits)
   (case $answer
     (((delete $key $id) (let $_ (input-ledger-test:apply $db $edits) ()))
      ($other $edits))))

; pump's loop over the rows of due, one check each.
(= (input-ledger-test:run $db $rows $number $now $step $edits)
   (if (== $rows ())
       done
       (let* ((($head $tail) (decons-atom $rows))
              ((input-notice $t $u $l $s $c $m $p $d $st $ch) $head)
              ($answer (input-ledger:check $db $t $now 172800))
              ($_ (println! (check $number $t $answer)))
              ($rest (input-ledger-test:after $db $answer $edits)))
         (input-ledger-test:run $db $tail (+ $number 1) (+ $now $step) $step $rest))))
"""


def pump_script(path, rows, first_collect, second_collect, edits, now, step):
    steps = ['($db1 (db:open %s))' % literal(path), '($_ (input-ledger:ensure $db1))']
    steps += ['($_ (input-ledger-test:seed $db1 %s))' % notice_atom(values) for values in rows]
    for label, collect in (("one", first_collect),):
        steps += ['($_ (println! (receipt-%s %d (input-ledger:receipt $db1 %s %s))))'
                  % (label, n, literal(key), atom(outcome)) for n, (key, outcome) in enumerate(collect)]
    steps.append('($rows (input-ledger:due $db1 32))')
    steps.append('($_ (println! (due $rows)))')
    edit_text = "(" + " ".join("(%s %s %s)" % (literal(task), column.replace("_", "-"), value if column == "state" else literal(value))
                               for task, column, value in edits) + ")"
    steps.append('($_ (input-ledger-test:run $db1 $rows 0 %r %r %s))' % (now, step, edit_text))
    steps += ['($_ (println! (receipt-two %d (input-ledger:receipt $db1 %s %s))))'
              % (n, literal(key), atom(outcome)) for n, (key, outcome) in enumerate(second_collect)]
    steps.append('($view (db:snapshot $db1 inputs))')
    steps.append('($_ (println! (notices (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) '
                 '(input-notice $a $b $c $d $e $f $g $h $i $j))))))')
    steps.append('($_ (db:close $view))')
    steps.append('($_ (db:close $db1))')
    return "!(import! &self %s)\n%s!(let* (%s)\n   done)\n" % (
        MODULE.with_suffix(""), PUMP_HELPERS, "\n        ".join(steps))


def notice_values(record):
    assert record[0] == ("symbol", "input-notice") and isinstance(record[9], tuple), record
    return tuple(record[1:9]) + (record[9][1], record[10])


@unittest.skipUnless(ENGINE, "set CETTA_BIN to the cetta-ours engine")
class PumpStoreHalfTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="input-pump-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.runs = 0

    def native(self, rows, first_collect, second_collect, edits, clock):
        self.runs += 1
        self.store = str(Path(self.directory) / ("store%d" % self.runs))
        now, step = (clock[1] + 1, 2.0) if clock[0] == "ticks" else (clock[1], 0.0)
        out = run_cetta(self.directory, "pump", pump_script(self.store, rows, first_collect, second_collect,
                                                           edits, now, step))
        self.assertNotIn("(Error", out, out[:3000])
        receipts = {label: {int(n): int(value) for n, value in re.findall(r"^\(receipt-%s (\d+) (\d+)\)$" % label, out, re.M)}
                    for label in ("one", "two")}
        checks = []
        for number, task, answer in re.findall(r'^\(check (\d+) ("(?:[^"\\]|\\.)*") (.*)\)$', out, re.M):
            checks.append((int(number), unprint(task[1:-1]), parse(answer)[0]))
        due = parse(re.search(r"^\(due (.*)\)$", out, re.M).group(1))[0]
        final = parse(re.search(r"^\(notices (.*)\)$", out, re.M).group(1))[0]
        return receipts, sorted(checks), due, sorted(notice_values(record) for record in final)

    def same(self, rows, pending=(), edits=(), clock=("const", NOW)):
        """pump's steps on the native module: collect the pending receipts (a truthy answer
        acknowledges its task), check every due notice, collect what is still pending. Answers
        the outcome (submits, receipts, acknowledged, rows), the checks and due."""
        collect_one = [(item[1][1], item[1][4]) for item in pending]
        first, _, _, _ = self.native(rows, collect_one, [], edits, clock)
        acknowledged = [item[0] for n, item in enumerate(pending) if first["one"][n]]
        left = [item for n, item in enumerate(pending) if not first["one"][n]]
        collect_two = [(item[1][1], item[1][4]) for item in left]
        receipts, checks, due, final = self.native(rows, collect_one, collect_two, edits, clock)
        acknowledged += [item[0] for n, item in enumerate(left) if receipts["two"][n]]
        outcome = {"receipts": [(key, receipts["one"][n]) for n, (key, _) in enumerate(collect_one)]
                               + [(key, receipts["two"][n]) for n, (key, _) in enumerate(collect_two)],
                   "acknowledged": acknowledged, "rows": final}
        # due answered whole input-notice records, checked in due's order
        self.assertEqual([record[1] for record in due], [task for _, task, _ in checks])
        self.assertTrue(all(len(record) == 11 and record[0] == ("symbol", "input-notice") for record in due))
        # the deletions pump would submit, in order
        submits = []
        for _, task, answer in checks:
            if isinstance(answer, list):
                self.assertEqual(answer[0], ("symbol", "delete"))
                submits.append((answer[1], json.dumps([["delete", answer[2]]], separators=(",", ":"))))
        outcome["submits"] = submits
        return outcome, checks, due

    def test_due_orders_by_processed_then_checked_then_created(self):
        rows = [row("a", created=999000.0, message=11, processed=1, checked=50.0),
                row("b", created=999100.0, message=12, processed=1, checked=10.0),
                row("c", created=999050.0, message=13, processed=1, checked=10.0),
                row("d", created=999500.0, processed=0, checked=0.0),
                row("e", created=999400.0, message=15, processed=0, checked=0.0),
                row("f", created=999000.0, processed=0, checked=5.0),
                row("g", created=999000.0, processed=1, checked=70.0),
                row("h", created=999200.0, message=18, processed=1, checked=10.0, state="submitted"),
                row("i", created=999000.0, message=19, processed=1, state="deleted"),
                row("j", created=999000.0, message=20, processed=1, state="expired")]
        outcome, checks, due = self.same(rows, clock=("ticks", NOW))
        self.assertEqual([record[1] for record in due], ["c", "b", "h", "a", "g", "e", "d", "f"])
        # pump's own order: the checked stamps rise in that order, i and j untouched
        stamps = {record[0]: record[9] for record in outcome["rows"]}
        self.assertEqual(sorted(stamps, key=stamps.get)[:2], ["i", "j"])
        self.assertEqual(sorted((t for t in stamps if t not in "ij"), key=stamps.get),
                         ["c", "b", "h", "a", "g", "e", "d", "f"])
        self.assertEqual([key for key, _ in outcome["submits"]],
                         [delete_key_of("42.0", send_key_of("default", "42.0", t)) for t in "cbha"])

    def test_at_most_thirty_two_notices_are_checked_oldest_first(self):
        rows = [row("t%02d" % n, created=999000.0 + n, message=100 + n, processed=1) for n in range(40)]
        rows += [row("u%d" % n, created=998000.0 + n, processed=0) for n in range(5)]
        outcome, checks, due = self.same(rows)
        self.assertEqual([record[1] for record in due], ["t%02d" % n for n in range(32)])
        self.assertEqual(len(outcome["submits"]), 32)
        self.assertEqual(sum(1 for record in outcome["rows"] if record[9] == NOW), 32)
        self.assertEqual(self.limit_of(5), 5)

    def limit_of(self, limit):
        out = run_cetta(self.directory, "limit", (
            "!(import! &self %s)\n%s"
            "!(let* (($db (db:open %s))\n        ($_ (input-ledger:ensure $db))\n%s"
            '        ($rows (input-ledger:due $db %d))\n        ($n (size-atom $rows))\n        ($_ (println! (size $n))))\n   done)\n')
            % (MODULE.with_suffix(""), PUMP_HELPERS, literal(str(Path(self.directory) / "limit-store")),
               "".join('        ($_ (input-ledger-test:seed $db %s))\n' % notice_atom(row("x%d" % n, created=1.0))
                       for n in range(8)), limit))
        return int(re.search(r"^\(size (\d+)\)$", out, re.M).group(1))

    def test_a_limit_of_zero_or_the_empty_store_gives_the_empty_expression(self):
        out = run_cetta(self.directory, "empty", (
            "!(import! &self %s)\n%s"
            "!(let* (($db (db:open %s))\n        ($_ (input-ledger:ensure $db))\n"
            '        ($_ (println! (none (collapse (input-ledger:due $db 32)))))\n'
            "        ($_ (input-ledger-test:seed $db %s))\n"
            '        ($_ (println! (zero (collapse (input-ledger:due $db 0)))))\n'
            '        ($rows (input-ledger:due $db 1))\n        ($n (size-atom $rows))\n        ($_ (println! (one $n))))\n   done)\n')
            % (MODULE.with_suffix(""), PUMP_HELPERS, literal(str(Path(self.directory) / "empty-store")),
               notice_atom(row("x", created=1.0))))
        self.assertEqual(re.findall(r"^\((none|zero|one) (.*)\)$", out, re.M),
                         [("none", "(())"), ("zero", "(())"), ("one", "1")])

    def test_expiry_exactly_at_the_age_and_just_past_it(self):
        rows = [row("exact", created=NOW - MAX_AGE, message=1, processed=1),
                row("past", created=NOW - MAX_AGE - 0.5, message=2, processed=1),
                row("barely-past", created=NOW - MAX_AGE - 0.001, message=3, processed=1),
                row("barely-inside", created=NOW - MAX_AGE + 0.001, message=4, processed=1),
                row("exact-unprocessed", created=NOW - MAX_AGE),
                row("past-unprocessed", created=NOW - MAX_AGE - 1.0, state="submitted"),
                row("past-no-message", created=NOW - MAX_AGE - 1.0, processed=1)]
        outcome, checks, due = self.same(rows)
        states = {record[0]: record[8] for record in outcome["rows"]}
        self.assertEqual(states, {"exact": "registered", "past": "expired", "barely-past": "expired",
                                  "barely-inside": "registered", "exact-unprocessed": "registered",
                                  "past-unprocessed": "expired", "past-no-message": "expired"})
        answers = {task: answer for _, task, answer in checks}
        self.assertEqual(answers["exact"][0], ("symbol", "delete"))
        self.assertEqual(answers["past"], ("symbol", "expired"))
        self.assertEqual(answers["exact-unprocessed"], ("symbol", "wait"))
        self.assertEqual(len(outcome["submits"]), 2)

    def test_wait_without_a_message_id_and_when_not_processed(self):
        rows = [row("neither", created=NOW - 10), row("no-message", created=NOW - 10, processed=1),
                row("unprocessed", created=NOW - 10, message=5), row("ready", created=NOW - 10, message=6, processed=1),
                row("ready-submitted", created=NOW - 10, message=7, processed=1, state="submitted", checked=3.5)]
        outcome, checks, _ = self.same(rows)
        answers = {task: answer for _, task, answer in checks}
        self.assertEqual([answers[t] for t in ("neither", "no-message", "unprocessed")], [("symbol", "wait")] * 3)
        self.assertEqual(len(outcome["submits"]), 2)
        waiting = [record for record in outcome["rows"] if record[0] in ("neither", "no-message", "unprocessed")]
        self.assertTrue(all(record[7] is None and record[9] == NOW and record[8] == "registered" for record in waiting))

    def test_the_delete_path_with_receipts_served_before_and_after(self):
        lane = "42.0"
        send = lambda task: send_key_of("default", lane, task)
        rows = [row("r1", created=NOW - 50, processed=1),                      # message id arrives by receipt
                row("r2", created=NOW - 40),                                   # id arrives, still unprocessed
                row("r3", created=NOW - 30, message=33, processed=1),          # its delete receipt follows pump
                row("r4", created=NOW - 20),                                   # failed evidence
                row("r5", created=NOW - 10, message=55, processed=1),          # a send receipt with another id
                row("r6", created=NOW - 5, message=66, processed=1, state="submitted")]
        pending = [("c1", delivery(send("r1"), ["delivered", 77])), ("c2", delivery(send("r2"), ["delivered", 88])),
                   ("c3", delivery(delete_key_of(lane, send("r3")), ["delivered", 33])),
                   ("c4", delivery(send("r4"), ["failed", "x"])), ("c5", delivery(send("r5"), ["delivered", 56])),
                   ("c6", delivery("nobody", ["delivered", 1])),
                   ("c7", delivery(delete_key_of(lane, send("r6")), ["delivered", 0]))]
        outcome, checks, _ = self.same(rows, pending)
        states = {record[0]: (record[5], record[8]) for record in outcome["rows"]}
        self.assertEqual(states["r1"], (77, "registered"))
        self.assertEqual(states["r2"], (88, "registered"))
        self.assertEqual(states["r3"], (33, "deleted"))
        self.assertEqual(states["r6"], (66, "deleted"))
        self.assertEqual(sorted(outcome["acknowledged"]), ["c1", "c2", "c3", "c4", "c7"])
        self.assertEqual(len(outcome["submits"]), 4)

    def test_awkward_strings_and_long_lanes_in_the_delete_path(self):
        lanes = ['é日本"x', "l" * 128, "back\\slash", "42.0"]
        tasks = ['ünï "t" \\', "🙂" * 40, "t" * 128, "plain"]
        rows = [row(task, created=NOW - 10 - n, message=1000 + n, processed=1, lane=lane,
                    update="é\\" + str(n)) for n, (lane, task) in enumerate(zip(lanes, tasks))]
        outcome, checks, _ = self.same(rows)
        self.assertEqual(len(outcome["submits"]), 4)

    def test_rows_changed_between_the_read_and_the_check_are_mirrored(self):
        # The first delete's submission edits rows that due already returned: pump goes on from its
        # snapshot and its UPDATEs name only the task, so deleted/expired rows are checked again.
        rows = [row("c", created=100.0, message=3, processed=1),                          # aged, no submit
                row("a", created=NOW - 900, message=1, processed=1),                      # first submit
                row("b", created=NOW - 800, message=2, processed=1),                      # becomes deleted
                row("e", created=NOW - 700, message=5, processed=1, delete="old"),        # becomes expired
                row("d", created=100.5, processed=0),                                     # aged, becomes deleted
                row("f", created=NOW - 600, message=6, processed=1, delete="fresh")]      # delete key replaced
        edits = [("b", "state", "deleted"), ("e", "state", "expired"), ("d", "state", "deleted"),
                 ("f", "delete_key", "other")]
        outcome, checks, due = self.same(rows, edits=edits)
        states = {record[0]: (record[7] == delete_key_of("42.0", record[3]), record[8]) for record in outcome["rows"]}
        self.assertEqual(states["b"], (True, "deleted"))
        self.assertEqual(states["e"], (True, "expired"))
        self.assertEqual(states["d"], (False, "expired"))
        self.assertEqual(states["f"], (True, "registered"))
        self.assertEqual(len(outcome["submits"]), 4)

    def test_only_finished_notices_leave_nothing_due(self):
        outcome, checks, due = self.same([row("a", created=NOW - 1, message=1, processed=1, state="deleted"),
                                         row("b", created=NOW - 1, message=1, processed=1, state="expired")])
        self.assertEqual((checks, due, outcome["submits"]), ([], [], []))
        self.assertEqual(self.same([])[2], [])

    def test_a_task_without_a_notice_changes_nothing_and_waits(self):
        # pump never reaches this (its rows come from due); here a check names a task with no notice.
        out = run_cetta(self.directory, "missing", (
            "!(import! &self %s)\n%s"
            "!(let* (($db (db:open %s))\n        ($_ (input-ledger:ensure $db))\n"
            "        ($_ (input-ledger-test:seed $db %s))\n"
            '        ($_ (println! (answer (input-ledger:check $db "nobody" 5.0 172800))))\n'
            '        ($view (db:snapshot $db inputs))\n'
            '        ($_ (println! (all (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) $a))))))\n   done)\n')
            % (MODULE.with_suffix(""), PUMP_HELPERS, literal(str(Path(self.directory) / "missing-store")),
               notice_atom(row("x", created=1.0))))
        self.assertIn("(answer wait)", out)
        self.assertIn('(all ("x"))', out)
        self.assertNotIn("(Error", out)

    def test_a_delete_answer_leaves_the_checked_stamp_and_the_delete_key_in_the_notice(self):
        rows = [row("t", created=NOW - 5, message=9, processed=1)]
        outcome, _, due = self.same(rows)
        self.assertEqual(len(due), 1)
        final = outcome["rows"][0]
        self.assertEqual((final[7], final[9]), (delete_key_of("42.0", final[3]), NOW))


if __name__ == "__main__":
    unittest.main()
