"""The requests family in the native input ledger (METTACLAW_INPUT_LEDGER_SOCKET).

observed_strategy.Ledger's request methods (accept, processed, disposition, request_record, request_page,
protected, active, cancel, retire, finish, attest, finish_exact_results, projection, selectable ...) and the
turn checkpoint's request-status write are records of ONE native store: with the variable set the requests are
not rows of the SQLite tables any more. The oracle is the same sequence of calls through the SQLite path
(variable unset): return values, the refusal texts, every reader's answer after every step and the final records
are compared; the native ones are also read by an independent cetta client straight from the atoms. Every
METTACLAW_* variable a test relies on is set here (the rest is removed), child processes get an environment built
from scratch (no inherited variable, so no token), every wait has a deadline and every case starts its own
server on a fresh temporary store.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
import observed_strategy
from observed_strategy import IdentityConflict, Ledger, digest
from execution_gateway.cetta_worker import CettaValueWorker, E, S
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, RecordingProxy, SKIP
from test_input_ledger_remote import ENGINE, Oracle, WORKER, available, oracle_environment
from test_turn_checkpoint_native import AWKWARD, native_dump_program, parse_native_full, sha, working

ROUTER = dict(advertised=["tool"], executable=["tool"], permitted=["tool"])
BIG = "".join("line %05d \u00e9\u65e5\u672c\U0001d11e \"q\" \\ \n" % n for n in range(700))  # about 25000 characters
ASCII_CHECKER = "sha256:" + hashlib.sha256(b"x").hexdigest()


def sqlite_records(path):
    """The request rows of a SQLite ledger, oldest first, with their context."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return [dict(zip(("id", "content", "status", "checker", "kind", "processed", "note"), row))
                for row in db.execute("""SELECT r.id,r.content,r.status,r.checker,c.kind,c.processed,c.note
                    FROM requests r JOIN request_context c ON c.request=r.id ORDER BY r.rowid""")]


