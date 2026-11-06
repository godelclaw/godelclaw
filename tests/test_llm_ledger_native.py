"""The LLM call ledger in the native store (src/llm_ledger.metta), against its SQLite oracle.

Every scenario starts from one SQLite ledger, copies it, and migrates the copy into a fresh native
store with scripts/migrate_llm_ledger.py. It then runs the same operations twice: on the SQLite file
(no ledger socket) and on the native store (METTACLAW_INPUT_LEDGER_SOCKET), with the same clock and
the same ids. Every answer, raised exception and the final rows of every table must agree, the
migrated copy must stay byte for byte as it was (the native run writes no SQLite), and the native
store's derived records (the newest-first turn list, the history counter) must match its calls and
events. The Python callers (llm_policy, native_lifetime: the host's path and the Python transport)
and the MeTTa callers (llm_policy_cetta, native_lifetime_cetta: the native transport) are both run.

Faults: a repeated request id replays its answer and a changed payload under it is refused; a ledger
that is importing or unreachable raises instead of falling back to SQLite; a rollback hands the
ledger back to SQLite; a failure's request, event and cooldown survive a server crash together or
not at all; capacity is shared by processes (the agent and its coding sessions).
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "scripts")]
import input_ack
import llm_policy
import native_lifetime
import migrate_llm_ledger
from execution_gateway.cetta_worker import S
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import available

CETTA = os.environ.get("CETTA_BIN")
DEAD_OWNER = "999999999:1"   # beyond pid_max: never a live process
LIVE = llm_policy._owner


class Ids:
    """uuid4 for llm_policy: the same tickets and request ids on both sides."""
    def __init__(self):
        self.n = 0

    def uuid4(self):
        self.n += 1
        return SimpleNamespace(hex="%032x" % self.n)


def outcome(function, *arguments, **options):
    try:
        return ("returned", function(*arguments, **options))
    except Exception as error:  # the type and message are the observation
        return ("raised", type(error).__name__, str(error)[:200])


def seed(path, now, rows=()):
    """A ledger file made by llm_policy itself at `now`, plus the given (sql, parameters) rows."""
    with mock.patch.dict(os.environ, {"METTACLAW_LLM_LEDGER_PATH": str(path)}), \
            mock.patch("time.time", return_value=now):
        with native_lifetime.transaction():
            pass
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS admission_recovery (id TEXT PRIMARY KEY, recovered_at REAL NOT NULL, "
                   "reason TEXT NOT NULL, cutoff REAL NOT NULL)")
        for sql, parameters in rows:
            db.execute(sql, parameters)


SELF = object()


def call_row(identity, *, epoch="e", pid=os.getpid(), start=SELF, created=100.0, updated=100.0, state="claimed",
             activity="", lane="", purpose="turn", notified=0, result=None, reason=None):
    start = native_lifetime.process_start(os.getpid()) if start is SELF else start
    return ("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,transport,soft_elapsed,"
            "bytes,quiet_ms,result,reason,activity,lane,purpose,notified) VALUES(?,?,?,?,?,?,?,NULL,NULL,0,0,0,?,?,?,?,?,?)",
            (identity, epoch, pid, start, created, updated, state, result, reason, activity, lane, purpose, notified))


def request_row(identity, *, started=100.0, status="pending", owner=LIVE, provider="anthropic", mode="godelclaw",
                output=None, finish=None):
    return ("INSERT INTO requests(id,started,reserved,status,output,provider,mode,finish,owner,fingerprint) "
            "VALUES(?,?,100,?,?,?,?,?,?,'fp')", (identity, started, status, output, provider, mode, finish, owner))


def event_row(identity, event, observed, detail):
    return ("INSERT INTO llm_call_history(call_id,event,observed,detail) VALUES(?,?,?,?)",
            (identity, event, observed, detail))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def typed(row):
    return tuple((type(value).__name__, repr(value)) for value in row)


def plan(identity, epoch, *, activity="agent-a", lane="interactive", purpose="turn", sent=False):
    return SimpleNamespace(correlation=identity, epoch=epoch, activity=activity, lane=lane, purpose=purpose,
                           guard="revision", cap=1 << 20, sent=sent, soft=float("inf"))


@unittest.skipUnless(available(), SKIP)
class LlmLedgerNative(NativeCase):
    maxDiff = None
    def setUp(self):
        super().setUp()
        self.oracle = self.directory / "oracle.sqlite3"
        self.copy = self.directory / "copy.sqlite3"
        self.env = {"METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
                    "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw", "METTACLAW_INSTANCE": "fixture",
                    "METTACLAW_ACTIVITY_CALL_CAPACITY": "2", "METTACLAW_ACTIVITY_INTERACTIVE_RESERVE": "1"}
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)

    def start(self, rows=(), now=50.0):
        seed(self.oracle, now, rows)
        shutil.copyfile(self.oracle, self.copy)
        self.copied = digest(self.copy)
        migrate_llm_ledger.copy(self.copy, self.server.socket, progress=lambda line: None)
        self.assertEqual(migrate_llm_ledger.verify(self.copy, self.server.socket), [])
        input_ack.close_ledger()

    @contextmanager
    def side(self, native, now):
        clock = SimpleNamespace(now=now)
        environment = {"METTACLAW_LLM_LEDGER_PATH": str(self.copy if native else self.oracle)}
        if native:
            environment["METTACLAW_INPUT_LEDGER_SOCKET"] = str(self.server.socket)
        with mock.patch.dict(os.environ, environment), mock.patch("time.time", lambda: clock.now), \
                mock.patch.object(llm_policy, "uuid", Ids()):
            if not native:
                os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
            yield clock

    def both(self, scenario, now=1000.0):
        """The scenario's observations on SQLite and natively, compared; then the final states."""
        with self.side(False, now) as clock:
            sqlite_answers = scenario(clock)
        with self.side(True, now) as clock:
            native_answers = scenario(clock)
        self.assertEqual(native_answers, sqlite_answers)
        self.assert_same_state()
        return sqlite_answers

    def assert_same_state(self, normalize=lambda name, row: row):
        self.assertEqual(digest(self.copy), self.copied, "the native run wrote the SQLite copy")
        tables = migrate_llm_ledger.read_source(self.oracle)
        native = migrate_llm_ledger._native_rows(self.server.socket)
        for name in migrate_llm_ledger.TABLES:
            want = sorted(typed(normalize(name, row)) for row in tables[name])
            have = sorted(typed(normalize(name, row)) for row in native[name])
            self.assertEqual(have, want, name)
        # the derived records follow the calls and the events
        records = {kind: migrate_llm_ledger._call("llm-records", (kind,), self.server.socket)
                   for kind in ("recent", "states")}
        created = {row[0]: row[4] for row in native["native_calls"] if row[15] == "turn"}
        recent = {row[1]: (row[2], row[3]) for row in records["recent"]}
        states = {row[1]: row[2] for row in records["states"]}
        walk, node = [], states.get("recent-turn")
        while node is not None:
            walk.append(node)
            node = recent[node][0]
        self.assertEqual(sorted(walk), sorted(created))
        self.assertEqual([recent[identity][1] for identity in walk], [created[identity] for identity in walk])
        self.assertEqual([created[identity] for identity in walk], sorted(created.values(), reverse=True))
        if native["llm_call_history"]:
            self.assertEqual(states["event-tail"], max(row[0] for row in native["llm_call_history"]))

    # ---- the Python transport (synthetic_llm._exchange) ----

    def test_python_transport_success_failure_cooldown_and_denial(self):
        self.start()

        def scenario(clock):
            result = {"choices": [{"finish_reason": "stop", "message": {}}],
                      "usage": {"prompt_tokens": 12, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 4}}}
            seen = []
            ticket = llm_policy.admit("synthetic", 100, request_fingerprint="fp1")
            seen.append(ticket)
            clock.now += 1
            seen.append(outcome(llm_policy.describe, ticket, model="m", protocol="chat-completions",
                                purpose="turn", transport="python", origin={"trigger": "telegram", "turn_id": "t1"}))
            seen.append(outcome(llm_policy.describe, ticket, model="m2", protocol="p", purpose="turn"))
            clock.now += 1
            seen.append(outcome(llm_policy.submitted, ticket))
            clock.now += 1
            seen.append(outcome(llm_policy.complete, ticket, result))
            seen.append(outcome(llm_policy.complete, ticket, result))
            seen.append(outcome(llm_policy.fail, ticket, sent=True))
            other = llm_policy.admit("synthetic", 50, request_fingerprint="fp2")
            clock.now += 1
            seen.append(outcome(llm_policy.fail, other, status=429, retry_after="30", sent=True))
            clock.now += 1
            seen.append(outcome(llm_policy.admit, "synthetic", 10))
            seen.append(outcome(llm_policy.admit, "anthropic", 10))
            clock.now += 40
            third = llm_policy.admit("synthetic", 10)
            seen.append(outcome(llm_policy.fail, third, status=402, sent=False))
            seen.append(outcome(llm_policy.submitted, third))
            seen.append(outcome(llm_policy.admit, "synthetic", 0))
            # a later, shorter cooldown never shortens a longer one
            longer, shorter = llm_policy.admit("anthropic", 10), llm_policy.admit("anthropic", 10)
            seen.append(outcome(llm_policy.fail, longer, status=429, retry_after="300", sent=True))
            seen.append(outcome(llm_policy.fail, shorter, status=429, retry_after="1", sent=True))
            clock.now += 5
            seen.append(outcome(llm_policy.admit, "anthropic", 10))
            seen.append(llm_policy.snapshot())
            seen.append(llm_policy.unknown_context())
            return seen

        seen = self.both(scenario)
        self.assertEqual(seen[8][:2], ("raised", "AdmissionDenied"))

    # ---- the host's path: claim, submit, progress, settle ----

    def test_host_path_complete_fail_reject_status_and_results(self):
        self.start()

        def scenario(clock):
            seen = []
            result = {"choices": [{"finish_reason": "stop", "message": {}}], "usage": {"input_tokens": 9, "output_tokens": 2}}
            first = llm_policy.admit("anthropic", 100)
            one = plan(first, "epoch-1")
            seen.append(outcome(native_lifetime.claim, one))
            clock.now += 1
            native_lifetime.submitted(one, 41)
            one.sent = True
            native_lifetime.progress(one, 100, 20, 0)
            clock.now += 1
            seen.append(outcome(native_lifetime.complete, one, result, json.dumps({"items": []})))
            seen.append(outcome(native_lifetime.complete, one, result, json.dumps({"items": []})))
            seen.append(outcome(native_lifetime.command_output_rejected, one, "invalid-envelope"))
            seen.append(outcome(native_lifetime.command_output_rejected, one, "no-such-code"))
            clock.now += 1
            second = llm_policy.admit("anthropic", 100)
            two = plan(second, "epoch-2")
            seen.append(outcome(native_lifetime.claim, two))
            native_lifetime.submitted(two, 42)
            two.sent = True
            clock.now += 1
            seen.append(outcome(native_lifetime.fail, two, "transport-error", status=None))
            third = llm_policy.admit("anthropic", 100)
            three = plan(third, "epoch-3", purpose="compaction")
            seen.append(outcome(native_lifetime.claim, three))
            native_lifetime.submitted(three, 43)
            three.sent = True
            clock.now += 1
            seen.append(outcome(native_lifetime.settle, three, "uncertain"))
            fourth = llm_policy.admit("anthropic", 100)
            four = plan(fourth, "epoch-4")
            seen.append(outcome(native_lifetime.claim, four))
            clock.now += 1
            seen.append(outcome(native_lifetime.fail, four, "http-error", status=429))
            seen.append(outcome(native_lifetime.settle, four, "bogus"))
            seen.append(native_lifetime.status())
            seen.append(native_lifetime.result(first))
            seen.append(native_lifetime.result(second, 0))
            seen.append(native_lifetime.result("absent"))
            seen.append(native_lifetime.call_state(one))
            seen.append(native_lifetime.call_state(plan("absent", "x")))
            seen.append(native_lifetime.evidence_context())
            with mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(enqueue_runtime_event=lambda *a, **k: None)}):
                native_lifetime.notify()
            seen.append(native_lifetime.status())
            return seen

        self.both(scenario)

    def test_busy_claims_and_capacity_refusals_fail_exactly_their_request(self):
        self.start()

        def scenario(clock):
            seen = []
            tickets = [llm_policy.admit("anthropic", 100) for _ in range(5)]
            calls = [plan(tickets[0], "a", activity="agent-a", lane="interactive"),
                     plan(tickets[1], "b", activity="work-1", lane="background"),
                     plan(tickets[2], "c", activity="work-2", lane="background"),
                     plan(tickets[3], "d", activity="", lane="")]
            for call in calls:
                answer = outcome(native_lifetime.claim, call)
                seen.append(answer)
                if answer == ("returned", False):
                    seen.append(outcome(llm_policy.fail, call.correlation, sent=False))
            seen.append(outcome(native_lifetime.claim, plan(tickets[4], "e", activity="bad id!", lane="interactive")))
            seen.append(outcome(native_lifetime.claim, plan(tickets[4], "e", activity="agent-z", lane="interactive",
                                                            purpose="nonsense")))
            with mock.patch.dict(os.environ, {"METTACLAW_ACTIVITY_INTERACTIVE_RESERVE": "5"}):
                seen.append(outcome(native_lifetime.claim, plan(tickets[4], "e", activity="agent-y", lane="interactive")))
            seen.append(native_lifetime.status())  # calls claimed at one time, listed in their arrival order
            return seen

        seen = self.both(scenario)
        self.assertEqual([answer for answer in seen if answer[0] == "raised"][0][1], "ValueError")

    def test_recovery_of_dead_owners_interrupted_calls_and_terminal_requests(self):
        dead = dict(pid=999999999, start="1")
        rows = [request_row("p-dead", owner=DEAD_OWNER), request_row("p-live"),
                request_row("c-dead"), call_row("c-dead", **dead),
                request_row("c-unborn"), call_row("c-unborn", start=None, pid=999999998) ,
                request_row("c-done", status="completed"), call_row("c-done", state="submitted", updated=10.0),
                request_row("c-failed", status="failed"), call_row("c-failed", state="claimed", updated=10.0),
                request_row("c-unknown", status="unknown"), call_row("c-unknown", state="submitted", updated=10.0),
                request_row("c-fresh", status="completed"), call_row("c-fresh", state="submitted", updated=999.5),
                request_row("c-compact", status="completed"),
                call_row("c-compact", state="submitted", updated=10.0, purpose="compaction"),
                call_row("c-orphan", state="claimed", updated=10.0, **dead),
                event_row("c-dead", "admitted", 100.0, '{"provider":"anthropic"}')]
        self.start(rows)

        def scenario(clock):
            seen = [outcome(llm_policy.admit, "anthropic", 100)]
            seen.append(outcome(native_lifetime.status))
            seen.append(outcome(native_lifetime.claim, plan("p-live", "z", activity="agent-q")))
            return seen

        self.both(scenario)

    def test_late_usage_and_a_claimed_epoch_without_its_call(self):
        rows = [request_row("old"), event_row("old", "admitted", 100.0, "{}"),
                event_row("old", "claimed", 100.0, '{"epoch":"gone"}'),
                request_row("other"), event_row("other", "admitted", 100.0, "{}")]
        self.start(rows)
        result = {"choices": [{"finish_reason": "length"}], "usage": {"output_tokens": 2 ** 70}}

        def scenario(clock):
            return [outcome(llm_policy.complete, "old", result),
                    outcome(llm_policy.complete, "other", {"choices": [{}], "usage": "bad"})]

        self.both(scenario)

    # ---- the MeTTa callers (the native transport, path A) ----

    def probe(self, forms, native):
        entry = self.directory / ("probe-%s.metta" % ("native" if native else "sqlite"))
        output = self.directory / ("probe-%s.jsonl" % ("native" if native else "sqlite"))
        output.unlink(missing_ok=True)
        lines = ['!(import! &self ' + json.dumps(str(ROOT / "src" / "native_lifetime_cetta.metta")) + ')',
                 '(= (probe:result $value) (case $value '
                 '(((LedgerAdmitted $id) (JsonArray ("admitted" $id))) '
                 ' ((LedgerDenied $code $delay) (JsonArray ("denied" $code $delay))) '
                 ' ((LedgerChanged $changed) (JsonArray ("changed" $changed))) '
                 ' ((LedgerCompletion $state) (JsonArray ("completion" (repr $state)))) '
                 # the SQLite submitted/progress answer and the native one mean the same to account:*
                 ' ((sqlite:committed $results) (JsonArray ("changed" True))) '
                 ' ((LedgerError (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
                 ' ((LedgerError $error) (JsonArray ("error"))) '
                 ' ((LedgerInvalid (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
                 ' ($other (JsonArray ("unexpected" (repr $other)))))))',
                 '(= (probe:emit $value) (fs:append-text ' + json.dumps(str(output)) +
                 ' (str:concat (json:stringify $value) "\\n")))']
        lines += ['!(probe:emit (probe:result %s))' % form for form in forms]
        lines.append('!(probe:emit (lp:owner))')
        entry.write_text("\n".join(lines) + "\n")
        environment = dict(os.environ, METTACLAW_LLM_LEDGER_PATH=str(self.copy if native else self.oracle))
        environment.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
        if native:
            environment["METTACLAW_INPUT_LEDGER_SOCKET"] = str(self.server.socket)
        run = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet", str(entry)],
                             cwd=ROOT, env=environment, capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(len(rows), len(forms) + 1, run.stdout[-2000:] + run.stderr[-2000:])
        return rows[:-1], rows[-1]

    def test_metta_callers_match_their_sqlite_answers_and_rows(self):
        self.start([request_row("p-dead", owner=DEAD_OWNER)])

        def meta(identity, epoch, **fields):
            value = dict(id=identity, epoch=epoch, guard="revision", cap=1024, activity="agent-m", lane="interactive",
                         purpose="turn", sent=False)
            value.update(fields)
            return "(json:parse %s)" % json.dumps(json.dumps(value))
        usage = "(json:parse %s)" % json.dumps(json.dumps({"input_tokens": 7, "output_tokens": 3}))
        forms = ['(lp:admit "t1" "anthropic" 100 "fp" 1000.0)',
                 '(lp:describe-origin "t1" "m" "p" "turn" "native" (json:parse "{\\"trigger\\": \\"x\\"}") 1000.5)',
                 '(nl:claim %s 1001.0)' % meta("t1", "e1"),
                 '(nl:submitted %s (json:parse "7") 1002.0)' % meta("t1", "e1"),
                 '(nl:progress %s 10 20 0 1003.0)' % meta("t1", "e1"),
                 '(nl:complete %s %s "stop" "STALE" 0 1003.5)' % (meta("t1", "stale"), usage),
                 '(nl:complete %s %s "stop" "CANON" 0 1004.0)' % (meta("t1", "e1"), usage),
                 '(nl:completion-state %s "CANON")' % meta("t1", "e1"),
                 '(nl:completion-state %s "OTHER")' % meta("t1", "e1"),
                 '(nl:command-output-rejected %s "invalid-envelope" 1005.0)' % meta("t1", "e1"),
                 '(lp:admit "t2" "chatgpt" 100 "fp" 1006.0)',
                 '(nl:claim %s 1007.0)' % meta("t2", "e2"),
                 '(nl:submitted %s (json:parse "8") 1008.0)' % meta("t2", "e2"),
                 '(nl:fail %s "transport-error" 429 JsonNull 0 1009.0)' % meta("t2", "e2", sent=True),
                 '(nl:fail %s "transport-error" 429 JsonNull 0 1009.5)' % meta("t2", "e2", sent=True),
                 '(lp:admit "t3" "chatgpt" 100 "fp" 1010.0)',
                 '(lp:admit "t3b" "anthropic" 100 "fp" 1010.0)',
                 '(lp:fail "t3b" False JsonNull JsonNull 1011.0)',
                 '(nl:claim %s 1012.0)' % meta("t4", "e4", activity="bad id!"),
                 '(nl:settle %s "late" JsonNull JsonNull 0 1013.0)' % meta("t9", "e9"),
                 '(lp:complete "t3b" %s "stop" 1014.0)' % usage]
        want, owner = self.probe(forms, False)
        have, other = self.probe(forms, True)
        self.assertEqual(have, want)
        self.assertEqual(want[5], ["changed", False])
        self.assertEqual(want[15], ["denied", "provider-cooldown", 299.0])

        def normalize(name, row):
            if name == "requests":
                return row[:8] + ("<owner>" if row[8] in (owner, other) else row[8],) + row[9:]
            if name == "native_calls":
                return row[:2] + (0, "<start>") + row[4:]
            return row
        self.assert_same_state(normalize)

    # ---- faults ----

    def test_a_repeated_request_replays_and_a_changed_payload_is_refused(self):
        self.start()
        socket = self.server.socket
        detail = '{"x":1}'
        arguments = ("t1", "anthropic", 100, "fp", "managed", "godelclaw", LIVE, detail, 1000.0, "rq1.same")
        first = migrate_llm_ledger._call("llm-admit", arguments, socket)
        again = migrate_llm_ledger._call("llm-admit", arguments, socket)
        self.assertEqual(first, again)
        with self.assertRaises(input_ack.LedgerUnavailable):
            migrate_llm_ledger._call("llm-admit", arguments[:7] + ('{"x":2}',) + arguments[8:], socket)
        self.assertEqual(len(migrate_llm_ledger._call("llm-records", ("requests",), socket)), 1)

    def test_selection_never_falls_back_to_sqlite_once_the_ledger_is_native(self):
        seed(self.oracle, 50.0)
        socket = str(self.server.socket)
        with self.side(True, 1000.0):
            os.environ["METTACLAW_LLM_LEDGER_PATH"] = str(self.oracle)
            before = digest(self.oracle)
            ticket = llm_policy.admit("anthropic", 10)  # legacy: nothing migrated
            self.assertNotEqual(digest(self.oracle), before)
            with closing(sqlite3.connect(self.oracle)) as db:
                self.assertEqual(db.execute("SELECT status FROM requests WHERE id=?", (ticket,)).fetchone(), ("pending",))
            # an import in progress answers an Error, never legacy
            expect = migrate_llm_ledger.records(migrate_llm_ledger.read_source(self.oracle))[1]
            self.assertEqual(migrate_llm_ledger._call("llm-import", (S("begin"), "src", (), expect, 0.0, "rq1.i"), socket),
                             S("imported"))
            with self.assertRaises(input_ack.LedgerUnavailable):
                llm_policy.admit("anthropic", 10)
            # a discarded import is legacy again; the rows written meanwhile to SQLite stay there
            migrate_llm_ledger.discard(socket)
            self.assertEqual(tuple(migrate_llm_ledger._call("llm-census", (), socket)[1:]), (0,) * 9)
            second = llm_policy.admit("anthropic", 10)
            with closing(sqlite3.connect(self.oracle)) as db:
                self.assertEqual(db.execute("SELECT status FROM requests WHERE id=?", (second,)).fetchone(), ("pending",))
            with self.assertRaises(migrate_llm_ledger.Refused):
                migrate_llm_ledger.discard(socket)
            # a server that cannot be reached raises too
            os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = str(self.directory / "absent.sock")
            input_ack.close_ledger()
            with self.assertRaises(input_ack.LedgerUnavailable):
                llm_policy.admit("anthropic", 10)

    def test_rollback_returns_the_ledger_to_its_exported_sqlite_file(self):
        self.start([request_row("r1", status="completed", output=5, finish="stop")])
        with self.side(True, 1000.0):
            ticket = llm_policy.admit("anthropic", 10)
        exported = self.directory / "export.sqlite3"
        migrate_llm_ledger.export(self.server.socket, self.copy, exported)
        self.assertEqual(migrate_llm_ledger.verify(exported, self.server.socket), [])
        migrate_llm_ledger.rollback(self.server.socket, "fixture")
        self.assertEqual(migrate_llm_ledger._call("llm-selection", (), self.server.socket), S("legacy"))
        with self.side(True, 1001.0):
            os.environ["METTACLAW_LLM_LEDGER_PATH"] = str(exported)
            self.assertTrue(llm_policy.fail(ticket, sent=False))
        with closing(sqlite3.connect(exported)) as db:
            self.assertEqual(db.execute("SELECT status,output FROM requests WHERE id=?", (ticket,)).fetchone(), ("failed", 0))

    def test_a_failure_its_event_and_its_cooldown_survive_a_crash_together(self):
        self.start()
        socket = self.server.socket
        tickets = []
        with self.side(True, 1000.0):
            for _ in range(6):
                tickets.append(llm_policy.admit("anthropic", 10))
        for number, ticket in enumerate(tickets):
            failed = '{"http_status":429,"sent":true}'
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.path[:0] = %r; import input_ack, llm_policy\n"
                 "input_ack._ledger_call('llm-fail', (%r, llm_policy._argument(None), 'godelclaw', llm_policy._argument(None), "
                 "%r, %r, 'failed', llm_policy._argument(None), 0, 'turn', 1100.0, 'rq1.crash%d'), 30, %r)"
                 % ([str(HERE), str(ROOT / "src")], ticket, failed, 1400.0 + number, number, str(socket))],
                env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.children.append(child)
            time.sleep(0.02 * number)
            self.server.stop(crash=True)
            child.wait(timeout=60)
            self.server.start()
            input_ack.close_ledger()
        requests = {row[1]: row for row in migrate_llm_ledger._call("llm-records", ("requests",), socket)}
        events = {(row[2], row[3]) for row in migrate_llm_ledger._call("llm-records", ("events",), socket)}
        cooldowns = [row[2] for row in migrate_llm_ledger._call("llm-records", ("cooldowns",), socket)]
        committed = [number for number, ticket in enumerate(tickets) if requests[ticket][4] == "failed"]
        for ticket in tickets:
            self.assertEqual((ticket, "failed") in events, requests[ticket][4] == "failed")
        self.assertEqual(cooldowns, [max(1400.0 + number for number in committed)] if committed else [])

    def test_capacity_is_shared_by_the_agent_and_its_coding_sessions(self):
        self.start()
        program = ("import os, sys; sys.path[:0] = %r\n"
                   "from types import SimpleNamespace\nimport llm_policy, native_lifetime\n"
                   "ticket = llm_policy.admit('anthropic', 10)\n"
                   "plan = SimpleNamespace(correlation=ticket, epoch='e', activity=sys.argv[1], lane=sys.argv[2], "
                   "purpose='coding', guard=None, cap=1024, sent=False, soft=1e9)\n"
                   "print(native_lifetime.claim(plan), flush=True)\nsys.stdin.read()\n") % ([str(HERE), str(ROOT / "src")],)

        def claims(native):
            environment = dict(os.environ, METTACLAW_LLM_LEDGER_PATH=str(self.copy if native else self.oracle))
            environment.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
            if native:
                environment["METTACLAW_INPUT_LEDGER_SOCKET"] = str(self.server.socket)
            seen, alive = [], []
            # each claimant stays alive until all have claimed: a dead one's call would be recovered
            for activity, lane in (("work-1", "background"), ("work-2", "background"), ("agent-x", "interactive"),
                                   ("agent-y", "interactive")):
                child = subprocess.Popen([sys.executable, "-c", program, activity, lane], env=environment,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.children.append(child)
                alive.append(child)
                seen.append(child.stdout.readline().strip())
            for child in alive:
                out, err = child.communicate("", timeout=120)
                self.assertEqual(child.returncode, 0, err[-2000:])
            return seen

        self.assertEqual(claims(False), ["True", "False", "True", "False"])
        self.assertEqual(claims(True), ["True", "False", "True", "False"])


if __name__ == "__main__":
    unittest.main()
