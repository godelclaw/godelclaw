"""The turns and their actions in the native ledger (METTACLAW_INPUT_LEDGER_SOCKET).

observed_strategy.Ledger's turn and effect methods (control, restore, admission_denial, capture,
decision_pending, decide, report, observe, attest, finish, finish_exact_results,
recover_recorded_actions, protected, projection) and strategy_host's command attempts keep their
records in the same native collection as the requests. Each scenario runs on the native ledger and
asserts its return values, the protected view and the final projection (meta keys, requests,
actions); the ledger's owner is replaced by one name in the recorded steps.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
import native_ledger
import llm_policy
from observed_strategy import Ledger, digest, encoded
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import available, oracle_environment

ROUTER = dict(advertised=["tool"], executable=["tool"], permitted=["tool"])


def owner_of(store):
    return store.projection()["meta"]["owner"]


def normalized(value, owner):
    """The value with this ledger's owner replaced by one name (tickets are <owner>:<serial>)."""
    return json.loads(json.dumps(value, sort_keys=True, default=str).replace(owner, "OWNER"))


def presented(store, ticket):
    """What a turn presents of a result: the protected view's projection of its receipt."""
    return {ticket: [r for r in store.protected("none")["results"] if r["ticket"] == ticket]}


def record_attempt(store, ticket, position, command, result):
    """strategy_host's reserve_command and command_result for one command."""
    command_text, result_text = encoded(command), encoded(result)
    input_ack.attempt_begin(ticket, position, command_text)
    if result is not None:
        input_ack.attempt_result(ticket, position, command_text, result_text)


def final(store):
    projection = store.projection()
    meta = {key: projection["meta"].get(key) for key in
            ("owner", "revision", "mode", "paused", "next_sample", "pending", "router_revision")}
    return dict(meta=meta, requests=projection["requests"], actions=projection["actions"],
                protected=store.protected("task-1"))


def lifecycle(store, note):
    """Every turn and effect method once, with the refusals and repeats each one has."""
    note("accept", [store.accept("task-1", "do the thing"),
                    store.accept("verified", "exact", "sha256:" + digest("exact output")),
                    store.accept("input:1", "a message", kind="message")])
    store.control(mode="research")
    store.control(mode="research")
    store.control(paused=True)
    note("paused", [store.capture("task-1", "boundary", ROUTER, 0), store.admission_denial("task-1", 0)])
    store.control(paused=False)
    store.control(router_revision={"r": 3})
    store.control(router_revision={"r": 3})
    note("meta-1", final(store)["meta"])
    snap = store.capture("task-1", "boundary", ROUTER, 0, guard="g")
    note("capture", snap)
    serial = snap["serial"]
    note("pending", [store.decision_pending(serial), store.decision_pending(serial + 1),
                     store.decide(serial + 1, {"kind": "command", "tool": "shell", "command": "ls"}, 0)])
    ticket = store.decide(serial, {"kind": "command", "tool": "shell", "command": {"argv": ["ls", "-l"]}}, 0)
    note("decide", [ticket, store.decide(serial, {"kind": "text"}, 0), store.decision_pending(serial)])
    note("report", [store.report(ticket, "out é\n" * 3), store.report(ticket, "again"),
                    store.report("nobody:1", "x")])
    note("protected-before-observe", store.protected("task-1"))
    store.observe([ticket], {ticket: [{"ticket": ticket, "payload_digest": "wrong"}]})
    note("unobserved", final(store)["actions"])
    store.observe([ticket], presented(store, ticket))
    note("observed", final(store)["actions"])
    note("attest", [store.attest("task-1", ticket, lambda work, receipt: False),
                    store.attest("input:1", ticket, lambda work, receipt: True),
                    store.attest("task-1", ticket, lambda work, receipt: work["content"] == "do the thing"
                                 and receipt["payload"].startswith("out"))])
    note("finish", [store.finish("task-1", ticket, {}),
                    store.finish("task-1", ticket, {"operator": lambda work, receipt: True}),
                    store.finish("task-1", ticket, {"operator": lambda work, receipt: True})])
    text_turn = store.capture("verified", "b2", ROUTER, 0)
    note("text", store.decide(text_turn["serial"], {"kind": "text"}, 0))
    exact_turn = store.capture("verified", "b3", ROUTER, 0)
    exact = store.decide(exact_turn["serial"], {"kind": "command", "tool": "shell", "command": "cat"}, 0)
    store.report(exact, "exact output")
    store.observe([exact], presented(store, exact))
    note("exact", [store.finish_exact_results(), store.status_of("verified")])
    interrupted = store.capture("input:1", "b4", ROUTER, 0)
    store.restore()
    note("restored", [store.decision_pending(interrupted["serial"]),
                      store.decide(interrupted["serial"], {"kind": "text"}, 0)])
    batch_turn = store.capture("input:1", "b5", ROUTER, 0)
    commands = [{"argv": ["one"]}, {"argv": ["two"]}, {"argv": ["three"]}]
    batch = store.decide(batch_turn["serial"], {"kind": "command", "tool": "batch",
                                                "command": {"commands": commands, "limit": 2}}, 0)
    half_turn = store.capture("input:1", "b6", ROUTER, 0)
    half = store.decide(half_turn["serial"], {"kind": "command", "tool": "batch",
                                              "command": {"commands": commands[:1], "limit": 1}}, 0)
    record_attempt(store, batch, 0, commands[0], {"disposition": "ok", "result": "1"})
    record_attempt(store, batch, 1, commands[1], {"disposition": "ok", "result": "2"})
    record_attempt(store, half, 0, commands[0], None)
    store.restore()
    note("recovered", [store.recover_recorded_actions(), store.recover_recorded_actions()])
    store.compact(3)
    note("final", final(store))