def sqlite_counts(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return (db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM request_context").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def seen(store, focus="input:1"):
    """Everything the readers say about the ledger, through the public methods."""
    page_all = store.request_page(0, 1000)
    # SQLite's substr() stops at an embedded NUL; the native summary keeps the characters (see
    # test_a_summary_with_a_nul_is_the_one_listed_divergence). Compared up to the NUL.
    for record in page_all["records"]:
        record["summary"] = record["summary"].split("\x00")[0]
    view = store.protected(focus)
    view.pop("owner")
    return {"page": page_all, "protected": view, "active": store.active(),
            "records": [store.request_record(row["id"]) for row in page_all["records"]],
            "projection": store.projection()["requests"]}


class Sides:
    """The same scenario (a function of a Ledger and a note function) on SQLite and on the native store."""
    def __init__(self, case):
        self.case = case

    def run(self, scenario):
        outcomes = {}
        for native in (False, True):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, self.case.server.socket) if native else Oracle.create(directory)
                store = Ledger(runner.path)
                steps = []
                scenario(store, lambda name, value: steps.append((name, value)))
                outcomes[native] = dict(steps=steps, final=seen(store), path=runner.path, records=sqlite_records(runner.path),
                                        tables=sqlite_counts(runner.path), raw_page=store.request_page(0, 1000))
        return outcomes[False], outcomes[True]

    def compare(self, scenario):
        oracle, native = self.run(scenario)
        self.case.assertEqual(len(native["steps"]), len(oracle["steps"]))
        for number, (want, got) in enumerate(zip(oracle["steps"], native["steps"])):
            self.case.assertEqual(got, want, (number, want[0]))
        for part in oracle["final"]:
            self.case.assertEqual(native["final"][part], oracle["final"][part], part)
        return oracle, native


def raises(function, *arguments, **options):
    try:
        return ("returned", function(*arguments, **options))
    except Exception as error:  # the exception type and text are part of what is compared
        return ("raised", type(error).__name__, str(error))


def lifecycle(store, note):
    """Every writer and every reader of the requests, with the refusals and the repeated calls."""
    note("accept", [store.accept("input:1", "first question", kind="message"),
                    store.accept("input:1", "first question", kind="message"),   # repeated: False
                    store.accept("input:2", "second", kind="message"),
                    store.accept("input:3", "third", kind="notification"),
                    store.accept("note:1", "a note", kind="notification"),
                    store.accept("task-1", "do the thing"),
                    store.accept("goal:o:1:0", "Goal one, cycle 0."),
                    store.accept("goal:o:1:1", "Goal one, cycle 1."),
                    store.accept("autonomous", "Ongoing work"),
                    store.accept("verified", "exact output", ASCII_CHECKER),
                    store.accept("t\u00e4sk \"q\"\n2", AWKWARD),
                    store.accept("input:t", "a task with an input: id", kind="task")])
    note("conflicts", [raises(store.accept, "input:1", "other content"),
                       raises(store.accept, "verified", "exact output", "operator"),
                       raises(store.accept, "x", "c", kind="nonsense")])
    note("bad-dispositions", [raises(store.disposition, "task-1", "achieved", "x"),
                              raises(store.disposition, "task-1", "keep", ""),
                              raises(store.disposition, "task-1", "keep", "x" * 801),
                              raises(store.disposition, "task-1", "keep", 5)])
    refusals = []
    for key, disposition in (("nope", "keep"), ("goal:o:1:0", "defer"), ("autonomous", "keep"),
                             ("verified", "addressed"), ("input:1", "addressed"), ("input:3", "addressed")):
        refusals.append((key, disposition, store.disposition(key, disposition, "why"), store.refusal))
    note("refusals", refusals)
    note("processed", [store.processed("input:1"), store.processed("input:1"), store.processed("task-1"),
                       store.processed("nope"), store.processed("goal:o:1:0"),
                       store.processed("input:t"), store.processed("input:t"), store.request_record("input:t")["status"],
                       store.request_record("input:t")["processed"]])
    note("after-processed", seen(store))
    note("addressed", [store.disposition("input:1", "addressed", "answered"), store.refusal,
                       store.request_record("input:1")["note"], store.request_record("input:1")["status"]])
    note("walk", [(d, store.disposition("task-1", d, "because " + d), store.request_record("task-1")["status"],
                   store.request_record("task-1")["kind"]) for d in ("waiting-user", "defer", "reopen", "keep", "defer")])
    note("keep-a-message", [store.disposition("input:2", "keep", "retain"), store.request_record("input:2")["kind"],
                            store.processed("input:2"), store.request_record("input:2")["status"]])
    note("checkpoint", [store.checkpoint(working(1.0), ["u1"], "input:3"), store.request_record("input:3")["status"],
                        store.checkpoint(working(2.0), ["u2"], "input:3"), store.checkpoint(working(3.0), ["u3"], "nope"),
                        store.checkpoint(working(4.0), ["u4"], "goal:o:1:0"), store.checkpoint(working(5.0), ["u5"], "note:1"),
                        store.request_record("note:1")["status"]])
    store.disposition("task-1", "keep", "active again")
    note("cancel", [store.cancel("task-1"), store.cancel("task-1", operator_authorized=True), store.cancel("task-1", True),
                    store.cancel("nope", True), store.disposition("task-1", "reopen", "x"), store.refusal])
    note("retire", [store.retire("goal:o:1:", "composted: " + "n" * 900), store.retire("goal:o:1:", "again"),
                    store.retire("nothing:", "x"), store.request_record("goal:o:1:1")["note"] == "composted: " + "n" * 788,
                    len(store.request_record("goal:o:1:1")["note"])])
    for number in range(1, 6):
        store.accept("grp:%d" % number, "group member %d" % number, kind="task")
    store.disposition("grp:2", "defer", "later")
    store.disposition("grp:3", "waiting-user", "asked")
    store.disposition("grp:4", "addressed", "done")
    note("retire-states", [store.retire("grp:", "closing"), [store.request_record("grp:%d" % n)["status"] for n in range(1, 6)],
                           [store.request_record("grp:%d" % n)["note"] for n in range(1, 6)]])
    note("end", seen(store))


@unittest.skipUnless(available(), SKIP)
class OracleTest(NativeCase):
    maxDiff = None

    def test_every_writer_and_reader_of_the_requests_answers_as_sqlite_does(self):
        oracle, native = Sides(self).compare(lifecycle)
        # Guards against a scenario that compares nothing:
        steps = dict(oracle["steps"])
        self.assertEqual(steps["accept"][:3], [True, False, True])
        # a message settles once; a task, an unknown id and a goal do not; a task with an input: id is settled
        # (its flag) but its status stays active
        self.assertEqual(steps["processed"], [True, False, False, False, False, True, False, "active", 1])
        self.assertEqual({row[1] for row in steps["refusals"]}, {"keep", "defer", "addressed"})
        self.assertIn("a message is addressed only after a turn has processed it", [row[3] for row in steps["refusals"]])
        self.assertEqual(steps["retire"][0], ["goal:o:1:0", "goal:o:1:1"])
        self.assertEqual(steps["cancel"][:4], [False, True, False, False])
        self.assertEqual([row["id"] for row in oracle["final"]["page"]["records"]][:3], ["grp:5", "grp:4", "grp:3"])

    def test_the_final_native_records_are_what_the_sqlite_rows_are(self):
        oracle, native = Sides(self).run(lifecycle)
        rows = oracle["records"]
        full = parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(len(full["requests"]), len(rows))
        # One record per request, numbered 1..n in acceptance order, a content atom and a link to the previous one
        # each, and the tail atom (their number and the newest).
        self.assertEqual(sorted(value[7] for value in full["requests"].values()), list(range(1, len(rows) + 1)))
        self.assertEqual(full["counter"], [len(rows)])
        self.assertEqual(full["tail"], [rows[-1]["id"]])
        self.assertEqual(full["chain"], {row["id"]: (rows[number - 1]["id"] if number else "") for number, row in enumerate(rows)})
        # and an arrival (its clock position, increasing in acceptance order) per request, and the meta keys written
        self.assertEqual(sorted(full["arrivals"], key=full["arrivals"].get), [row["id"] for row in rows])
        self.assertEqual(full["atoms"], 3 * len(rows) + 1 + len(full["processed"]) + len(full["notices"]) + len(full["working"])
                         + len(full["arrivals"]) + len(full["states"]))
        for number, row in enumerate(rows, 1):
            kind, checker, status, settled, note, summary, content_digest, seq = full["requests"][row["id"]]
            # (the dump is read as text with universal newlines: a CR LF prints as LF; the hashes below are exact)
            lf = lambda text: text.replace("\r\n", "\n").replace("\r", "\n")
            self.assertEqual((kind, lf(checker), status, lf(note), seq),
                             (row["kind"], lf(row["checker"]), row["status"], lf(row["note"]), number), row["id"])
            self.assertEqual(settled is not None, bool(row["processed"]), row["id"])
            self.assertEqual(summary, lf(row["content"][:180]))
            self.assertEqual(content_digest, digest(row["content"]))
            self.assertEqual(full["texts"][row["id"]], (sha(row["content"]), len(row["content"])), "the content, byte for byte")
        # What the Python readers give back is the same content, byte for byte.
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            store = Ledger(directory / "x.db")
            for row in rows:
                self.assertEqual(store.request_record(row["id"])["content"], row["content"][:4000])
            self.assertEqual([(r["id"], r["content"]) for r in store.active()],
                             [(r["id"], r["content"]) for r in rows if r["status"] == "active"])

    def test_native_requests_are_not_rows_of_the_sqlite_tables(self):
        oracle, native = Sides(self).run(lifecycle)
        self.assertEqual(native["records"], [])
        self.assertEqual(native["tables"], (0, 0, 0), "no request row, no context row and no event in SQLite")
        self.assertGreater(oracle["tables"][0], 5)

    def test_content_pages_summaries_and_the_listing_match_sqlite(self):
        def scenario(store, note):
            for number in range(25):
                store.accept("task:%02d" % number, ("%02d " % number) + BIG[:(number + 1) * 1100], kind="task")
            store.accept("input:1", "\u00e9" * 4000 + "x" * 5 + "\U0001d11e" * 4001, kind="message")  # a page boundary inside text
            for length in (3999, 4000, 4001, 8000, 8001):
                store.accept("exact:%d" % length, "y" * length, kind="task")
                for offset in (0, 4000, 8000):
                    note(("exact", length, offset), store.request_record("exact:%d" % length, offset))
                note(("exact-protected", length), (store.protected("exact:%d" % length)["request_truncated"],
                                                   len(store.protected("exact:%d" % length)["request"])))
            for key in ("task:00", "task:24", "input:1", "nope"):
                for offset in (0, 1, 3999, 4000, 4001, 8000, 8001, 12000, 10 ** 6):
                    note((key, offset), store.request_record(key, offset))
            note("negative", [store.request_record("input:1", -5)["offset"], store.request_page(-3, 2)["records"][0]["id"]])
            for offset, limit in ((0, 8), (8, 8), (16, 8), (24, 8), (26, 8), (27, 8), (100, 8), (0, 1), (5, 3), (0, 100), (3, 0)):
                note(("page", offset, limit), store.request_page(offset, limit))
            walk, offset = [], 0
            while offset is not None:
                page = store.request_page(offset)
                walk += [r["id"] for r in page["records"]]
                offset = page["next_offset"]
            note("walk", walk)
            view = store.protected("task:03")
            view.pop("owner")
            note("protected", view)
            note("protected-long-focus", store.protected("input:1")["request_truncated"])
            note("protected-none", store.protected(None)["unfinished"][0]["id"])
            note("protected-unknown", store.protected("nope")["request"])
        oracle, native = Sides(self).compare(scenario)
        steps = dict(oracle["steps"])
        self.assertEqual(len(steps["walk"]), 31)
        self.assertEqual([steps[("exact-protected", n)][0] for n in (3999, 4000, 4001)], [False, False, True])
        self.assertEqual([steps[("exact", n, 0)]["next_offset"] for n in (3999, 4000, 4001, 8000)], [None, None, 4000, 4000])
        self.assertEqual(steps[("exact", 8000, 4000)]["next_offset"], None)
        self.assertEqual(steps[("task:24", 0)]["next_offset"], 4000)
        self.assertEqual(steps[("input:1", 4000)]["content"][:6], "xxxxx\U0001d11e")
        self.assertEqual(steps["protected"]["omitted_active"], 23, "31 active requests, 8 listed")
        self.assertEqual(steps["protected"]["unfinished"][0]["id"], "task:03")
        self.assertTrue(steps["protected-long-focus"])

    def test_awkward_strings_survive_byte_for_byte_as_ids_contents_notes_and_checkers(self):
        awkward = [AWKWARD, "q\"uote", "back\\slash", "new\nline", "cr\r\nlf", "nul\x00byte", "\u2028\u2029", "%s %d {} $x !(y) ;; (z)",
                   "\U0001d11e\u00e9\u65e5\u672c", " leading and trailing ", "tab\t", "x" * 600]

        def scenario(store, note):
            for number, text in enumerate(awkward):
                note(("accept", number), store.accept("id:" + text, text + "|content", text + "|checker", kind="task"))
                note(("record", number), store.request_record("id:" + text))
                note(("disposition", number), (store.disposition("id:" + text, "defer", text[:800] or "x"), store.refusal))
            note("repeat", [store.accept("id:" + text, text + "|content", text + "|checker", kind="task") for text in awkward])
            note("conflict", [raises(store.accept, "id:" + text, "different", text + "|checker") for text in awkward[:3]])
        oracle, native = Sides(self).compare(scenario)
        self.assertEqual(oracle["final"]["page"]["total"], len(awkward))
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            store = Ledger(directory / "x.db")
            for text in awkward:
                record = store.request_record("id:" + text)
                self.assertEqual((record["id"], record["content"], record["checker"]),
                                 ("id:" + text, text + "|content", text + "|checker"))

    def test_a_summary_with_a_nul_is_the_one_listed_divergence(self):
        content = "nul\x00byte " + "y" * 300

        def scenario(store, note):
            store.accept("t", content, kind="task")
        oracle, native = Sides(self).run(scenario)
        self.assertEqual(oracle["raw_page"]["records"][0]["summary"], "nul", "SQLite substr() stops at the NUL")
        self.assertEqual(native["raw_page"]["records"][0]["summary"], content[:180], "the native summary is the first 180 characters")
        self.assertEqual(oracle["final"]["records"], native["final"]["records"], "the content itself is exact on both")

    def test_selectable_is_the_same_rule_on_both_paths(self):
        def selectable(store, key):
            if input_ack._native_socket():
                return Ledger.selectable(None, key)
            with store.transaction() as db:
                return Ledger.selectable(db, key)

        def scenario(store, note):
            store.accept("m", "m", kind="message")
            store.accept("n", "n", kind="notification")
            store.accept("t", "t", kind="task")
            store.accept("input:t", "x", kind="task")
            store.accept("g", "g", kind="goal")
            store.accept("d", "d", kind="task")
            store.accept("a", "a", kind="task")
            note("fresh", [selectable(store, k) for k in ("m", "n", "t", "input:t", "g", "nope")])
            store.checkpoint(working(1.0), ["u"], "m")
            store.processed("n")
            store.processed("input:t")
            store.disposition("d", "defer", "later")
            store.disposition("a", "addressed", "done")
            store.disposition("m", "addressed", "done")
            store.cancel("g", True)
            note("later", [selectable(store, k) for k in ("m", "n", "t", "input:t", "g", "d", "a")])
            store.accept("m2", "m", kind="message")
            store.processed("m2")
            note("processed-message", selectable(store, "m2"))
        oracle, native = Sides(self).compare(scenario)
        self.assertEqual(dict(oracle["steps"])["fresh"], [True, True, True, True, True, False])
        self.assertEqual(dict(oracle["steps"])["later"], [False, True, True, True, False, False, False])
        self.assertIs(dict(oracle["steps"])["processed-message"], True)

    def test_a_covered_request_keeps_the_rules_of_ledger_disposition(self):
        # Nothing makes a request covered natively (covering is the task-context family, see
        # test_covering_belongs_to_the_task_context_family...): a covered request can only be imported. The verbs
        # keep the rules of Ledger.disposition for it, except the one that needs input_supersessions (SQLite).
        directory = self.session()
        atoms = (E("request", "c1", 1, "message", "operator", "covered", 0.0, "", "s", "d"), E("request-text", "c1", "text"),
                 E("request-prev", "c1", ""), E("request-tail", "tail", 1, "c1"))
        self.assertIs(input_ack._ledger_call("import", (S("empty"), atoms)), True)
        store = Ledger(directory / "x.db")
        self.assertEqual([store.disposition("c1", "defer", "x"), store.refusal],
                         [False, "input batch is covered; reopen it to create independent work"])
        self.assertEqual([store.disposition("c1", "addressed", "x"), store.refusal],
                         [False, "input batch is covered; reopen it to create independent work"])
        self.assertTrue(store.disposition("c1", "reopen", "again"))
        record = store.request_record("c1")
        self.assertEqual((record["status"], record["kind"], record["note"]), ("active", "task", "again"))

    def test_what_a_checkpoint_settles_is_seen_by_every_reader_of_the_same_store(self):
        def scenario(store, note):
            store.accept("input:1", "m", kind="message")
            store.accept("input:2", "n", kind="message")
            note("before", [store.disposition("input:1", "addressed", "d"), store.refusal, [r["id"] for r in store.active()],
                            store.protected("input:1")["counts"], store.request_record("input:1")["processed"]])
            store.checkpoint(working(1.0), ["a"], "input:1")
            note("after", [store.disposition("input:1", "addressed", "d"), store.refusal, [r["id"] for r in store.active()],
                           store.protected("input:1")["counts"], store.request_record("input:1")["processed"],
                           store.request_record("input:1")["status"], store.processed("input:1"),
                           [r["status"] for r in store.projection()["requests"]]])
            note("status", [store.status_of(k) for k in ("input:1", "input:2", "nope")])
        oracle, native = Sides(self).compare(scenario)
        self.assertEqual(dict(oracle["steps"])["after"][:3], [True, None, ["input:2"]])


def observed_receipt(store, key, payload, domain="success"):
    """An action of the request with a returned, observed receipt (the actions stay in SQLite on both paths)."""
    sample = store.capture(key, "context", ROUTER, 0)
    ticket = store.decide(sample["serial"], dict(kind="command", owner=sample["owner"], request=key, tool="tool",
                                                 command=["tool"]), 0)
    store.report(ticket, payload, "returned", domain)
    store.observe([ticket], {ticket: [store.protected(key)["results"][0]]})
    return ticket


@unittest.skipUnless(available(), SKIP)
class CheckerTest(NativeCase):
    """finish, attest and finish_exact_results read and move a request (native) next to its actions (SQLite)."""
    maxDiff = None

    def test_attest_finish_and_the_exact_output_checkers_match_sqlite(self):
        always = lambda work, receipt: True
        seen_work = []

        def spy(work, receipt):
            seen_work.append(work)
            return True

        def scenario(store, note):
            store.accept("w1", "work one", kind="task")
            store.accept("w2", "work two", ASCII_CHECKER.replace(hashlib.sha256(b"x").hexdigest(), digest("payload2")), kind="task")
            store.accept("w3", "work three", "sha256:" + digest("expected"), kind="task")
            store.accept("w4", "work four", "SHA256:" + digest("payload4"), kind="task")   # LIKE is ASCII case-insensitive
            store.accept("w5", "work five \u00e9\u65e5 \"q\"", kind="task")
            t1 = observed_receipt(store, "w1", "payload1", "unknown")
            t2 = observed_receipt(store, "w2", "payload2", "unknown")
            t3 = observed_receipt(store, "w3", "payload3", "unknown")
            t4 = observed_receipt(store, "w4", "payload4", "unknown")
            t5 = observed_receipt(store, "w5", "payload5", "unknown")
            note("early-finish", [store.finish("w1", t1, {"operator": always}), store.finish("nope", t1, {"operator": always})])
            note("attest", [store.attest("w1", t1, always), store.attest("w1", t1, lambda w, r: False),
                            store.attest("nope", t1, always), store.attest("w1", "no-ticket", always)])
            note("finish", [store.finish("w1", t1, {"other": always}), store.finish("w1", t1, {"operator": lambda w, r: False}),
                            store.finish("w1", t1, {"operator": always}), store.finish("w1", t1, {"operator": always}),
                            store.request_record("w1")["status"], [r["id"] for r in store.active()]])
            note("exact", [store.finish_exact_results(), store.finish_exact_results(),
                           [(k, store.request_record(k)["status"]) for k in ("w2", "w3", "w4")]])
            note("work-dict", [store.attest("w5", t5, spy), store.finish("w5", t5, {"operator": spy})])
            note("done", [store.request_record("w5")["status"], store.disposition("w5", "reopen", "x"), store.refusal,
                          store.cancel("w5", True)])
        oracle, native = Sides(self).compare(scenario)
        steps = dict(oracle["steps"])
        self.assertEqual(steps["early-finish"], [False, False])
        self.assertEqual(steps["attest"], [True, False, False, False])
        self.assertEqual(steps["finish"][2:5], [True, False, "achieved"])
        # w2 matches its digest; w3 does not; w4's checker is selected by LIKE (ASCII case-insensitive) on both paths
        # but removeprefix("sha256:") leaves its upper-case prefix in the digest, so it does not match either
        self.assertEqual(steps["exact"][0], 1)
        self.assertEqual(steps["exact"][1], 0)

    def test_the_checker_is_given_the_request_row_as_sqlite_gives_it(self):
        got = {}
        for native in (False, True):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, self.server.socket) if native else Oracle.create(directory)
                store = Ledger(runner.path)
                store.accept("w", "content \u00e9 \n x", kind="task")
                ticket = observed_receipt(store, "w", "p", "unknown")
                seen_work = []
                store.attest("w", ticket, lambda work, receipt: seen_work.append((dict(work), dict(receipt, ticket="t"))) or True)
                got[native] = seen_work
        self.assertEqual(got[True], got[False])
        self.assertEqual(got[False][0][0], {"id": "w", "content": "content \u00e9 \n x", "status": "active", "checker": "operator"})


