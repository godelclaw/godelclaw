"""One client process of the native request tests: observed_strategy.Ledger on the native path.

    python native_request_client.py <spec file>

The spec is a Python literal: {"socket", "go", "name", "shared"}. The process waits for the file `go` (so that two
clients start together), then accepts its own requests and the shared ones, settles and addresses some of
them, and prints one JSON line per answer: [operation, key, answer]. The environment is set by this script from
the spec only; the caller starts it with a clean environment.
"""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src"), str(HERE.parent / "channels")]
import input_ack
from observed_strategy import IdentityConflict, Ledger


def main():
    spec = ast.literal_eval(Path(sys.argv[1]).read_text())
    directory = Path(tempfile.mkdtemp(prefix="il-client-", dir="/tmp"))
    os.environ.update({"METTACLAW_STRATEGY_PATH": str(directory / "gate.db"), "METTACLAW_OBSERVED_STRATEGY": "1",
                       "METTACLAW_INSTANCE": "default", input_ack.LEDGER_SOCKET: spec["socket"]})
    try:
        run(spec, directory)
    finally:
        shutil_rmtree(directory)


def run(spec, directory):
    store = Ledger(directory / "gate.db")
    deadline = time.monotonic() + 120
    while not Path(spec["go"]).exists():
        if time.monotonic() > deadline:
            sys.exit("timed out waiting for go")
        time.sleep(0.005)
    name = spec["name"]

    busy = [0]

    def say(operation, key, answer):
        print(json.dumps([operation, key, answer]), flush=True)

    def retry(function, *arguments, **options):
        """What a caller does with a locked database: try again later. LedgerBusy means nothing was applied
        (the write lost 32 races in a row to the other process); the repeat is a new call."""
        for _ in range(400):
            try:
                return function(*arguments, **options)
            except input_ack.LedgerBusy:
                busy[0] += 1
                time.sleep(0.005)
        raise RuntimeError("busy for 400 tries")

    for number in range(spec["own"]):
        key = "%s-%d" % (name, number)
        say("accept", key, retry(store.accept, key, "own %s %d \u00e9\u65e5" % (name, number), kind="message"))
        if number % 3 == 0:
            say("processed", key, retry(store.processed, key))
        if number % 4 == 0:
            say("disposition", key, [retry(store.disposition, key, "defer", "by " + name), store.refusal])
    for number in range(spec["shared"]):
        key = "shared-%d" % number
        say("accept", key, retry(store.accept, key, "shared %d" % number, kind="message"))
        say("processed", key, retry(store.processed, key))
        say("disposition", key, [retry(store.disposition, key, "addressed", "by " + name), store.refusal])
    say("done", name, busy[0])


def shutil_rmtree(directory):
    import shutil
    input_ack.close_ledger()
    shutil.rmtree(directory, True)


if __name__ == "__main__":
    main()