def presentations(store, note):
    """An answered prompt marks an action observed only when it carried every attempt's result and the
    receipt exists (lean/pettaclaw/ObservedReceipts.lean); the protected view then moves on."""
    store.accept("r1", "work one")
    tickets = []
    for number in range(4):
        snap = store.capture("r1", "b%d" % number, ROUTER, 0)
        commands = [["shell", "ls %d" % number], ["shell", "cat %d" % number]]
        tickets.append(store.decide(snap["serial"], {"kind": "command", "tool": "batch",
                                                     "command": {"commands": commands, "limit": 2}}, 0))
    one, two, three, four = tickets
    for ticket in (one, two, three):
        record_attempt(store, ticket, 0, ["shell", "ls"], {"disposition": "returned", "result": "a"})
    for ticket in (one, two):
        record_attempt(store, ticket, 1, ["shell", "cat"], {"disposition": "returned", "result": "b"})
    record_attempt(store, three, 1, ["shell", "cat"], None)          # interrupted: no result
    record_attempt(store, four, 0, ["shell", "ls"], {"disposition": "returned", "result": "c"})
    for ticket in (one, two, three):
        store.report(ticket, "batch " + ticket)                              # four has no receipt
    note("view-before", [r["ticket"] for r in store.protected("r1")["results"]])
    note("partial", store.observe_presented([(one, 0)]))                     # one of two calls carried
    note("none", store.observe_presented([]))
    note("whole", store.observe_presented([(one, 0), (one, 1), ("elsewhere:9", 0)]))
    note("again", store.observe_presented([(one, 0), (one, 1)]))             # already observed
    note("interrupted", store.observe_presented([(three, 0), (three, 1)]))
    note("no-receipt", store.observe_presented([(four, 0)]))
    note("view-between", [r["ticket"] for r in store.protected("r1")["results"]])
    note("second", store.observe_presented([(two, 1), (two, 0), (three, 0)]))
    note("view-after", [r["ticket"] for r in store.protected("r1")["results"]])
    note("observed", {a["ticket"]: a["observed"] for a in store.projection()["actions"]})


def topics(store, note):
    """topic_jobs on either ledger: the implicit conversation topic, select, notes, the context and the
    historical model jobs (none)."""
    import topic_jobs
    def attempt(function, *arguments):
        try:
            return ("returned", function(*arguments))
        except ValueError as error:
            return ("raised", str(error))
    note("initial", [topic_jobs.topics(), topic_jobs.context(), topic_jobs.foreground_revision(),
                     topic_jobs.status(), topic_jobs.results("nobody")])
    note("select", attempt(topic_jobs.select, "research"))
    note("note", attempt(topic_jobs.note, "first notes é \"quoted\""))
    note("again", attempt(topic_jobs.note, "second notes"))
    note("after", [topic_jobs.topics(), topic_jobs.context(), topic_jobs.foreground_revision()])
    note("back", attempt(topic_jobs.select, "conversation"))
    note("quiet", [topic_jobs.context(), topic_jobs.foreground_revision()])
    note("reselect", attempt(topic_jobs.select, "research"))
    note("kept", [topic_jobs.topics(), topic_jobs.context()])
    note("bad-name", attempt(topic_jobs.select, "bad name!"))


