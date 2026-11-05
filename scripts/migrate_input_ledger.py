#!/usr/bin/env python3
"""One-shot copy of the input ledger tables of a SQLite ledger into an EMPTY native store.

    migrate_input_ledger.py --source <ledger.sqlite3> --socket <ledger.sock>

Both arguments are required and nothing is defaulted from the environment: the
source is opened read-only (SQLite mode=ro, one read transaction) and is never
written; the target is the native db server (src/input_ledger_server.metta)
listening on --socket, reached through src/input_ledger.metta in a cetta value
worker (CETTA_VALUE_WORKER_BIN, else next to CETTA_BIN, as input_ack.py finds it).

It copies processed_inputs and input_notices (all ten columns; NULL message_id
and delete_key become the symbol `none`), the working set (meta 'working_set',
the JSON text of the last turn checkpoint, as one (working-set "current" "<text>") atom: the
boot read of the native store is the authority for it after the cutover, and the
JSON file next to the ledger is only a projection that can lag by a crash window)
and the requests: every row of requests joined with its request_context row, in
rowid order, as a (request ...) record numbered 1, 2, ... by that order and a
(request-text ...) atom of its content, linked to the one before it by (request-prev ...), with the tail atom (the
number of requests and the newest one; see "the requests" in src/input_ledger.metta).
The summary (first 180 characters) and the digest are made here as Ledger.accept
makes them. request_context.processed = 1 becomes a settled time of 0.0 (SQLite
never recorded when). Then it reads every record back by key and compares it with
the source column by column (the working set and every content byte for byte),
and counts what the store holds.
Refused before anything is written: a request without a request_context row, with a
status, kind or processed flag the native store does not know, with a content of
more bytes than the ledger's server accepts as one value (input_ack.LEDGER_VALUE_BYTES), a
source with a request that is covered or a row in input_supersessions (this script does not
copy the task context yet: nothing would delete the supersession when the request is reopened).
It refuses a target that holds any atom, and never merges into one. The copy is
made in batches, each atomic: if it stops half way, discard the target store
(it is not in use before the cutover) and run it again on a fresh one.
The report names counts, never ids. Stop every writer of the source first.

Exit status: 0 copied and verified, 1 refused or failed, 2 usage.
"""

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STATES = ("registered", "submitted", "deleted", "expired")
KINDS = ("message", "notification", "task", "goal", "autonomous")
STATUSES = ("active", "processed", "waiting-user", "deferred", "addressed", "achieved", "cancelled")
HEAD_COLUMNS = ("id", "seq", "kind", "checker", "status", "settled", "note", "summary", "digest")
NOTICE_COLUMNS = ("task", "update_id", "lane", "send_key", "created", "message_id",
                  "processed", "delete_key", "state", "checked")
BATCH = 200


class MigrationError(Exception):
    """The copy was refused or could not be completed or verified."""


def _number(value, where):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise MigrationError("%s is %s, not a finite number" % (where, type(value).__name__))
    return float(value)


def _text(value, where):
    if type(value) is not str:
        raise MigrationError("%s is %s, not text" % (where, type(value).__name__))
    return value


