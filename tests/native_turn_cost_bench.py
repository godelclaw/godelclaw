#!/usr/bin/env python3
"""What one agent turn costs against the native input ledger: calls and milliseconds per turn.

    CETTA_BIN=<cetta> python3 native_turn_cost_bench.py [--src <src dir>] [--turns N] [--turn-cache]

Runs a representative message turn N times against a native db server on a fresh temporary store, with the
real helper.py, observed_strategy.py, task_context.py and input_ack.py of --src (default: this repository's src;
point it at an export of an earlier tree to measure that tree). One turn is the sequence the loop makes:
strategy_host.begin's request writes (accept, link_inputs, task_context.begin), the model-facing readers
((requests), request-status), the reply that closes the message, the wait phase's reads of the working set
(wait_seconds, rest_due, rest_holding, rest_pending) and working_set_save with the turn's checkpoint. Every call
to the ledger worker is counted by verb and timed. Where the request verbs do not exist (an earlier tree) the
same Python methods run on SQLite and cost no native call.

--turn-cache measures what a per-turn memo of the working set read would save (it is a throwaway experiment, not
code of the product: see the report on why it is not shipped). Never touches any ledger but its own.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from collections import Counter
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, default=ROOT / "src")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--turn-cache", action="store_true")
    parser.add_argument("--prefill-processed", type=int, default=0, help="processed input ids already in the store")
    parser.add_argument("--prefill-requests", type=int, default=0, help="requests already in the store (a tree with request verbs)")
    arguments = parser.parse_args()
    sys.path[:0] = [str(HERE), str(arguments.src), str(ROOT / "channels")]
    for name in [name for name in os.environ if name.startswith("METTACLAW_")]:
        del os.environ[name]
    import input_ack  # first: the harness module below puts the repository's src in front of sys.path
    import helper
    import task_context
    import observed_strategy
    from execution_gateway.cetta_worker import CettaValueWorker
    from test_input_ledger_remote import DbServer
    assert Path(input_ack.__file__).resolve().parent == arguments.src.resolve(), input_ack.__file__
    directory = Path(tempfile.mkdtemp(prefix="il-bench-", dir="/tmp"))
    server = DbServer(directory)
    server.start()
    calls, spent = Counter(), Counter()
    original = CettaValueWorker.call

    def counting(worker, request):
        started = time.perf_counter()
        try:
            return original(worker, request)
        finally:
            calls[str(request[2])] += 1
            spent[str(request[2])] += time.perf_counter() - started

    cache = {}
    try:
        os.environ.update({"METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_STRATEGY_PATH": str(directory / "ledger.db"),
                           "METTACLAW_WORKING_SET_PATH": str(directory / "working.json"), "METTACLAW_INSTANCE": "bench",
                           "METTACLAW_INPUT_LEDGER_SOCKET": str(server.socket)})
        store = observed_strategy.Ledger(directory / "ledger.db")
        native_requests = hasattr(input_ack, "request_accept")
        from execution_gateway.cetta_worker import E, S
        for start in range(0, arguments.prefill_processed, 100):
            input_ack._ledger_call("mark-processed", (1.0, tuple("old-%d" % n for n in range(start, min(arguments.prefill_processed, start + 100)))))
        if native_requests and arguments.prefill_requests:
            atoms = []
            for n in range(arguments.prefill_requests):
                atoms += [E("request", "old:%d" % n, n + 1, "message", "operator", "addressed", S("none"), "", "old question", "d" * 64),
                          E("request-text", "old:%d" % n, "old question %d" % n), E("request-prev", "old:%d" % n, "old:%d" % (n - 1) if n else "")]
            atoms.append(E("request-tail", "tail", arguments.prefill_requests, "old:%d" % (arguments.prefill_requests - 1)))
            for start in range(0, len(atoms), 100):
                input_ack._ledger_call("import", (S("any"), tuple(atoms[start:start + 100])))
        if arguments.turn_cache:
            real_read = input_ack.working_set

            def memo():
                if "text" not in cache:
                    cache["text"] = real_read()
                return cache["text"]

            real_turn = input_ack.turn_checkpoint

            def write(*args, **kwargs):
                cache.clear()
                return real_turn(*args, **kwargs)
            patches = [mock.patch.object(input_ack, "working_set", memo), mock.patch.object(input_ack, "turn_checkpoint", write)]
        else:
            patches = []
        for patch in patches:
            patch.start()
        with mock.patch.object(CettaValueWorker, "call", counting):
            input_ack.committed(["warm"])  # starts the worker: not part of a turn
            calls.clear()
            spent.clear()
            started = time.perf_counter()
            for turn in range(arguments.turns):
                cache.clear()  # a turn begins
                key = "input:%d" % turn
                store.accept(key, "question number %d" % turn, kind="message")
                store.link_inputs(key, [str(turn)])
                task_context.begin(store, key, "operator-event", "input:%d" % (turn - 1) if turn else None, "chat")
                store.protected(key)                       # (requests)
                store.request_record(key)                  # (request-status "id")
                if native_requests:
                    store.status_of(key)                   # the focus check
                    store.native_request(key)              # the reply's look at the request
                    input_ack.request_answered(key)        # the reply closed the message (strategy_host._close_answered_message)
                helper.wait_seconds(5, time.time(), 60)     # the wait phase: two reads of the working set
                helper.rest_due(0)
                helper.rest_holding()
                helper.rest_pending()
                helper._turn_commit = ((str(turn),), key)
                helper.working_set_save(10, 1, "result %d" % turn, time.time())  # a read and the checkpoint
            elapsed = time.perf_counter() - started
        for patch in patches:
            patch.stop()
        total = sum(calls.values())
        print(json.dumps({"src": str(arguments.src), "turns": arguments.turns, "turn_cache_experiment": arguments.turn_cache,
                          "prefill": [arguments.prefill_processed, arguments.prefill_requests],
                          "native_request_verbs": native_requests, "calls_per_turn": total / arguments.turns,
                          "ms_per_turn": 1000 * elapsed / arguments.turns,
                          "by_verb": {verb: [calls[verb] / arguments.turns, round(1000 * spent[verb] / calls[verb], 1)]
                                      for verb in sorted(calls)}}, indent=1))
    finally:
        input_ack.close_ledger()
        server.stop()
        shutil.rmtree(directory, True)


if __name__ == "__main__":
    main()