class NativeSides(NativeCase):
    """A scenario (a function of a Ledger and a note function) on the native ledger: its noted steps,
    with the ledger's owner replaced by one name."""
    def steps(self, scenario):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            store._owner = None
            steps = []
            scenario(store, lambda name, value: steps.append((name, value)))
            outcome = normalized(steps, owner_of(store))
            input_ack.close_ledger()
        return outcome


@unittest.skipUnless(available(), SKIP)
class TurnEffectNativeTest(NativeSides):
    def test_every_turn_and_effect_method(self):
        steps = dict(self.steps(lifecycle))
        self.assertEqual(steps["finish"], [False, True, False])
        self.assertEqual(steps["recovered"], [1, 0])
        self.assertEqual(steps["final"]["meta"]["mode"], "research")


    def test_an_answered_prompt_marks_what_it_carried(self):
        steps = dict(self.steps(presentations))
        one, two = [t for t in steps["view-before"]]
        self.assertEqual([steps[k] for k in ("partial", "none", "whole", "again", "interrupted", "no-receipt", "second")],
                         [0, 0, 1, 0, 0, 0, 1])
        self.assertEqual(steps["view-between"][0], two)        # the marked receipt left the waiting list
        self.assertNotIn(one, steps["view-after"] + steps["view-between"])
        observed = steps["observed"]
        self.assertEqual(sorted(t for t, v in observed.items() if v), sorted([one, two]))

    def test_topics(self):
        steps = dict(self.steps(topics))
        self.assertIn('"active":"conversation"', steps["initial"][0])
        self.assertEqual(steps["after"][2], ["research", 2])
        self.assertEqual(steps["quiet"], ["", ["conversation", 0]])
        self.assertIn("second notes", steps["kept"][1])
        self.assertEqual(steps["bad-name"][0], "raised")

    def test_the_receipts_page_and_the_reservations(self):
        import strategy_host as host
        big = {"disposition": "returned", "result": "\U0001f9ea \"quoted\"\\n" * 3000}

        def scenario(store, note):
            with mock.patch.multiple(host, _ledger=store, _turn="1", _ticket="t", _position=0), \
                    mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1"}):
                with mock.patch.object(host, "_close_answered_message"), \
                        mock.patch.dict(sys.modules, {"process_supervision": mock.Mock(), "stream_sink": mock.Mock()}):
                    for number, command in enumerate((["help"], ["shell", "ls"], ["cat", "x"])):
                        note("reserve", host.reserve_command("1", command))
                        if number != 1:
                            host.command_result("1", "returned", command, big["result"] if number == 0 else "small")
                    host._position = 0
                    try:
                        host.reserve_command("1", ["help"])
                        note("repeat", "accepted")
                    except llm_policy.IntegrityError as error:
                        note("repeat", str(error))
                cursor, pages = "", []
                while True:
                    page = json.loads(host.command_receipts(cursor))
                    pages.append(page)
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                note("pages", pages)
                note("recent", json.loads(host.command_receipts("2:0")))
        steps = self.steps(scenario)
        self.assertEqual([name for name, _ in steps][:3], ["reserve"] * 3)
        self.assertNotEqual(dict(steps)["repeat"], "accepted", "a position is reserved once")
        pages = dict(steps)["pages"]
        self.assertGreater(len(pages), 3, "the large result is sliced over several pages")

    def test_the_clock_orders_turns_among_requests(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            store.accept("a", "first")
            first = store.capture("a", "b", ROUTER, 0)
            store.accept("b", "second")
            second = store.capture("b", "b", ROUTER, 0)
            _, turns, arrivals = native_ledger.turn_dump()
            positions = {turn["serial"]: turn["position"] for turn in turns}
            self.assertLess(arrivals["a"], positions[first["serial"]])
            self.assertLess(positions[first["serial"]], arrivals["b"])
            self.assertLess(arrivals["b"], positions[second["serial"]])
            input_ack.close_ledger()



if __name__ == "__main__":
    unittest.main()