@unittest.skipUnless(available(), SKIP)
class TaskContextTest(NativeCase):
    """The Python readers of the task-context family that ask for requests (the rest of that family is SQLite)."""
    maxDiff = None

    def test_begin_resume_read_note_and_the_projections_match_sqlite(self):
        import task_context

        def scenario(store, note):
            store.accept("input:a", "first question \u00e9\u65e5", kind="message")
            store.link_inputs("input:a", ["1"])
            store.accept("input:b", "second \"q\"\n", kind="message")
            store.link_inputs("input:b", ["2"])
            store.accept("task-x", "a task", kind="task")
            note("begin", [task_context.begin(store, "input:a", "operator-event", None, "chat1"), task_context.current(store)])
            note("resume", [task_context.resume(store, "input:a"), task_context.resume(store, "nope"),
                            task_context.resume(store, "task-x")])
            store.processed("input:a")
            store.disposition("task-x", "waiting-user", "waiting")
            note("begin-child", [task_context.begin(store, "input:b", "operator-event", "task-x", "chat1"),
                                 task_context.current(store), task_context.resume(store, "input:b")])
            note("unknown-parent", raises(task_context.begin, store, "input:c", "operator-event", None, "c", "nope"))
            note("read", [raises(task_context.read, store, "task-input:input:a"),
                          raises(task_context.read, store, "task-input:nope"),
                          raises(task_context.read, store, "task-input:input:b", 3, 5)])
            note("note", [raises(task_context.note, store, "input:b", "an interpretation"),
                          raises(task_context.note, store, "nope", "an interpretation"),
                          raises(task_context.note, store, "input:b", "  ")])
            note("projection", [task_context.projection(store, "input:b"), task_context.projection(store, "task-x")])
            conversation = task_context.conversation_projection(store, "input:b")
            conversation["root"] = "conversation:<owner>"  # the owner is random per ledger
            note("conversation", conversation)
        oracle, native = Sides(self).compare(scenario)
        steps = dict(oracle["steps"])
        self.assertEqual(steps["begin-child"][2], "input:b")
        self.assertEqual([item["id"] for item in steps["projection"][0]["requests"]], ["input:b", "task-x"])
        self.assertEqual(steps["unknown-parent"][:2], ("raised", "ValueError"))
        self.assertEqual(len(steps["conversation"]["requests"]), 3)

    def test_covering_supersedes_the_same_batches_on_the_native_store(self):
        import task_context

        def scenario(store, note):
            for key, ids in (("input:a", ["1"]), ("input:b", ["1", "2"])):
                store.accept(key, "text " + key, kind="message")
                store.link_inputs(key, ids)
                task_context.begin(store, key, "operator-event", None, "chat")
            note("statuses", [store.status_of("input:a"), store.status_of("input:b"), [r["id"] for r in store.active()]])
        oracle, native = Sides(self).run(scenario)
        self.assertEqual(dict(oracle["steps"])["statuses"], ["covered", "active", ["input:b"]], "SQLite covers input:a")
        # The frames, the inputs and the coverage are native records too: the same batch is covered.
        self.assertEqual(dict(native["steps"])["statuses"], dict(oracle["steps"])["statuses"])

    def test_the_strategy_host_closes_an_answered_message_and_disposes_through_the_same_store(self):
        import strategy_host as host
        import task_context
        import telegram

        def scenario(store, note):
            for key, ids in (("input:a", ["1"]), ("input:two", ["2", "3"]), ("input:c", ["4"]), ("input:d", ["5"])):
                store.accept(key, "q " + key, kind="message")
                store.link_inputs(key, ids)
                store.processed(key)
            for key, chat in (("input:a", "42"), ("input:two", "42"), ("input:c", "7"), ("input:d", "42")):
                task_context.begin(store, key, "operator-event", None, chat)
            statuses = []
            with mock.patch.object(host, "ledger", return_value=store), mock.patch.object(host, "_sample", None), \
                    mock.patch.object(telegram, "_reply_target", return_value="42"):
                for key, command, result in (("input:a", ["send", "hi"], "sent message 7"),
                                             ("input:two", ["send", "hi"], "sent message 8"),   # two inputs: not closed
                                             ("input:c", ["send", "hi"], "sent message 9"),     # another chat
                                             ("input:d", ["send", "hi"], "not sent"),
                                             ("input:d", ["read", "x"], "sent message 1"),
                                             ("input:d", ["send-telegram-chat", "42", "hi"], "sent message 1")):
                    with mock.patch.object(host, "_focus", key):
                        host._close_answered_message(command, result)
                    statuses.append((key, store.status_of(key)))
            note("closed", statuses)
            with mock.patch.object(host, "ledger", return_value=store):
                note("disposition", [host.request_disposition("input:c", "addressed", "done"),
                                     host.request_disposition("nope", "keep", "x"),
                                     host.request_disposition("input:c", "bogus", "x"),
                                     host.request_disposition("input:c", "defer", ""),
                                     store.request_record("input:c")["status"]])
                with mock.patch.object(host, "_message_focus", "input:two"):
                    note("input-processed", [host.input_processed(), store.request_record("input:two")["processed"]])
                note("focus", [host.focus_request("input:c"), host.focus_request("nope"), host.focus_request("input:a")])
        oracle, native = Sides(self).compare(scenario)
        steps = dict(oracle["steps"])
        self.assertEqual(steps["closed"][0], ("input:a", "addressed"))
        self.assertEqual(steps["closed"][1], ("input:two", "processed"))
        self.assertEqual(steps["closed"][2], ("input:c", "processed"))
        self.assertEqual(steps["closed"][3], ("input:d", "processed"))
        self.assertEqual(steps["closed"][5], ("input:d", "addressed"))


