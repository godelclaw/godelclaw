"""scripts/migrate_input_ledger.py: a SQLite input ledger copied into an empty native store.

The synthetic source ledgers are made by input_ack.py itself (through the
observed_strategy.Ledger + METTACLAW_STRATEGY_PATH route of test_input_ack.py,
with the SQLite path), so the schema is the real one and every state is
reached by the real operations. The target is a native db server on a fresh
temporary store. The script is run as a process, as an operator would, with an
environment built from scratch; the engine seams (after_copy) are used in-process.
No test names a live ledger: every path is in a temporary directory.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "scripts")]
import input_ack
import migrate_input_ledger as migration
from execution_gateway.cetta_worker import E, S
import test_input_ledger as base
from test_input_ack_native import EVERY_STATE, NativeCase, SKIP
from native_ledger_client import NativeRunner
from test_input_ledger_remote import DbServer, ENGINE, LANE, Oracle, WORKER, available, oracle_environment, run_oracle
from test_turn_checkpoint_native import (AWKWARD, REQUESTS, encoded, native_dump_program, parse_native, parse_native_full, sha,
                                         working)

SCRIPT = ROOT / "scripts/migrate_input_ledger.py"
EXTRA_IDS = ("mark", ["tab\there", "y" * 300, "e\u0301", "\u2603 snow", "back\\slash\\"], 430.0)
CONTINUATION = [("register", "t9", "u9", LANE, 500000.0), ("mark", ["u9", "u4"], 500001.0),
                ("committed", ["u9", "u1", "u4"]), ("pump", 500002.0), ("receipt", ("send", "t9", LANE), ["delivered", 9])]


def atom_text(value):
    """A record as MeTTa source, strings as literals (input_ack._show prints Python reprs)."""
    from execution_gateway.cetta_worker import FloatBits, Symbol, scalar_float
    from test_input_ledger_remote import literal
    if isinstance(value, Symbol):
        return value.name
    if isinstance(value, FloatBits):
        return repr(scalar_float(value))
    if isinstance(value, tuple):
        return "(" + " ".join(atom_text(item) for item in value) + ")"
    return literal(value) if isinstance(value, str) else repr(value)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tables(path):
    """The two tables of a SQLite ledger file as sorted rows (read-only)."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        notices = sorted(db.execute("SELECT %s FROM input_notices" % ", ".join(base.COLUMNS)))
        processed = dict(db.execute("SELECT id, processed FROM processed_inputs"))
    return [tuple(row) for row in notices], processed


