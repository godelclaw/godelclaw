"""The turns and their actions in the native ledger (METTACLAW_INPUT_LEDGER_SOCKET).

observed_strategy.Ledger's turn and effect methods (control, restore, admission_denial, capture,
decision_pending, decide, report, observe, attest, finish, finish_exact_results,
recover_recorded_actions, protected, projection) and strategy_host's command attempts keep their
records in the same native collection as the requests. The oracle is the same sequence of calls on
the SQLite path: every return value, the protected view after every step and the final projection
(meta keys, requests, actions) are compared. The two ledgers have different owners, so the owner is
replaced by one name before comparing. A seeded generator adds random sequences of the same calls.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
from pathlib import Path
import random
import sqlite3
import sys
import unittest
from unittest import mock
import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from observed_strategy import Ledger, digest, encoded
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import Oracle, available, oracle_environment

ROUTER = dict(advertised=["tool"], executable=["tool"], permitted=["tool"])


def owner_of(store):
    return store.projection()["meta"]["owner"]


def normalized(value, owner):
    """The value with this ledger's owner replaced by one name (tickets are <owner>:<serial>)."""
    return json.loads(json.dumps(value, sort_keys=True, default=str).replace(owner, "OWNER"))


def presented(store, ticket):
    """What a turn presents of a result: the protected view's projection of its receipt."""
    return {ticket: [r for r in store.protected("none")["results"] if r["ticket"] == ticket]}


def record_attempt(store, native, ticket, position, command, result):
    """strategy_host's reserve_command and command_result for one command, on either path."""
    command_text, result_text = encoded(command), encoded(result)
    if native:
        input_ack.attempt_begin(ticket, position, command_text)
        if result is not None:
            input_ack.attempt_result(ticket, position, command_text, result_text)
        return
    with store.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT, position INTEGER, command TEXT, "
                   "result TEXT, PRIMARY KEY(ticket,position))")
        db.execute("INSERT INTO command_attempts VALUES (?,?,?,NULL)", (ticket, position, command_text))
        if result is not None:
            db.execute("UPDATE command_attempts SET result=? WHERE ticket=? AND position=? AND command=? "
                       "AND result IS NULL", (result_text, ticket, position, command_text))


def final(store):
    projection = store.projection()
    meta = {key: projection["meta"].get(key) for key in
            ("owner", "revision", "mode", "paused", "next_sample", "pending", "router_revision")}
    return dict(meta=meta, requests=projection["requests"], actions=projection["actions"],
                protected=store.protected("task-1"))


def lifecycle(store, native, note):
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
    record_attempt(store, native, batch, 0, commands[0], {"disposition": "ok", "result": "1"})
    record_attempt(store, native, batch, 1, commands[1], {"disposition": "ok", "result": "2"})
    record_attempt(store, native, half, 0, commands[0], None)
    store.restore()
    note("recovered", [store.recover_recorded_actions(), store.recover_recorded_actions()])
    store.compact(3)
    note("final", final(store))