class ProxyRequests(RecordingProxy):
    """RecordingProxy that also keeps the request ids (rq1.<32 hex>) of the request verbs it sees."""
    def frame(self, connection):
        data = RecordingProxy.frame(self, connection)
        if data:
            self.rids = getattr(self, "rids", [])
            self.rids += re.findall(rb"rq1\.[0-9a-f]{32}", data)
        return data


REQUEST_VERBS = ("request-accept", "request-settle", "request-disposition", "request-cancel", "request-finish",
                 "request-answered", "request-retire")


@unittest.skipUnless(available(), SKIP)
class ReplayTest(NativeCase):
    """Request ids: a retry of one call replays it, a new call is a new transition; lost replies, restarts."""
    maxDiff = None

    def store(self):
        directory = self.session()
        return Ledger(directory / "x.db")

    def test_the_same_request_id_replays_the_first_answer_and_another_id_decides_again(self):
        self.session()
        accept = lambda rid, content="c": input_ack._ledger_call(
            "request-accept", ("r", content, "operator", "task", content[:180], digest(content), rid))
        self.assertEqual(accept("rq1.one"), S("created"))
        self.assertEqual(accept("rq1.one"), S("created"), "the same call again is a replay, not 'exists'")
        self.assertEqual(accept("rq1.two"), S("exists"), "a new call decides on the state it finds")
        with self.assertRaises(input_ack.LedgerUnavailable) as raised:
            accept("rq1.one", "other content")  # the same id with other arguments
        self.assertIn("receipt-collision", str(raised.exception))
        self.assertEqual(Ledger.native_request("r")["content"], "c")
        settle = lambda rid, now=1.0: input_ack._ledger_call("request-settle", ("r", now, rid))
        self.assertEqual(Ledger.native_request("r")["processed"], 0)
        self.assertIs(settle("rq1.s1"), False)  # a task is not settled by a checkpoint: nothing changes
        store = self.store()
        store.accept("input:m", "m", kind="message")
        self.assertIs(input_ack._ledger_call("request-settle", ("input:m", 5.0, "rq1.s2")), True)
        self.assertIs(input_ack._ledger_call("request-settle", ("input:m", 5.0, "rq1.s2")), True, "replayed")
        self.assertIs(input_ack._ledger_call("request-settle", ("input:m", 5.0, "rq1.s3")), False, "already settled")
        with self.assertRaises(input_ack.LedgerUnavailable):
            input_ack._ledger_call("request-settle", ("input:m", 6.0, "rq1.s2"))  # same id, another time

    def test_each_call_makes_its_own_id_and_every_repeat_of_one_call_carries_it(self):
        store = self.store()
        seen_requests = []
        original = CettaValueWorker.call
        lost = [True]

        def spy(worker, request):
            if request[2] in REQUEST_VERBS:
                seen_requests.append(request)
            reply = original(worker, request)
            if request[2] == "request-settle" and lost[0]:
                lost[0] = False
                from execution_gateway.cetta_worker import WorkerReplyUncertain
                raise WorkerReplyUncertain("simulated: the reply was lost", request_id="x", payload=request)
            return reply

        with mock.patch.object(CettaValueWorker, "call", spy):
            store.accept("input:1", "m", kind="message")
            store.accept("input:1", "m", kind="message")   # identical arguments: still a second call
            self.assertIs(store.processed("input:1"), True, "the lost reply was repeated, and its first answer replayed")
        ids = [request[3][-1] for request in seen_requests]
        self.assertTrue(all(re.fullmatch(r"rq1\.[0-9a-f]{32}", item) for item in ids), ids)
        self.assertEqual(len(ids), 4)
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(ids[2], ids[3], "the repeat carries the very same request")
        self.assertEqual(seen_requests[2], seen_requests[3])
        self.assertEqual(store.request_record("input:1")["status"], "processed")

    def test_a_reply_lost_after_the_commit_is_replayed_once_for_every_writer(self):
        proxy = ProxyRequests(self.directory / "proxy.sock", self.server.socket)
        self.addCleanup(proxy.close)
        directory = self.session(proxy.path)
        store = Ledger(directory / "x.db")
        store.accept("input:1", "first", kind="message")
        store.accept("goal:o:1:0", "goal", kind="goal")
        store.accept("task-1", "task", kind="task")
        answers = {}
        for name, call in (("accept", lambda: store.accept("input:2", "second", kind="message")),
                           ("processed", lambda: store.processed("input:1")),
                           ("disposition", lambda: store.disposition("input:1", "addressed", "done")),
                           ("cancel", lambda: store.cancel("task-1", True)),
                           ("retire", lambda: store.retire("goal:o:1:", "composted"))):
            proxy.dropped.clear()
            proxy.armed.set()
            answers[name] = call()
            self.assertTrue(proxy.dropped.is_set(), "the proxy never lost the reply of " + name)
        # What the single call answers, not what a second application would have answered:
        self.assertEqual(answers, {"accept": True, "processed": True, "disposition": True, "cancel": True,
                                   "retire": ["goal:o:1:0"]})
        self.assertEqual(store.request_page(0, 10)["total"], 4, "no request was accepted twice")
        self.assertEqual([r["status"] for r in reversed(store.request_page(0, 10)["records"])],
                         ["addressed", "cancelled", "cancelled", "active"])
        self.assertEqual(store.request_record("input:1")["note"], "done")
        full = parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(sorted(value[7] for value in full["requests"].values()), [1, 2, 3, 4])
        self.assertEqual(full["counter"], [4])

    def test_the_next_call_after_a_server_restart_reconnects_and_the_records_are_still_there(self):
        for crash in (False, True):
            with self.subTest(crash=crash):
                store = self.store()
                key = "input:%s" % crash
                store.accept(key, "before \u00e9", kind="message")
                self.assertEqual(self.server.stop(crash=crash), -9 if crash else 0)
                with self.assertRaises(input_ack.LedgerUnavailable):
                    store.request_record(key)
                self.server.start()
                self.assertEqual(store.request_record(key)["content"], "before \u00e9")
                self.assertTrue(store.processed(key))
                self.assertTrue(store.accept(key + "-after", "after", kind="message"))
                self.assertTrue(store.disposition(key, "addressed", "ok"))
        self.assertEqual(Ledger(self.directory / "x.db").request_page(0, 10)["total"], 4)

    def test_an_unreachable_server_fails_every_reader_and_writer_and_never_falls_back_to_sqlite(self):
        path = self.directory / "ledger.db"
        with oracle_environment():
            os.environ["METTACLAW_STRATEGY_PATH"] = str(path)
            sqlite_store = Ledger(path)
            sqlite_store.accept("input:1", "in sqlite", kind="message")
            sqlite_store.accept("w", "work", kind="task")
            ticket = observed_receipt(sqlite_store, "w", "p", "success")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.server.stop()
        failures = {}
        with oracle_environment():
            os.environ["METTACLAW_STRATEGY_PATH"] = str(path)
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            store = Ledger(path)
            calls = {
                "accept": lambda: store.accept("x", "c"),
                "processed": lambda: store.processed("input:1"),
                "disposition": lambda: store.disposition("input:1", "keep", "n"),
                "request_record": lambda: store.request_record("input:1"),
                "request_page": lambda: store.request_page(),
                "protected": lambda: store.protected("input:1"),
                "active": lambda: store.active(),
                "projection": lambda: store.projection(),
                "cancel": lambda: store.cancel("w", True),
                "retire": lambda: store.retire("w", "n"),
                "finish": lambda: store.finish("w", ticket, {"operator": lambda w, r: True}),
                "attest": lambda: store.attest("w", ticket, lambda w, r: True),
                "finish_exact_results": lambda: store.finish_exact_results(),
                "selectable": lambda: Ledger.selectable(None, "input:1"),
                "status_of": lambda: store.status_of("input:1"),
                "checkpoint": lambda: store.checkpoint(working(1.0), ["a"], "input:1"),
                "working_checkpoint": lambda: store.working_checkpoint(),
            }
            for name, call in calls.items():
                try:
                    call()
                except sqlite3.OperationalError as error:
                    failures[name] = error
            self.assertEqual(sorted(failures), sorted(calls), "every one raises; none answers from the SQLite file")
            self.assertTrue(all(isinstance(error, input_ack.LedgerUnavailable) for error in failures.values()))
            self.assertTrue(all("db:connect" in str(error) for error in failures.values()), {k: str(v) for k, v in failures.items()})
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before, "the SQLite ledger was written")
        self.server.start()
        with oracle_environment():
            os.environ["METTACLAW_STRATEGY_PATH"] = str(path)
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            store = Ledger(path)
            # The native store is another, empty ledger: the SQLite record is not in it.
            self.assertEqual(store.request_record("input:1"), {"id": "input:1", "status": "unknown"})
            self.assertEqual(store.request_page()["total"], 0)

    def test_with_the_variable_unset_or_empty_no_request_method_touches_the_native_path(self):
        for value in (None, ""):
            with oracle_environment() as directory:
                if value is not None:
                    os.environ[input_ack.LEDGER_SOCKET] = value
                store = Ledger(directory / "l.db")
                os.environ["METTACLAW_STRATEGY_PATH"] = str(directory / "l.db")
                with mock.patch.object(input_ack, "_ledger_call", side_effect=AssertionError("native path used")), \
                        mock.patch.object(input_ack, "_ledger_worker", side_effect=AssertionError("native worker used")):
                    lifecycle(store, lambda name, value: None)
                    store.accept("w", "work", kind="task")
                    ticket = observed_receipt(store, "w", "p", "unknown")
                    store.attest("w", ticket, lambda w, r: True)
                    store.finish("w", ticket, {"operator": lambda w, r: True})
                    store.finish_exact_results()
                    store.status_of("w")
                    self.assertEqual(sqlite_counts(directory / "l.db")[0], 17)
        self.assertIsNone(input_ack._ledger, "a worker was started with the variable unset")

    def test_a_stale_request_processed_atom_of_the_task_5_layout_cannot_make_a_new_request_born_settled(self):
        store = self.store()
        legacy = lambda: parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))["legacy"]
        self.assertIs(input_ack._ledger_call("import", (S("any"), (E("request-processed", "input:1", 5.0),
                                                                   E("request-processed", "input:other", 6.0)))), True)
        self.assertEqual(len(legacy()), 2)
        self.assertEqual(input_ack._ledger_call("census", ()), (S("census"), 0, 0, 0, 2), "counted as other atoms")
        self.assertTrue(store.accept("input:1", "m", kind="message"))
        self.assertEqual(len(legacy()), 1, "the atom of this id was removed with the acceptance; the other one is untouched")
        record = store.request_record("input:1")
        self.assertEqual((record["processed"], record["status"]), (0, "active"))
        store.checkpoint(working(9.0), ["a"], "input:1")
        self.assertEqual(store.request_record("input:1")["processed"], 1)
        self.assertEqual(store.request_record("input:other"), {"id": "input:other", "status": "unknown"})

    def test_a_store_made_with_the_earlier_index_layout_is_refused_loudly(self):
        # The layout of a collection is fixed when it is first ensured; a store of the task-4/5 layout (no request
        # atoms, no request indexes) cannot be used by this module: it must be made again from SQLite.
        out = self.run_client("old", "!(import! &self (library db))\n"
                                     "!(let* (($db (db:connect %s))\n"
                                     "        ($r (db:ensure $db inputs ((SpaceIndexV1 3 1) (SpaceIndexV1 11 1) (SpaceIndexV1 11 2)"
                                     " (SpaceIndexV1 11 4) (SpaceIndexV1 11 8)))))\n"
                                     "   (println! (old $r)))\n" % json.dumps(str(self.server.socket)))
        self.assertIn("(old true)", out)
        self.session()
        with self.assertRaises(input_ack.LedgerUnavailable) as raised:
            input_ack.committed(["a"])
        self.assertIn("index-layout-mismatch", str(raised.exception))