def read_source(path):
    """(processed rows, notice rows, working set text or None, request rows) of the SQLite ledger, in one read
    transaction on a read-only connection. A request row is (id, content, status, checker, kind, processed, note) in
    rowid order."""
    path = Path(path)
    if not path.is_file():
        raise MigrationError("the source ledger is not a file: %s" % path)
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            db.execute("PRAGMA query_only=1")
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "processed_inputs" not in tables:
                raise MigrationError("the source has no processed_inputs table: not an input ledger")
            processed = db.execute("SELECT id, processed FROM processed_inputs ORDER BY id").fetchall()
            working = None
            if "meta" in tables:
                found = db.execute("SELECT value FROM meta WHERE key='working_set'").fetchall()
                if found:
                    working = _working_text(found[0][0])
            requests = []
            if "requests" in tables:
                if "input_supersessions" in tables and db.execute("SELECT 1 FROM input_supersessions").fetchone():
                    raise MigrationError("the source has input_supersessions rows: the task-context family is not native yet")
                context = "request_context" in tables
                requests = db.execute("SELECT r.id, r.content, r.status, r.checker, %s FROM requests r %s ORDER BY r.rowid"
                                      % (("c.kind, c.processed, c.note", "LEFT JOIN request_context c ON c.request=r.id")
                                         if context else ("NULL, NULL, NULL", ""))).fetchall()
            notices = []
            if "input_notices" in tables:
                have = {row[1] for row in db.execute("PRAGMA table_info(input_notices)")}
                missing = [name for name in NOTICE_COLUMNS if name not in have]
                if missing and missing != ["checked"]:
                    raise MigrationError("input_notices lacks the columns " + ", ".join(missing))
                # A table from before `checked` existed reads as input_ack's ALTER TABLE would give it: 0.
                columns = ", ".join(name if name in have else "0.0" for name in NOTICE_COLUMNS)
                notices = db.execute("SELECT %s FROM input_notices ORDER BY task" % columns).fetchall()
    except sqlite3.Error as error:
        raise MigrationError("cannot read the source ledger: %s" % error) from error
    return processed, notices, working, requests


def _working_text(value):
    """The stored working set, if it is what Ledger.checkpoint writes: the JSON text of an object."""
    if type(value) is not str:
        raise MigrationError("meta working_set is %s, not text" % type(value).__name__)
    try:
        parsed = json.loads(value)
        value.encode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise MigrationError("meta working_set is not JSON text: %s" % error) from error
    if not isinstance(parsed, dict):
        raise MigrationError("meta working_set is JSON but not an object")
    return value


def processed_atom(row, number):
    from execution_gateway.cetta_worker import E
    where = "processed_inputs row %d" % number
    return E("processed-input", _text(row[0], where + " id"), _number(row[1], where + " processed"))


def notice_atom(row, number):
    """The input-notice record of a SQLite row, as src/input_ledger.metta describes it."""
    from execution_gateway.cetta_worker import E, S
    where = "input_notices row %d" % number
    task, update_id, lane, send_key, created, message_id, processed, delete_key, state, checked = row
    if message_id is None:
        message = S("none")
    elif type(message_id) is int or type(message_id) is str:
        message = message_id
    else:
        raise MigrationError("%s message_id is %s" % (where, type(message_id).__name__))
    if type(processed) is not int or processed not in (0, 1):
        raise MigrationError("%s processed is not 0 or 1" % where)
    if state not in STATES:
        raise MigrationError("%s has a state that is none of %s" % (where, ", ".join(STATES)))
    return E("input-notice", _text(task, where + " task"), _text(update_id, where + " update_id"),
             _text(lane, where + " lane"), _text(send_key, where + " send_key"),
             _number(created, where + " created"), message, processed,
             S("none") if delete_key is None else _text(delete_key, where + " delete_key"),
             S(state), _number(checked, where + " checked"))


def request_atoms(row, number):
    """The (request ...) record and the (request-text ...) atom of a SQLite request row; number counts from 0."""
    from execution_gateway.cetta_worker import E, S
    import input_ack
    import observed_strategy
    where = "request row %d" % (number + 1)
    identity, content, status, checker, kind, processed, note = row
    _text(identity, where + " id")
    _text(content, where + " content")
    _text(checker, where + " checker")
    if kind is None:
        raise MigrationError("%s (%s) has no request_context row" % (where, "an id"))
    if kind not in KINDS:
        raise MigrationError("%s has a kind that is none of %s" % (where, ", ".join(KINDS)))
    if status == "covered":
        raise MigrationError("%s is covered: the task-context family is not native yet" % where)
    if status not in STATUSES:
        raise MigrationError("%s has a status that is none of %s" % (where, ", ".join(STATUSES)))
    if type(processed) is not int or processed not in (0, 1):
        raise MigrationError("%s processed is not 0 or 1" % where)
    if len(content.encode("utf-8", "surrogatepass")) >= input_ack.LEDGER_VALUE_BYTES:
        raise MigrationError("%s has a content of %d bytes: more than the ledger's server accepts as one value"
                             % (where, len(content.encode("utf-8", "surrogatepass"))))
    try:
        content.encode("utf-8")
    except UnicodeError as error:
        raise MigrationError("%s content is not text: %s" % (where, error)) from error
    return (E("request", identity, number + 1, kind, checker, status, 0.0 if processed else S("none"),
              _text(note, where + " note"), content[:180], observed_strategy.digest(content)),
            E("request-text", identity, content))


