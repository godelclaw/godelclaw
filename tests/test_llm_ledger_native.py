"""The LLM call ledger in the native store (src/llm_ledger.metta), through its Python and MeTTa callers.

Each scenario runs on a fresh native store (METTACLAW_INPUT_LEDGER_SOCKET) with a fixed clock and fixed ids,
and its expectations are asserted; the store's derived records (the newest-first turn list, the history
counter) must follow its calls and events. The Python callers (llm_policy, native_lifetime: the host's path
and the Python transport) and the MeTTa callers (llm_policy_cetta, native_lifetime_cetta: the native
transport) are both run.

Faults: a repeated request id replays its answer and a changed payload under it is refused; a failure's
request, event and cooldown survive a server crash together or not at all; capacity is shared by processes
(the agent and its coding sessions).
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import input_ack
import llm_policy
import native_lifetime
import native_ledger
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


def ledger(verb, arguments, socket, timeout=600):
    """One ledger verb at `socket`, its answer with true/false/none as Python values."""
    return llm_policy.plain(input_ack._ledger_call(verb, tuple(llm_policy._argument(a) for a in arguments),
                                                   timeout, socket))


def outcome(function, *arguments, **options):
    try:
        return ("returned", function(*arguments, **options))
    except Exception as error:  # the type and message are the observation
        return ("raised", type(error).__name__, str(error)[:200])



def plan(identity, epoch, *, activity="agent-a", lane="interactive", purpose="turn", sent=False):
    return SimpleNamespace(correlation=identity, epoch=epoch, activity=activity, lane=lane, purpose=purpose,
                           guard="revision", cap=1 << 20, sent=sent, soft=float("inf"))


@unittest.skipUnless(available(), SKIP)
class LlmLedgerNative(NativeCase):
    maxDiff = None
    def setUp(self):
        super().setUp()
        self.env = {"METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
                    "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw", "METTACLAW_INSTANCE": "fixture",
                    "METTACLAW_ACTIVITY_CALL_CAPACITY": "2", "METTACLAW_ACTIVITY_INTERACTIVE_RESERVE": "1"}
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)

    def start(self, dead=()):
        """The case's fresh store, with a pending request of a dead owner for each id in `dead`."""
        for identity in dead:
            ledger("llm-admit", (identity, "anthropic", 100, "fp", "managed", "godelclaw", DEAD_OWNER,
                                 '{"provider":"anthropic"}', 100.0, "rq1.seed-" + identity), self.server.socket)
        input_ack.close_ledger()

    @contextmanager
    def side(self, now):
        clock = SimpleNamespace(now=now)
        with mock.patch.dict(os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.server.socket)}), \
                mock.patch("time.time", lambda: clock.now), mock.patch.object(llm_policy, "uuid", Ids()):
            yield clock

    def run_scenario(self, scenario, now=1000.0):
        """The scenario's observations; then the derived records are checked."""
        with self.side(now) as clock:
            answers = scenario(clock)
        self.assert_derived_records()
        return answers

    def assert_derived_records(self):
        """The newest-first turn list and the history counter follow the calls and the events."""
        socket = self.server.socket
        records = {kind: ledger("llm-records", (kind,), socket) for kind in ("recent", "states")}
        created = {row["id"]: row["created"] for row in native_ledger.llm_records("calls", socket) if row["purpose"] == "turn"}
        recent = {row[1]: (row[2], row[3]) for row in records["recent"]}
        states = {row[1]: row[2] for row in records["states"]}
        walk, node = [], states.get("recent-turn")
        while node is not None:
            walk.append(node)
            node = recent[node][0]
        self.assertEqual(sorted(walk), sorted(created))
        self.assertEqual([recent[identity][1] for identity in walk], [created[identity] for identity in walk])
        self.assertEqual([created[identity] for identity in walk], sorted(created.values(), reverse=True))
        events = native_ledger.llm_records("events", socket)
        if events:
            self.assertEqual(states["event-tail"], max(row["sequence"] for row in events))

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

        seen = self.run_scenario(scenario)
        self.assertEqual(seen[8][:2], ("raised", "AdmissionDenied"))

    # ---- the host's path: claim, submit, progress, settle ----

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

        seen = self.run_scenario(scenario)
        self.assertEqual([answer for answer in seen if answer[0] == "raised"][0][1], "ValueError")

    # ---- the MeTTa callers (the native transport, path A) ----

    def probe(self, forms):
        entry = self.directory / "probe.metta"
        output = self.directory / "probe.jsonl"
        output.unlink(missing_ok=True)
        lines = ['!(import! &self ' + json.dumps(str(ROOT / "src" / "native_lifetime_cetta.metta")) + ')',
                 '(= (probe:result $value) (case $value '
                 '(((LedgerAdmitted $id) (JsonArray ("admitted" $id))) '
                 ' ((LedgerDenied $code $delay) (JsonArray ("denied" $code $delay))) '
                 ' ((LedgerChanged $changed) (JsonArray ("changed" $changed))) '
                 ' ((LedgerCompletion $state) (JsonArray ("completion" (repr $state)))) '
                 ' ((LedgerError (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
                 ' ((LedgerError $error) (JsonArray ("error"))) '
                 ' ((LedgerInvalid (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
                 ' ($other (JsonArray ("unexpected" (repr $other)))))))',
                 '(= (probe:emit $value) (fs:append-text ' + json.dumps(str(output)) +
                 ' (str:concat (json:stringify $value) "\\n")))']
        lines += ['!(probe:emit (probe:result %s))' % form for form in forms]
        lines.append('!(probe:emit (lp:owner))')
        entry.write_text("\n".join(lines) + "\n")
        environment = dict(os.environ, METTACLAW_INPUT_LEDGER_SOCKET=str(self.server.socket))
        run = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet", str(entry)],
                             cwd=ROOT, env=environment, capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(len(rows), len(forms) + 1, run.stdout[-2000:] + run.stderr[-2000:])
        return rows[:-1], rows[-1]

    def test_metta_callers_answers_and_records(self):
        self.start(dead=["p-dead"])

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
        got, _ = self.probe(forms)
        self.assertEqual(got[5], ["changed", False])
        self.assertEqual(got[15], ["denied", "provider-cooldown", 299.0])
        self.assert_derived_records()

    # ---- faults ----

    def test_a_repeated_request_replays_and_a_changed_payload_is_refused(self):
        self.start()
        socket = self.server.socket
        detail = '{"x":1}'
        arguments = ("t1", "anthropic", 100, "fp", "managed", "godelclaw", LIVE, detail, 1000.0, "rq1.same")
        first = ledger("llm-admit", arguments, socket)
        again = ledger("llm-admit", arguments, socket)
        self.assertEqual(first, again)
        with self.assertRaises(input_ack.LedgerUnavailable):
            ledger("llm-admit", arguments[:7] + ('{"x":2}',) + arguments[8:], socket)
        self.assertEqual(len(ledger("llm-records", ("requests",), socket)), 1)

    def test_a_failure_its_event_and_its_cooldown_survive_a_crash_together(self):
        self.start()
        socket = self.server.socket
        tickets = []
        with self.side(1000.0):
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
        requests = {row[1]: row for row in ledger("llm-records", ("requests",), socket)}
        events = {(row[2], row[3]) for row in ledger("llm-records", ("events",), socket)}
        cooldowns = [row[2] for row in ledger("llm-records", ("cooldowns",), socket)]
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

        def claims():
            environment = dict(os.environ, METTACLAW_INPUT_LEDGER_SOCKET=str(self.server.socket))
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

        self.assertEqual(claims(), ["True", "False", "True", "False"])


if __name__ == "__main__":
    unittest.main()