def generated(seed):
    """A random sequence of the turn and effect calls, seeded."""
    def scenario(store, native, note):
        rng = random.Random(seed)
        requests, tickets, serials = [], [], []
        for step in range(36):
            choice = rng.randrange(11)
            if choice == 0 or not requests:
                key = "r%d" % len(requests)
                requests.append(key)
                note(step, ("accept", store.accept(key, "work %d" % len(requests))))
            elif choice == 1:
                snap = store.capture(rng.choice(requests), "b%d" % step, ROUTER, 0)
                if snap:
                    serials.append(snap["serial"])
                note(step, ("capture", snap))
            elif choice in (2, 3) and serials:
                serial = rng.choice(serials + [serials[-1]] * 3)
                kind = rng.choice(["text", "command", "command"])
                response = {"kind": kind, "tool": "shell", "command": {"n": step}} if kind == "command" else {"kind": kind}
                answer = store.decide(serial, response, 0)
                if kind == "command" and answer != "ignored":
                    tickets.append(answer)
                note(step, ("decide", answer))
            elif choice == 4 and tickets:
                ticket = rng.choice(tickets + ["nobody:%d" % step])
                note(step, ("report", store.report(ticket, "payload %d" % rng.randrange(3),
                                                   domain=rng.choice(["unknown", "success"]))))
            elif choice == 5 and tickets:
                ticket = rng.choice(tickets)
                shown = presented(store, ticket) if rng.random() < 0.7 else {ticket: [{"ticket": ticket, "payload_digest": "x"}]}
                store.observe([ticket], shown)
                note(step, ("observe", final(store)["actions"]))
            elif choice == 6 and tickets:
                ticket, request = rng.choice(tickets), rng.choice(requests)
                note(step, ("attest", store.attest(request, ticket, lambda work, receipt: rng.random() < 0.8)))
            elif choice == 7 and tickets:
                ticket, request = rng.choice(tickets), rng.choice(requests)
                note(step, ("finish", store.finish(request, ticket, {"operator": lambda work, receipt: True})))
            elif choice == 8:
                store.control(mode=rng.choice([None, "a", "b"]), paused=rng.choice([None, False, False, True]),
                              router_revision=rng.choice([None, 1, 2]))
                note(step, ("control", final(store)["meta"]))
            elif choice == 9:
                store.restore()
                note(step, ("restore", final(store)["meta"]))
            elif choice == 10 and requests:
                note(step, ("cancel", store.cancel(rng.choice(requests), operator_authorized=True)))
            note(step, ("protected", store.protected(rng.choice(requests) if requests else "none")))
        note("final", final(store))
    return scenario


@unittest.skipUnless(available(), SKIP)
class TurnEffectNativeTest(NativeCase):
    def run_both(self, scenario):
        outcomes = {}
        for native in (False, True):
            with oracle_environment() as directory:
                runner = NativeRunner.create(directory, self.server.socket) if native else Oracle.create(directory)
                store = Ledger(runner.path)
                steps = []
                scenario(store, native, lambda name, value: steps.append((name, value)))
                outcomes[native] = normalized(steps, owner_of(store))
                if native:
                    # Nothing of the turns or actions went to the SQLite file.
                    with sqlite3.connect(runner.path) as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 0)
                        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone())
                input_ack.close_ledger()
        return outcomes[False], outcomes[True]

    def compare(self, scenario):
        oracle, native = self.run_both(scenario)
        self.assertEqual(len(native), len(oracle))
        for number, (want, got) in enumerate(zip(oracle, native)):
            self.assertEqual(got, want, (number, want[0]))
        return oracle, native

    def test_every_turn_and_effect_method_matches_sqlite(self):
        oracle, _ = self.compare(lifecycle)
        steps = dict(oracle)
        self.assertEqual(steps["finish"], [False, True, False])
        self.assertEqual(steps["recovered"], [1, 0])
        self.assertEqual(steps["final"]["meta"]["mode"], "research")


    def test_the_receipts_page_and_the_reservations_match_sqlite(self):
        import strategy_host as host
        big = {"disposition": "returned", "result": "\U0001f9ea \"quoted\"\\n" * 3000}

        def scenario(store, native, note):
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
                    except sqlite3.IntegrityError as error:
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
        oracle, native = self.compare(scenario)
        self.assertEqual([name for name, _ in native][:3], ["reserve"] * 3)
        pages = dict(oracle)["pages"]
        self.assertGreater(len(pages), 3, "the large result is sliced over several pages")

    def test_the_clock_orders_turns_among_requests(self):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket)
            store = Ledger(runner.path)
            store.accept("a", "first")
            first = store.capture("a", "b", ROUTER, 0)
            store.accept("b", "second")
            second = store.capture("b", "b", ROUTER, 0)
            _, turns, arrivals = input_ack.turn_dump()
            positions = {turn["serial"]: turn["position"] for turn in turns}
            self.assertLess(arrivals["a"], positions[first["serial"]])
            self.assertLess(positions[first["serial"]], arrivals["b"])
            self.assertLess(arrivals["b"], positions[second["serial"]])
            input_ack.close_ledger()


def random_case(seed):
    def test(self):
        self.compare(generated(seed))
    test.__doc__ = "A seeded random sequence of turn and effect calls matches SQLite (seed %d)." % seed
    return test


for _seed in range(6):  # one test each, so that each sequence starts on a fresh server and store
    setattr(TurnEffectNativeTest, "test_random_sequence_%d_matches_sqlite" % _seed, random_case(_seed))


if __name__ == "__main__":
    unittest.main()