def request_links(requests):
    """The chain of the requests (request-prev per request) and the tail atom (their number and the newest)."""
    from execution_gateway.cetta_worker import E
    before, links = "", []
    for row in requests:
        links.append(E("request-prev", row[0], before))
        before = row[0]
    return links + ([E("request-tail", "tail", len(requests), before)] if requests else [])


def _same_number(stored, expected):
    from execution_gateway.cetta_worker import FloatBits, scalar_float
    return isinstance(stored, FloatBits) and scalar_float(stored) == expected


def _mismatch(stored, expected):
    """The names of the columns in which a stored record differs from the one that was copied."""
    from execution_gateway.cetta_worker import FloatBits
    names = ("tag",) + {"processed-input": ("id", "processed"), "request": HEAD_COLUMNS,
                        "request-text": ("id", "content"), "request-prev": ("id", "previous")}.get(expected[0].name, NOTICE_COLUMNS)
    if not (isinstance(stored, tuple) and len(stored) == len(expected)):
        return ["shape"]
    different = []
    for name, have, want in zip(names, stored, expected):
        if isinstance(want, float):
            same = _same_number(have, want)
        else:
            same = type(have) is type(want) and have == want
        if not same:
            different.append(name)
    return different


def _batches(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def migrate(source, socket, *, batch=BATCH, after_copy=None):
    """Copy `source` into the empty store behind `socket`; verify; return the report.

    `after_copy(call)` runs between the copy and the check, with the ledger call
    function (verb, arguments): a seam for the tests, which change the store there."""
    from execution_gateway.cetta_worker import E, S
    import input_ack
    if not socket:
        raise MigrationError("no target socket")

    def call(verb, arguments, timeout=60):
        try:
            return input_ack._ledger_call(verb, arguments, timeout, socket=socket)
        except sqlite3.OperationalError as error:
            raise MigrationError("the native ledger failed: %s" % error) from error

    processed, notices, working, requests = read_source(source)
    atoms = [processed_atom(row, number) for number, row in enumerate(processed)]
    notice_atoms = [notice_atom(row, number) for number, row in enumerate(notices)]
    request_pairs = [request_atoms(row, number) for number, row in enumerate(requests)]
    heads = [pair[0] for pair in request_pairs]
    # A request's record and its content stay together, in acceptance order; the chain and the counters close the list.
    request_rows = [atom for pair in request_pairs for atom in pair] + request_links(requests)
    # The working set goes first: in the first batch, so that a value the store refuses (the engine takes no
    # atom of about 1 MiB) is refused before anything else was copied.
    working_atoms = [] if working is None else [E("working-set", "current", working)]
    census = call("census", ())
    if census != (S("census"), 0, 0, 0, 0):
        raise MigrationError("the target store is not empty (census: %s): nothing was copied"
                             % input_ack._show(census))
    expect, batches = "empty", 0
    big = working is not None and len(working.encode("utf-8")) >= input_ack.WORKING_SET_NOTE
    for chunk in _batches(working_atoms + atoms + notice_atoms + request_rows, batch):
        try:
            answer = call("import", (S(expect), tuple(chunk)))
        except MigrationError as error:
            if batches == 0 and big:
                # The server runs the import: a value of about 1 MiB cannot be sent to it, which refuses it as surely.
                raise MigrationError("the store refused the working set (%d bytes; the engine takes no value of about "
                                     "1 MiB): nothing was copied (%s)" % (len(working.encode("utf-8")), error)) from error
            raise
        if answer == S("refused"):
            # The verb answers `refused` for an aborted transaction: a store that is not empty, or a body that
            # failed, which is what the engine's limit on the size of an atom (about 1 MiB) does to the first batch.
            raise MigrationError(("the target store is not empty, or it refused the working set (%d bytes; the engine "
                                  "takes no value of about 1 MiB): nothing was copied" % len(working.encode("utf-8"))
                                  if big else "the target store is not empty: nothing was copied") if batches == 0
                                 else "the target store changed during the copy")
        if answer is not True:
            raise MigrationError("unexpected answer to a batch: %s" % input_ack._show(answer))
        expect, batches = "any", batches + 1
    if after_copy is not None:
        after_copy(call)
    census = call("census", ())
    # other atoms: the working set, per request a content and a link to the one before, and the tail atom
    if census != (S("census"), len(atoms), len(notice_atoms), len(heads),
                  len(working_atoms) + 2 * len(heads) + (1 if heads else 0)):
        raise MigrationError("the target holds %s, the source had %d processed ids, %d notices, %d requests and %d working set"
                             % (input_ack._show(census), len(atoms), len(notice_atoms), len(heads), len(working_atoms)))
    newest = call("request-page", (0, 1))
    if heads and not (isinstance(newest, tuple) and newest[1:2] == (len(heads),) and len(newest[2]) == 1
                      and newest[2][0][0] == heads[-1][1]):
        raise MigrationError("the request counter and tail of the target are not %d and %r: %s"
                             % (len(heads), heads[-1][1], input_ack._show(newest)))
    stored = call("working-set", ())
    if (stored if isinstance(stored, str) else None) != working:
        raise MigrationError("the working set read back from the target differs from the source's")
    request_texts = {pair[0][1]: pair[1] for pair in request_pairs}
    request_before = {link[1]: link for link in request_links(requests) if link[0].name == "request-prev"}
    for kind, rows in (("processed", atoms), ("notice", notice_atoms), ("request", heads)):
        for chunk in _batches(rows, batch):
            found = call("lookup", (S(kind), tuple(item[1] for item in chunk)))
            by_key = {}
            for stored in found:
                by_key.setdefault((stored[0], stored[1]), []).append(stored)
            for want in chunk:
                wanted = [want] + ([request_texts[want[1]], request_before[want[1]]] if kind == "request" else [])
                for atom in wanted:
                    have = by_key.get((S(atom[0].name), atom[1]), [])
                    if len(have) != 1:
                        raise MigrationError("%s row %d: the target holds %d %s records for its key"
                                             % (kind, rows.index(want), len(have), atom[0].name))
                    different = _mismatch(have[0], atom)
                    if different:
                        raise MigrationError("%s row %d differs in: %s" % (kind, rows.index(want), ", ".join(different)))
    return {"source": str(Path(source).resolve()), "socket": str(socket), "processed_inputs": len(atoms),
            "input_notices": len(notice_atoms), "requests": len(heads), "working_set_bytes": None if working is None else len(working.encode("utf-8")),
            "batches": batches, "verified": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="the SQLite ledger to read (read-only)")
    parser.add_argument("--socket", required=True, help="the socket of the native ledger server (an empty store)")
    parser.add_argument("--batch", type=int, default=BATCH, help="rows per transaction (default %d)" % BATCH)
    args = parser.parse_args(argv)
    if not 1 <= args.batch <= 1000:
        parser.error("--batch must be between 1 and 1000")
    try:
        report = migrate(args.source, args.socket, batch=args.batch)
    except MigrationError as error:
        print("migration refused or failed: %s" % error, file=sys.stderr)
        return 1
    finally:
        import input_ack
        input_ack.close_ledger()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