@unittest.skipUnless(available(), SKIP)
class TwoProcessTest(NativeCase):
    """Two processes (and two workers) on one store: no request is accepted twice, none is lost, seq stays dense."""

    def client(self, name, own, shared, go):
        spec = self.directory / (name + ".spec")
        spec.write_text(repr({"socket": str(self.server.socket), "go": str(go), "name": name, "own": own, "shared": shared}))
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.directory), "LANG": "C.UTF-8",
                       "CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER)}
        log = open(self.directory / (name + ".out"), "wb")
        with log:
            process = subprocess.Popen([sys.executable, str(HERE / "native_request_client.py"), str(spec)],
                                       env=environment, cwd=self.directory, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
        process.output = self.directory / (name + ".out")
        self.children.append(process)
        return process

    def test_two_processes_accepting_settling_and_disposing_share_one_consistent_store(self):
        go = self.directory / "go"
        processes = [self.client("a", 14, 8, go), self.client("b", 14, 8, go)]
        time.sleep(1.5)  # both are waiting for `go`
        go.write_text("go")
        outputs = [self.finish(process, 150) for process in processes]
        answers = []
        for output in outputs:
            lines = [json.loads(line) for line in output.splitlines() if line.startswith("[")]
            self.assertTrue(lines and lines[-1][0] == "done", output[-3000:])
            answers.append(lines)
        accepts = [(who, line[1], line[2]) for who, lines in zip("ab", answers) for line in lines if line[0] == "accept"]
        for number in range(8):
            created = [who for who, key, answer in accepts if key == "shared-%d" % number and answer is True]
            self.assertEqual(len(created), 1, "shared-%d was created %s" % (number, created))
        self.assertTrue(all(answer is True for who, key, answer in accepts if key.startswith(who + "-")))
        with oracle_environment() as directory:
            NativeRunner.create(directory, self.server.socket)
            store = Ledger(directory / "x.db")
            page = store.request_page(0, 1000)
            self.assertEqual(page["total"], 14 + 14 + 8)
            rows = {r["id"]: r for r in page["records"]}
            self.assertEqual(len(rows), 36)
            for who in "ab":
                for number in range(14):
                    row = rows["%s-%d" % (who, number)]
                    self.assertEqual(row["processed"], 1 if number % 3 == 0 else 0)
                    self.assertEqual(row["status"], "deferred" if number % 4 == 0 else "processed" if number % 3 == 0 else "active")
                    self.assertEqual(row["note"], "by " + who if number % 4 == 0 else "")
            for number in range(8):  # both address it; the note is whoever came last
                row = rows["shared-%d" % number]
                self.assertEqual((row["status"], row["processed"]), ("addressed", 1))
                self.assertIn(row["note"], ("by a", "by b"))
        full = parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(sorted(value[7] for value in full["requests"].values()), list(range(1, 37)), "seq is dense and unique")
        self.assertEqual(full["counter"], [36])
        settled = [line[2] for lines in answers for line in lines if line[0] == "processed" and line[1].startswith("shared")]
        self.assertEqual(sum(settled), 8, "each shared request was settled exactly once, by one of the processes")
        self.assertEqual(len(settled), 16)


if __name__ == "__main__":
    unittest.main()