@unittest.skipUnless(available(), SKIP)
class MigrationTest(NativeCase):
    def build(self, operations, name="source.db"):
        """A ledger file made by input_ack.py (the SQLite path) running `operations`."""
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            for operation in operations:
                oracle.apply(operation)
            target = self.directory / name
            shutil.copyfile(oracle.path, target)
        return target

    def environment(self, **extra):
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.directory), "LANG": "C.UTF-8",
                       "CETTA_BIN": str(ENGINE), "CETTA_VALUE_WORKER_BIN": str(WORKER)}
        environment.update(extra)
        return environment

    def run_script(self, *arguments, **extra):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], env=self.environment(**extra),
                              cwd=self.directory, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=140)

    def migrate_cli(self, source, **extra):
        return self.run_script("--source", str(source), "--socket", str(self.server.socket), **extra)

    def fresh_target(self):
        self.server.stop()
        shutil.rmtree(self.server.store)
        self.server.start()

    def migrate_here(self, source, **options):
        try:
            return migration.migrate(source, str(self.server.socket), **options)
        finally:
            input_ack.close_ledger()

    def census(self):
        rows, ids = self.dump()
        return len(rows), len(ids)

    # ---- success ----

    def test_an_empty_ledger_migrates_to_an_empty_store(self):
        source = self.build([])
        before = digest(source)
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        # No input rows; the ledger's meta keys (its owner, revision and the like) are the one batch of the turn core.
        self.assertEqual((report["processed_inputs"], report["input_notices"], report["batches"], report["verified"]),
                         (0, 0, 1, True))
        self.assertGreater(report["turn_core_records"], 0)
        self.assertEqual(self.dump(), ([], {}))
        self.assertEqual(digest(source), before)
        # The target now holds those keys: a second run refuses it rather than copying twice.
        self.assertEqual(self.migrate_cli(source).returncode, 1)

    def test_a_populated_ledger_with_every_state_is_copied_row_for_row_and_behaves_alike_afterwards(self):
        operations = EVERY_STATE + [EXTRA_IDS]
        source = self.build(operations)
        notices, processed = tables(source)
        self.assertEqual({row[8] for row in notices}, {"registered", "submitted", "deleted", "expired"})
        self.assertTrue(any(row[5] is None for row in notices) and any(row[7] is None for row in notices))
        self.assertTrue(any(row[5] is not None for row in notices) and any(row[7] is not None for row in notices))
        self.assertTrue(any(row[6] == 1 for row in notices) and any(row[6] == 0 for row in notices))
        self.assertIn("y" * 300, processed)
        before = digest(source)
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual((report["processed_inputs"], report["input_notices"]), (len(processed), len(notices)))
        self.assertTrue(report["verified"])
        rows, ids = self.dump()
        self.assertEqual(rows, notices)
        self.assertEqual(ids, processed)
        self.assertEqual(digest(source), before, "the source ledger changed")
        self.assertEqual([entry.name for entry in self.directory.iterdir() if entry.name.startswith("source.db")],
                         ["source.db"], "the source left a journal behind")
        # The migrated store goes on as the SQLite ledger would have.
        expected, final_notices, final_processed = run_oracle(operations + CONTINUATION)
        answers = self.run_native(CONTINUATION)
        for number, operation in enumerate(CONTINUATION):
            if operation[0] != "pump":
                self.assertEqual(answers[number], expected[len(operations) + number], operation)
        rows, ids = self.dump()
        self.assertEqual(rows, final_notices)
        self.assertEqual(ids, final_processed)

    def test_the_turn_core_is_copied_and_every_reader_reads_as_on_the_source(self):
        # Frames, coverage, turns, actions with receipts, attempts, proposals and notes, written on SQLite.
        from observed_strategy import Ledger
        from test_task_context_native import contexts
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            contexts(Ledger(oracle.path), False, lambda name, value: None)
            source = self.directory / "core.db"
            shutil.copyfile(oracle.path, source)
        report = self.migrate_here(source)
        self.assertGreater(report["turn_core_records"], 30)
        self.assertEqual(migration.verify_readers(source, str(self.server.socket)), [])

    def families_source(self):
        """A SQLite ledger with two topics (one active, with notes), a historical model job and two provider notices."""
        from observed_strategy import Ledger
        import topic_jobs
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            Ledger(oracle.path)
            topic_jobs.select("research"); topic_jobs.note("notes é \"q\"")
            topic_jobs.select("design"); topic_jobs.select("research")
            with closing(sqlite3.connect(oracle.path)) as db:
                db.execute("INSERT INTO model_jobs(id, topic, revision, snapshot, question, state, result, boot) "
                           "VALUES ('job-1', 'research', 1, '{}', 'q', 'completed', 'the answer', 'boot-1')")
                db.execute("CREATE TABLE IF NOT EXISTS provider_notices (episode TEXT PRIMARY KEY, intent TEXT NOT NULL, "
                           "queued INTEGER NOT NULL DEFAULT 0)")
                db.executemany("INSERT INTO provider_notices VALUES (?,?,?)",
                               [("episode-1", '{"key":"42.0.1","commands":[["send","x","plain"]]}', 1),
                                ("episode-2", '{"key":"42.0.2","commands":[["send","y","plain"]]}', 0)])
                db.commit()
            source = self.directory / "families.db"
            shutil.copyfile(oracle.path, source)
        return source

    def test_the_topics_jobs_and_provider_notices_are_copied_with_the_core(self):
        source = self.families_source()
        report = self.migrate_here(source)
        self.assertTrue(report["verified"])
        self.assertEqual(migration.verify_readers(source, str(self.server.socket)), [])
        self.assertEqual(input_ack._ledger_call("families-census", (), 30, socket=str(self.server.socket))[1:],
                         (3, 1, 2, 1))
        input_ack.close_ledger()

    def test_add_families_copies_them_into_a_store_that_holds_the_core(self):
        source = self.families_source()
        done = self.run_script("--source", str(source), "--socket", str(self.server.socket), "--add-families",
                               "--verify-readers")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual((report["topics"], report["model_jobs"], report["provider_notices"], report["active_topic"],
                          report["readers_differing"]), (3, 1, 2, True, []))
        again = self.run_script("--source", str(source), "--socket", str(self.server.socket), "--add-families")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already", again.stderr)

    def test_a_large_ledger_is_copied_in_batches(self):
        operations = [("mark", ["id-%04d" % n for n in range(300)], float(n)) for n in range(1)]
        operations += [("register", "task-%03d" % n, "id-%04d" % n if n % 2 else "free-%d" % n, LANE, 100.0 + n)
                       for n in range(120)]
        source = self.build(operations)
        notices, processed = tables(source)
        started = time.monotonic()
        done = self.run_script("--source", str(source), "--socket", str(self.server.socket), "--batch", "50")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["batches"], -(-(len(processed) + len(notices)) // 50) + -(-report["turn_core_records"] // 50))
        self.assertGreater(report["batches"], 4)
        self.assertEqual(self.dump(), (notices, processed))
        self.assertLess(time.monotonic() - started, 120)

    # ---- the working set (meta 'working_set') and the request status ----

    def build_with_checkpoints(self, name="turns.db", extra_working=None):
        """A ledger file whose turns went through Ledger.checkpoint on SQLite: processed ids, notices settled by
        them, a request settled, and the working set of the last turn."""
        from observed_strategy import Ledger
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            store = Ledger(oracle.path)
            for key, kind in REQUESTS:
                store.accept(key, "content of " + key, kind=kind)
            oracle.apply(("register", "t1", "u1", LANE, 100.0))
            oracle.apply(("register", "t2", "u2", LANE, 101.0))
            store.checkpoint(working(10.0, loops=4), ["u1"], "input:one")
            store.checkpoint(extra_working or working(20.0, lastresults=AWKWARD, rest_timer={"deadline": 5.25, "command": AWKWARD}),
                             ["u2", AWKWARD], "input:two")
            target = self.directory / name
            shutil.copyfile(oracle.path, target)
            expected = store.working_checkpoint()
        return target, expected

    def native_state(self):
        return parse_native(self.run_client("dump", native_dump_program(self.server.socket)))

    def test_the_working_set_is_copied_byte_for_byte_and_the_store_goes_on_from_it(self):
        from observed_strategy import Ledger
        source, expected = self.build_with_checkpoints()
        with closing(sqlite3.connect(source)) as db:
            text = db.execute("SELECT value FROM meta WHERE key='working_set'").fetchone()[0]
            flags = {row[0] for row in db.execute("SELECT request FROM request_context WHERE processed=1")}
        self.assertEqual(flags, {"input:one", "input:two"})
        before = digest(source)
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["working_set_bytes"], len(text.encode("utf-8")))
        notices, ids, stored, requests, atoms = self.native_state()
        self.assertEqual(stored, [(sha(text), len(text.encode("utf-8")))])
        self.assertEqual(requests, {"input:one": 0.0, "input:two": 0.0},
                         "the settled requests are migrated; the time SQLite never recorded is 0.0")
        self.assertEqual(digest(source), before)
        # The native boot read gives the working set the SQLite one gave, and checkpoints continue from it.
        with oracle_environment() as directory:
            os.environ["METTACLAW_STRATEGY_PATH"] = str(source)
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            store = Ledger(source)
            self.assertEqual(input_ack.working_set(), text)
            self.assertEqual(store.working_checkpoint(), expected)
            self.assertEqual(input_ack.committed(["u1", "u2", AWKWARD]), 1)
            # input:one is settled in the migrated record already: the native checkpoint keeps its first time;
            # input:three is not settled yet.
            store.checkpoint(working(30.0, loops=5), ["u3"], "input:one")
            store.checkpoint(working(31.0, loops=6), ["u4"], "input:three")
            self.assertEqual(store.working_checkpoint(), working(31.0, loops=6))
            self.assertEqual(input_ack.committed(["u1", "u3", "u4"]), 1)
        notices, ids, stored, requests, atoms = self.native_state()
        self.assertEqual(requests, {"input:one": 0.0, "input:two": 0.0, "input:three": 31.0})
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][0], sha(encoded(working(31.0, loops=6))))

    def test_a_ledger_without_a_working_set_gives_a_store_that_reads_none(self):
        source = self.build(EVERY_STATE)
        with closing(sqlite3.connect(source)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meta WHERE key='working_set'").fetchone()[0], 0)
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(json.loads(done.stdout)["working_set_bytes"])
        self.assertEqual(self.native_state()[2], [])
        with oracle_environment():
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            self.assertIsNone(input_ack.working_set())

    def test_a_working_set_that_is_not_the_json_text_of_an_object_is_refused_before_anything_is_written(self):
        source, _ = self.build_with_checkpoints()
        before = digest(source)
        for value in ("not json", "[1, 2]", '"text"', "5", ""):
            with self.subTest(value=value):
                broken = self.directory / "broken.db"
                shutil.copyfile(source, broken)
                with closing(sqlite3.connect(broken)) as db:
                    db.execute("UPDATE meta SET value=? WHERE key='working_set'", (value,))
                    db.commit()
                self.fresh_target()
                done = self.migrate_cli(broken)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("working_set", done.stderr)
                self.assertEqual(self.native_state()[1:4], ({}, [], {}))
                self.assertEqual(self.dump(), ([], {}))
        self.assertEqual(digest(source), before)

    def test_values_of_megabytes_are_copied_and_read_back(self):
        # The ledger's server takes values of input_ack.LEDGER_VALUE_BYTES (the engine's default is 1 MiB).
        source, _ = self.build_with_checkpoints()
        big = self.directory / "big.db"
        shutil.copyfile(source, big)
        with closing(sqlite3.connect(big)) as db:
            db.execute("UPDATE meta SET value=? WHERE key='working_set'", (encoded(working(40.0, lastresults="x" * 1200000)),))
            db.execute("UPDATE requests SET content=printf('%.*c', 3000000, 'y') WHERE id='task-1'")
            db.commit()
        done = self.run_script("--source", str(big), "--socket", str(self.server.socket))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_working_set_the_store_cannot_hold_is_refused_before_anything_else_is_copied(self):
        # A server started with the engine's default allowance (1 MiB) cannot take this working set. It is in the
        # first batch, so the refusal leaves the target empty and the run can be repeated on a server that takes it.
        source, _ = self.build_with_checkpoints()
        huge = self.directory / "huge.db"
        shutil.copyfile(source, huge)
        with closing(sqlite3.connect(huge)) as db:
            db.execute("UPDATE meta SET value=? WHERE key='working_set'", (encoded(working(40.0, lastresults="x" * 1200000)),))
            db.commit()
        self.server.stop()
        (self.directory / "small").mkdir()
        self.server = DbServer(self.directory / "small", value_bytes=1048576)
        self.server.start()
        # One row per batch: were the working set not in the first batch, rows would be committed before it fails.
        done = self.run_script("--source", str(huge), "--socket", str(self.server.socket), "--batch", "1")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refused the working set (", done.stderr)
        notices, ids, stored, requests, atoms = self.native_state()
        self.assertEqual((notices, ids, stored, requests, atoms), ([], {}, [], {}, 0), "a refused working set left a partial copy")
        self.assertEqual(self.migrate_cli(source).returncode, 0, "the empty target takes the real copy")

    def test_a_working_set_that_differs_after_the_copy_is_detected(self):
        source, _ = self.build_with_checkpoints()
        change = lambda call: call("checkpoint", (encoded(working(99.0)), (), "", 99.0, "ck1.after-the-copy"))
        with self.assertRaises(migration.MigrationError) as raised:
            self.migrate_here(source, after_copy=change)
        self.assertIn("working set read back", str(raised.exception))
        self.fresh_target()
        self.assertTrue(self.migrate_here(source)["verified"])

    def test_a_target_holding_only_a_working_set_is_not_empty(self):
        source = self.build(EVERY_STATE)
        with oracle_environment():
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            input_ack.turn_checkpoint(encoded(working(1.0)), [], "", 1.0)
        input_ack.close_ledger()
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not empty", done.stderr)
        self.assertEqual(self.native_state()[2:3], ([(sha(encoded(working(1.0))), len(encoded(working(1.0)).encode()))],))

    # ---- the requests (requests + request_context) ----

    def build_requests(self, name="requests.db", covered=False):
        """A ledger file whose requests went through the real Ledger methods (SQLite path): every kind, every status
        a request can have, settled and not, notes, an awkward id and content, a content over the 4000-character page."""
        from observed_strategy import Ledger
        import task_context
        with oracle_environment() as directory:
            oracle = Oracle.create(directory)
            store = Ledger(oracle.path)
            store.accept("input:1", "first question", kind="message")
            store.accept("input:2", "second", kind="message")
            store.accept("input:3", "third", kind="notification")
            store.accept("task-1", "do the thing", kind="task")
            store.accept("task-2", "wait for it", kind="task")
            store.accept("task-3", "verified", "sha256:" + "ab" * 32, kind="task")
            store.accept("goal:o:1:0", "Goal one, cycle 0.")
            store.accept("goal:o:1:1", "Goal one, cycle 1.")
            store.accept("autonomous", "Ongoing work")
            store.accept("big", ("%d \u00e9\u65e5\u672c\U0001d11e \"q\" \\ \n" * 1) * 1 + "z" * 9000, kind="task")
            store.accept("t\u00e4sk \"q\"\n2", AWKWARD, AWKWARD + "|checker", kind="task")
            store.checkpoint(working(1.0), ["a"], "input:1")
            store.processed("input:3")
            store.disposition("input:1", "addressed", "answered \u00e9")
            store.disposition("task-2", "waiting-user", "waiting " + AWKWARD[:100])
            store.disposition("goal:o:1:0", "keep", "n") if False else None
            store.cancel("task-1", True)
            store.retire("goal:o:1:", "composted")
            store.disposition("input:2", "defer", "later")
            if covered:
                for key, ids in (("input:c1", ["1"]), ("input:c2", ["1", "2"])):
                    store.accept(key, "text " + key, kind="message")
                    store.link_inputs(key, ids)
                    task_context.begin(store, key, "operator-event", None, "chat")
            target = self.directory / name
            shutil.copyfile(oracle.path, target)
        return target

    def oracle_answers(self, source, native):
        """Every reader's answer on the SQLite ledger file `source` (native=False) or, with the variable set, on the native store."""
        from observed_strategy import Ledger
        with oracle_environment() as directory:
            os.environ["METTACLAW_STRATEGY_PATH"] = str(source)
            if native:
                os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            store = Ledger(source)
            page = store.request_page(0, 1000)
            for record in page["records"]:  # SQLite's substr() stops at an embedded NUL (see test_request_family_native)
                record["summary"] = record["summary"].split("\x00")[0]
            view = store.protected("input:1")
            view.pop("owner")
            return {"page": page, "protected": view, "active": store.active(),
                    "records": [store.request_record(row["id"]) for row in page["records"]],
                    "projection": store.projection()["requests"]}

    def test_the_requests_are_copied_row_for_row_and_every_reader_answers_alike_afterwards(self):
        source = self.build_requests()
        before = digest(source)
        with closing(sqlite3.connect(source)) as db:
            statuses = {row[0] for row in db.execute("SELECT status FROM requests")}
            kinds = {row[0] for row in db.execute("SELECT kind FROM request_context")}
            self.assertEqual(statuses, {"active", "processed", "addressed", "cancelled", "deferred", "waiting-user"})
            self.assertEqual(kinds, {"message", "notification", "task", "goal", "autonomous"})
            total = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual((report["requests"], report["verified"]), (total, True))
        self.assertEqual(digest(source), before, "the source ledger changed")
        self.assertEqual([entry.name for entry in self.directory.iterdir() if entry.name.startswith("requests.db")],
                         ["requests.db"], "the source left a journal behind")
        expected = self.oracle_answers(source, native=False)
        got = self.oracle_answers(source, native=True)
        for part in expected:
            self.assertEqual(got[part], expected[part], part)
        full = parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(sorted(v[7] for v in full["requests"].values()), list(range(1, total + 1)))
        self.assertEqual(full["counter"], [total])
        self.assertEqual({k for k, v in full["requests"].items() if v[3] is not None},
                         {"input:1", "input:3"}, "the settled requests are those whose request_context.processed is 1")
        self.assertTrue(all(v[3] == 0.0 for v in full["requests"].values() if v[3] is not None),
                        "SQLite never recorded when: 0.0")
        # The migrated store goes on as the SQLite ledger would have: the next request is number total+1, newest first.
        from observed_strategy import Ledger
        with oracle_environment():
            os.environ["METTACLAW_STRATEGY_PATH"] = str(source)
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            store = Ledger(source)
            self.assertTrue(store.accept("input:new", "after the copy", kind="message"))
            self.assertEqual(store.request_page(0, 2)["records"][0]["id"], "input:new")
            self.assertFalse(store.accept("input:1", "first question", kind="message"))
            self.assertFalse(store.processed("input:1"), "settled before the copy")
            self.assertTrue(store.disposition("input:2", "reopen", "back"))
            self.assertTrue(store.checkpoint(working(50.0), ["z"], "input:new") is None)
            self.assertEqual(store.request_record("input:new")["status"], "processed")
        full = parse_native_full(self.run_client("dump", native_dump_program(self.server.socket)))
        self.assertEqual(full["counter"], [total + 1])
        self.assertEqual(full["requests"]["input:new"][7], total + 1)

    def test_requests_are_copied_in_small_batches_with_each_record_beside_its_content(self):
        source = self.build_requests()
        done = self.run_script("--source", str(source), "--socket", str(self.server.socket), "--batch", "3")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        # a working set, the processed ids and notices, a record, a content and a link per request, the tail atom
        atoms = (1 if report["working_set_bytes"] else 0) + report["processed_inputs"] + report["input_notices"] \
            + 3 * report["requests"] + 1
        self.assertEqual(report["batches"], -(-atoms // 3) + -(-report["turn_core_records"] // 3))
        self.assertGreater(report["batches"], 7)
        self.assertTrue(report["verified"])

    def test_a_request_the_native_store_cannot_represent_is_refused_before_anything_is_written(self):
        source = self.build_requests()
        before = digest(source)
        cases = {
            "unknown status": ("UPDATE requests SET status='bogus' WHERE id='task-1'", "status that is none of"),
            "unknown kind": ("UPDATE request_context SET kind='bogus' WHERE request='task-1'", "kind that is none of"),
            "a processed flag of 2": ("UPDATE request_context SET processed=2 WHERE request='task-1'", "processed is not 0 or 1"),
            "no context row": ("DELETE FROM request_context WHERE request='task-1'", "no request_context row"),
            "a content that is no text": ("UPDATE requests SET content=CAST(x'ff' AS BLOB) WHERE id='task-1'", "not text"),
        }
        for name, (statement, message) in cases.items():
            with self.subTest(case=name):
                broken = self.directory / "broken.db"
                shutil.copyfile(source, broken)
                with closing(sqlite3.connect(broken)) as db:
                    db.execute(statement)
                    db.commit()
                self.fresh_target()
                done = self.run_script("--source", str(broken), "--socket", str(self.server.socket), "--batch", "1")
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(message, done.stderr)
                self.assertEqual(self.native_state(), ([], {}, [], {}, 0), "something was written before the refusal")
                self.assertEqual(self.dump(), ([], {}))
        self.assertEqual(digest(source), before)
        self.fresh_target()
        self.assertEqual(self.migrate_cli(source).returncode, 0, "the empty target takes the real copy")

    def test_a_request_that_differs_after_the_copy_is_detected_by_the_row_for_row_check(self):
        source = self.build_requests()

        def tamper_status(call):
            found = call("lookup", (S("request"), ("input:2",)))
            old = [item for item in found if item[0] == S("request")][0]
            self.edit(old, (old[0],) + old[1:5] + ("waiting-user",) + old[6:])

        def tamper_text(call):
            self.edit((S("request-text"), "task-2", "wait for it"), (S("request-text"), "task-2", "wait for it!"))

        for name, tamper, message in (("status", tamper_status, "request row"), ("content", tamper_text, "request row")):
            with self.subTest(case=name):
                self.fresh_target()
                with self.assertRaises(migration.MigrationError) as raised:
                    self.migrate_here(source, after_copy=tamper)
                self.assertIn(message, str(raised.exception))

    def edit(self, old, new):
        """Replace one atom of the native store (a test-only edit through a cetta client)."""
        from test_input_ledger_remote import PRELUDE, literal
        out = self.run_client("edit", PRELUDE + (
            '!(let* (($db (db:connect %s))\n'
            '        ($r (db:transaction-result $db inputs "edit.%d" edit (|-> ($s) (let* (($a (remove-atom $s %s)) ($b (add-atom $s %s))) done)))))\n'
            '   (println! (edit $r)))\n') % (literal(str(self.server.socket)), time.monotonic_ns(),
                                              atom_text(old), atom_text(new)))
        self.assertIn("db:committed", out)

    def test_a_target_holding_only_a_request_is_not_empty(self):
        source = self.build_requests()
        with oracle_environment() as directory:
            from observed_strategy import Ledger
            NativeRunner.create(directory, self.server.socket)
            Ledger(directory / "x.db").accept("input:early", "x", kind="message")
        input_ack.close_ledger()
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not empty", done.stderr)

    # ---- refusals ----

    def test_a_target_that_is_not_empty_is_refused_and_left_alone(self):
        source = self.build(EVERY_STATE)
        before = digest(source)
        for populate in (lambda: input_ack.processed_update_ids(["already"]),
                         lambda: self.run_native([("register", "t0", "u0", LANE, 1.0)])):
            self.fresh_target()
            with oracle_environment():
                os.environ["METTACLAW_STRATEGY_PATH"] = str(self.directory / "gate.db")
                os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
                populate()
            input_ack.close_ledger()
            state = self.dump()
            done = self.migrate_cli(source)
            self.assertEqual(done.returncode, 1)
            self.assertIn("not empty", done.stderr)
            self.assertEqual(self.dump(), state, "a refused migration changed the target")
            self.assertEqual(digest(source), before)

    def test_a_second_run_is_refused_and_changes_nothing(self):
        source = self.build(EVERY_STATE)
        self.assertEqual(self.migrate_cli(source).returncode, 0)
        state = self.dump()
        again = self.migrate_cli(source)
        self.assertEqual(again.returncode, 1)
        self.assertIn("not empty", again.stderr)
        self.assertEqual(self.dump(), state)
        # Also in process, and also when the source is another ledger.
        other = self.build([("mark", ["other"], 1.0)], name="other.db")
        with self.assertRaises(migration.MigrationError):
            self.migrate_here(other)
        self.assertEqual(self.dump(), state)

    def test_a_store_that_fills_during_the_copy_is_refused_by_the_first_batch_itself(self):
        # The emptiness check and the first batch are one transaction: whoever writes between the census
        # and the first batch makes the batch refuse (`refused`), and nothing of the source reaches the store.
        source = self.build(EVERY_STATE)
        real = input_ack._ledger_call

        def racing(verb, arguments, timeout=10, socket=None):
            if verb == "import" and arguments[0] == S("empty"):
                real("mark-processed", (1.0, ("intruder",)), 10, socket)
            return real(verb, arguments, timeout, socket)

        with mock.patch.object(input_ack, "_ledger_call", racing):
            with self.assertRaises(migration.MigrationError) as raised:
                self.migrate_here(source)
        self.assertIn("not empty", str(raised.exception))
        self.assertEqual(self.dump(), ([], {"intruder": 1.0}))

    def test_a_difference_between_the_copy_and_the_source_is_detected(self):
        source = self.build(EVERY_STATE)
        tasks = {row[0]: row for row in tables(source)[0]}
        registered = [task for task, row in tasks.items() if row[8] == "registered" and row[5] is None][0]
        cases = {
            "state": lambda call: call("submitted", (registered,)),
            "checked": lambda call: call("check", (registered, 5.0, 172800)),
            "message_id": lambda call: call("receipt", (tasks[registered][3], E("delivered", 99))),
        }
        for column, change in cases.items():
            with self.subTest(column=column):
                self.fresh_target()
                with self.assertRaises(migration.MigrationError) as raised:
                    self.migrate_here(source, after_copy=change)
                self.assertIn("differs in: " + column, str(raised.exception))
        for name, change, wanted in (
                ("an extra id", lambda call: call("mark-processed", (1.0, ("extra",))), "1 more id"),):
            with self.subTest(case=name):
                self.fresh_target()
                with self.assertRaises(migration.MigrationError) as raised:
                    self.migrate_here(source, after_copy=change)
                self.assertIn("the target holds", str(raised.exception))
        # The check is not vacuous: without a seam the same copy passes.
        self.fresh_target()
        self.assertTrue(self.migrate_here(source)["verified"])

    def test_the_comparison_names_every_column_that_differs(self):
        want = E("input-notice", "t", "u", "l", "s", 1.5, S("none"), 0, S("none"), S("registered"), 0.0)
        replacements = {"task": "x", "update_id": "x", "lane": "x", "send_key": "x", "created": 2.5, "message_id": 7,
                        "processed": 1, "delete_key": "k", "state": S("expired"), "checked": 3.0}
        from execution_gateway.cetta_worker import FloatBits
        for index, name in enumerate(migration.NOTICE_COLUMNS, start=1):
            have = list(want)
            value = replacements[name]
            have[index] = FloatBits.of(value) if isinstance(value, float) else value
            for position, original in enumerate(want):
                if isinstance(original, float) and have[position] == original:
                    have[position] = FloatBits.of(original)
            self.assertEqual(migration._mismatch(tuple(have), want), [name], name)
        same = [FloatBits.of(item) if isinstance(item, float) else item for item in want]
        self.assertEqual(migration._mismatch(tuple(same), want), [])
        # A number that came back as an integer, or text for a symbol, is a difference too.
        same[5] = 1
        same[6] = "none"
        self.assertEqual(migration._mismatch(tuple(same), want), ["created", "message_id"])
        self.assertEqual(migration._mismatch((S("processed-input"), "a"), E("processed-input", "a", 1.0)), ["shape"])

    # ---- the source: read-only, explicit, validated ----

    def test_the_source_is_opened_read_only_and_a_read_only_file_in_a_read_only_directory_works(self):
        (self.directory / "ro").mkdir()
        source = self.build(EVERY_STATE, name="ro/ro.db")
        opened = []
        real = sqlite3.connect

        def spy(target, *args, **kwargs):
            opened.append(target)
            return real(target, *args, **kwargs)

        with mock.patch.object(migration.sqlite3, "connect", spy):
            self.assertTrue(self.migrate_here(source)["verified"])
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].endswith("?mode=ro"), opened)
        before = digest(source)
        os.chmod(source, 0o444)
        os.chmod(source.parent, 0o555)
        self.addCleanup(os.chmod, source.parent, 0o755)
        self.fresh_target()
        done = self.migrate_cli(source)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(digest(source), before)
        self.assertEqual(sorted(entry.name for entry in source.parent.iterdir()), ["ro.db"])

    def test_ids_with_line_breaks_and_control_characters_are_copied_and_found(self):
        ids = ["line\nbreak", "cr\rlf\r\n", "nul\u0000inside", "tab\there", "\u2028sep", "'single'", "\"double\""]
        source = self.build([("mark", ids, 5.0)])
        self.assertEqual(set(tables(source)[1]), set(ids))
        self.assertTrue(self.migrate_here(source)["verified"])
        with oracle_environment():
            os.environ["METTACLAW_STRATEGY_PATH"] = str(self.directory / "gate.db")
            os.environ[input_ack.LEDGER_SOCKET] = str(self.server.socket)
            self.assertEqual(input_ack.committed(ids), 1)
            self.assertEqual(input_ack.committed(ids + ["line"]), 0)

    def test_nothing_is_taken_from_the_environment(self):
        source = self.build(EVERY_STATE)
        decoy = self.build([("mark", ["decoy"], 1.0)], name="decoy.db")
        before = digest(decoy)
        missing = self.run_script("--source", str(source), METTACLAW_INPUT_LEDGER_SOCKET=str(self.server.socket),
                                  METTACLAW_STRATEGY_PATH=str(decoy))
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(self.dump(), ([], {}))
        self.assertEqual(self.run_script("--socket", str(self.server.socket), METTACLAW_STRATEGY_PATH=str(decoy)).returncode, 2)
        done = self.migrate_cli(source, METTACLAW_INPUT_LEDGER_SOCKET=str(self.directory / "decoy.sock"),
                                METTACLAW_STRATEGY_PATH=str(decoy), METTACLAW_ENGINE_STATE_PATH=str(decoy),
                                METTACLAW_INSTANCE="decoy")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.dump(), tables(source)[0:1] + (tables(source)[1],))
        self.assertEqual(digest(decoy), before)
        self.assertNotIn(str(decoy), done.stdout + done.stderr)

    def test_a_missing_source_an_unreachable_target_and_a_file_that_is_no_ledger_copy_nothing(self):
        source = self.build(EVERY_STATE)
        missing = self.migrate_cli(self.directory / "nosuch.db")
        self.assertEqual(missing.returncode, 1)
        self.assertFalse((self.directory / "nosuch.db").exists(), "a missing source must not be created")
        down = self.run_script("--source", str(source), "--socket", str(self.directory / "nobody.sock"))
        self.assertEqual(down.returncode, 1)
        self.assertIn("native ledger failed", down.stderr)
        stranger = self.directory / "stranger.db"
        with closing(sqlite3.connect(stranger)) as db:
            db.execute("CREATE TABLE other (x)")
            db.commit()
        refused = self.migrate_cli(stranger)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("not an input ledger", refused.stderr)
        text = self.directory / "text.db"
        text.write_text("not sqlite at all, but long enough to look like a file " * 5)
        self.assertEqual(self.migrate_cli(text).returncode, 1)
        self.assertEqual(self.dump(), ([], {}))

    def test_values_that_the_ledger_never_holds_are_refused_before_anything_is_written(self):
        source = self.build([("register", "t1", "u1", LANE, 100.0)])
        notice = self.directory / "notice.db"
        cases = {
            "state": "UPDATE input_notices SET state='odd'",
            "processed": "UPDATE input_notices SET processed=2",
            "message_id": "UPDATE input_notices SET message_id=1.5",
            "blob": "UPDATE input_notices SET lane=x'00ff'",
            "processed id": "UPDATE processed_inputs SET processed='soon'",
        }
        for name, statement in cases.items():
            with self.subTest(case=name):
                shutil.copyfile(source, notice)
                with closing(sqlite3.connect(notice)) as db:
                    db.execute("INSERT INTO processed_inputs VALUES ('p', 1.0)")
                    db.execute(statement)
                    db.commit()
                done = self.migrate_cli(notice)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("input_notices row 0" if "notice" in statement else "processed_inputs row 0", done.stderr)
                self.assertEqual(self.dump(), ([], {}), "something was written before the refusal")
                self.fresh_target()

    def test_a_ledger_from_before_the_checked_column_reads_as_zero(self):
        source = self.build([("register", "t1", "u1", LANE, 100.0), ("mark", ["a"], 1.0)])
        legacy = self.directory / "legacy.db"
        with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(legacy)) as new:
            new.execute("CREATE TABLE processed_inputs (id TEXT PRIMARY KEY, processed REAL NOT NULL)")
            new.execute("INSERT INTO processed_inputs VALUES ('a', 1.0)")
            new.execute("CREATE TABLE input_notices (task TEXT PRIMARY KEY, update_id TEXT NOT NULL, lane TEXT NOT NULL,"
                        " send_key TEXT UNIQUE NOT NULL, created REAL NOT NULL, message_id INTEGER,"
                        " processed INTEGER NOT NULL DEFAULT 0, delete_key TEXT, state TEXT NOT NULL DEFAULT 'registered')")
            for row in old.execute("SELECT task,update_id,lane,send_key,created,message_id,processed,delete_key,state FROM input_notices"):
                new.execute("INSERT INTO input_notices VALUES (?,?,?,?,?,?,?,?,?)", row)
            new.commit()
        done = self.migrate_cli(legacy)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows, ids = self.dump()
        self.assertEqual([row[9] for row in rows], [0.0])
        self.assertEqual(ids, {"a": 1.0})

    def test_usage_errors(self):
        self.assertEqual(self.run_script().returncode, 2)
        source = self.build([])
        self.assertEqual(self.run_script("--source", str(source), "--socket", str(self.server.socket), "--batch", "0").returncode, 2)
        self.assertEqual(self.run_script("--source", str(source), "--socket", "").returncode, 1)


if __name__ == "__main__":
    unittest.main()
