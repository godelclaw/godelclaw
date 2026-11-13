"""A native ledger server for one test: the store every agent uses, isolated per test.

    socket = native_ledger.start(self, directory)

starts src/input_ledger_server.metta on a fresh store under `directory`/ledger, exports
METTACLAW_INPUT_LEDGER_SOCKET for in-process callers (input_ack names the socket on every
call, so nothing else needs resetting), and returns the socket path for subprocess
environments. The server stops and the variable is restored when the test ends.
"""
import itertools
import os
from pathlib import Path

from test_input_ledger_remote import DbServer

VARIABLE = "METTACLAW_INPUT_LEDGER_SOCKET"
_proxies = itertools.count(1)


def start(testcase, directory):
    root = Path(directory) / "ledger"
    root.mkdir(parents=True, exist_ok=True)
    server = DbServer(root)
    server.start()
    testcase.addCleanup(server.stop)
    previous = os.environ.get(VARIABLE)
    os.environ[VARIABLE] = str(server.socket)

    def restore():
        if previous is None:
            os.environ.pop(VARIABLE, None)
        else:
            os.environ[VARIABLE] = previous
    testcase.addCleanup(restore)
    testcase.ledger_server = server
    return str(server.socket)


def faulty(testcase, directory, upstream, marker=None, armed=None, lose="request", once=False, count=None):
    """A socket in front of the server at `upstream` that injects one storage fault per matching
    connection: one whose request carries `marker` (bytes; None matches every request) while the
    file `armed` exists (always, when armed is None).
      lose="request": the connection closes before the request reaches the server; nothing is
                      written, and the caller sees the ledger unreachable.
      lose="reply":   the request reaches the server and commits; its reply is dropped, so the
                      caller cannot tell whether the write landed.
    `once` disarms after the first fault (deleting `armed`); `count` after that many (a client tries a
    lost call three times before it reports the failure). Every other connection is relayed unchanged.
    Returns (socket path, list of faults injected)."""
    import socket as sockets
    import threading
    path = str(Path(directory) / ("faulty-%s-%d.sock" % (lose, next(_proxies))))
    listener = sockets.socket(sockets.AF_UNIX)
    listener.bind(path)
    listener.listen(16)
    faults, live = [], [True]

    def close(*ends):
        for end in ends:
            try:
                end.shutdown(sockets.SHUT_RDWR)
            except OSError:
                pass
            end.close()

    def armed_now():
        return live[0] and (armed is None or Path(armed).exists())

    def disarm():
        if once or (count is not None and len(faults) >= count):
            live[0] = False
            if armed is not None:
                Path(armed).unlink(missing_ok=True)

    def relay(client):
        server = sockets.socket(sockets.AF_UNIX)
        try:
            server.connect(str(upstream))
        except OSError:
            close(client, server)
            return
        lost = [False]

        def back():
            try:
                while data := server.recv(65536):
                    if lost[0]:
                        break
                    client.sendall(data)
            except OSError:
                pass
            close(client, server)
        threading.Thread(target=back, daemon=True).start()
        tail = b""
        try:
            while data := client.recv(65536):
                if not lost[0] and (marker is None or marker in tail + data) and armed_now():
                    faults.append(lose)
                    disarm()
                    if lose == "request":
                        break
                    lost[0] = True
                server.sendall(data)
                if marker:
                    tail = (tail + data)[-(len(marker) - 1):] if len(marker) > 1 else b""
        except OSError:
            pass
        if not lost[0]:
            close(client, server)

    def accept():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=relay, args=(client,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    testcase.addCleanup(close, listener)
    return path, faults


# The LLM ledger's records with the field names of the tables they replaced, so a test
# asserts the same facts: requests, cooldowns, calls (native_calls), results, events
# (llm_call_history).
FIELDS = {
    "requests": ("id", "started", "reserved", "status", "output", "provider", "mode", "finish",
                 "owner", "fingerprint"),
    "cooldowns": ("provider", "until"),
    "calls": ("id", "epoch", "pid", "start", "created", "updated", "state", "guard", "transport",
              "soft", "bytes", "quiet", "reason", "activity", "lane", "purpose", "notified"),
    "results": ("id", "result"),
    "events": ("sequence", "call_id", "event", "observed", "detail"),
}


def _text(value):
    name = getattr(value, "name", None)
    return name if name is not None else value


def llm_records(kind, socket=None):
    """Every LLM ledger record of `kind` as a dict; none is None, symbols are their names."""
    import input_ack
    import llm_policy
    answer = llm_policy.plain(input_ack._ledger_call("llm-records", (kind,), 30, socket))
    rows = []
    for record in answer:
        values = [_text(item) for item in record[1:]]
        rows.append(dict(zip(FIELDS[kind], values)))
    if kind == "calls":
        results = {row["id"]: row["result"] for row in llm_records("results", socket)}
        for row in rows:
            row["result"] = results.get(row["id"])
    return rows


def llm_call(call_id, epoch=None, socket=None):
    """The native_calls row of one call (and epoch), or None."""
    for row in llm_records("calls", socket):
        if row["id"] == call_id and (epoch is None or row["epoch"] == epoch):
            return row
    return None


def llm_events(call_id=None, socket=None):
    """llm_call_history rows in sequence order, optionally of one call."""
    rows = sorted(llm_records("events", socket), key=lambda row: row["sequence"])
    return [row for row in rows if call_id is None or row["call_id"] == call_id]


def turn_dump(socket=None):
    """(meta keys, turns, arrivals): every turn as a dict and every request's arrival
    position. A whole-store read that only tests make."""
    import input_ack
    from execution_gateway.cetta_worker import S
    answer = input_ack._ledger_call("turn-dump", (), input_ack.WHOLE_READ_SECONDS, socket)
    if not (isinstance(answer, tuple) and len(answer) == 4 and answer[0] == S("dump")):
        raise input_ack.LedgerUnavailable("input ledger turn-dump: unexpected answer %s" % input_ack._show(answer))
    state = {entry[0]: None if input_ack._is_none(entry[1]) else entry[1]
             for entry in input_ack._entries(answer[1], "turn-dump")}
    turns = [dict(serial=t[1], status=t[2], revision=t[3], mode=t[4], owner=t[5], focus=t[6],
                  boundary_digest=t[7], router=t[8], guard=t[9], position=t[10])
             for t in input_ack._entries(answer[2], "turn-dump")]
    arrivals = {entry[0]: entry[1] for entry in input_ack._entries(answer[3], "turn-dump")}
    return state, sorted(turns, key=lambda turn: turn["serial"]), arrivals
