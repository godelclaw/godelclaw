#!/usr/bin/env python3
"""Move an agent's LLM call ledger (llm-budget.sqlite3) into its native ledger store, check it, and back.

    migrate_llm_ledger.py copy     --source <llm-budget.sqlite3> --socket <ledger.sock>
    migrate_llm_ledger.py verify   --source <llm-budget.sqlite3> --socket <ledger.sock>
    migrate_llm_ledger.py export   --socket <ledger.sock> --schema-from <llm-budget.sqlite3> --output <new.sqlite3>
    migrate_llm_ledger.py rollback --socket <ledger.sock> --reason <text>
    migrate_llm_ledger.py discard  --socket <ledger.sock>

The target is the `provider` collection of the store that the agent's ledger server
(src/input_ledger_server.metta) serves on --socket; src/llm_ledger.metta describes its records.
The source is opened read-only (mode=ro, one read transaction) and never written.

copy copies every row of requests, cooldowns, native_calls (its result as a separate record),
llm_call_history (with its sequence numbers) and admission_recovery, builds the newest-first list
of the turn calls and the two counters, and refuses a collection that holds anything. The first
batch marks the collection as importing (every verb then answers an Error); the last one checks
the census against the source's and only then selects the native ledger. Nothing selects it
before: callers keep using the SQLite file until that last batch commits. A copy that stopped half
way leaves the importing mark (and callers then answer an error); discard removes such an import
whole, and the callers use the SQLite file again.

verify compares every record with its source row, value and type, and every reader on both sides:
the status listing, the notices selection, each call's result, call state and completion state,
each request's pending mode, the request counts, the unknown requests, the active calls and the
usage report over the whole history.

export writes the native ledger as a new SQLite file with the source's schema, for a rollback after
native writes; rollback then hands the ledger back to SQLite (the native records stay as evidence).
The order: stop the agent, its coding sessions and its control process; export; check the export
with verify (--source the export); rollback; put the export in place of the live file, the old
one kept; start.

Stop every writer of the source before copy. Reports name counts, never ids or contents.
Exit status: 0 done and checked, 1 refused or failed, 2 usage.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TABLES = ("requests", "cooldowns", "native_calls", "llm_call_history", "admission_recovery")
REQUEST_COLUMNS = ("id", "started", "reserved", "status", "output", "provider", "mode", "finish", "owner", "fingerprint")
CALL_COLUMNS = ("id", "epoch", "pid", "process_start", "created", "updated", "state", "guard", "transport",
                "soft_elapsed", "bytes", "quiet_ms", "reason", "activity", "lane", "purpose", "notified")
BATCH_BYTES = 8 * 1024 * 1024
BATCH_RECORDS = 4000


class Refused(Exception):
    pass


def _call(verb, arguments, socket, timeout=600):
    import input_ack
    import llm_policy
    return llm_policy.plain(input_ack._ledger_call(verb, tuple(llm_policy._argument(a) for a in arguments),
                                                   timeout, socket))


def read_source(path):
    """The five tables of a ledger file, each as a list of rows in rowid order."""
    path = Path(path)
    if not path.is_file():
        raise Refused("no ledger file at the source")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables = {}
        for name, columns in (("requests", ", ".join(REQUEST_COLUMNS)), ("cooldowns", "provider, until"),
                              ("native_calls", ", ".join(CALL_COLUMNS) + ", result"),
                              ("llm_call_history", "sequence, call_id, event, observed, detail"),
                              ("admission_recovery", "id, recovered_at, reason, cutoff")):
            tables[name] = (db.execute("SELECT %s FROM %s ORDER BY rowid" % (columns, name)).fetchall()
                            if name in present else [])
        unknown = present - set(TABLES) - {"sqlite_sequence"}
        if unknown:
            raise Refused("the source has %d table(s) this copy does not know" % len(unknown))
    for name, rows in tables.items():
        for row in rows:
            if any(isinstance(value, bytes) for value in row):
                raise Refused("a %s row holds a BLOB" % name)
    return tables


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def records(tables):
    """The native records of the source, in a fixed order, and the census they make."""
    from execution_gateway.cetta_worker import E, S
    none = lambda value: S("none") if value is None else value
    out = []
    for row in tables["requests"]:
        out.append(E("llm:request", *map(none, row)))
    for row in tables["cooldowns"]:
        out.append(E("llm:cooldown", *map(none, row)))
    results = 0
    for row in tables["native_calls"]:
        out.append(E("llm:call", *map(none, row[:-1])))
        if row[-1] is not None:
            out.append(E("llm:result", row[0], row[-1]))
            results += 1
    for row in tables["llm_call_history"]:
        out.append(E("llm:event", *map(none, row)))
    # newest first is ORDER BY created DESC with ties in rowid order: built oldest first, ties reversed
    turns = [row for _, row in sorted(((-number, row) for number, row in enumerate(tables["native_calls"])
                                       if row[15] == "turn"), key=lambda pair: (pair[1][4], pair[0]))]
    older = None
    for row in turns:
        out.append(E("llm:recent", row[0], none(older), row[4]))
        older = row[0]
    for row in tables["admission_recovery"]:
        out.append(E("llm:recovery", *map(none, row)))
    states = 1  # "selected"
    if tables["llm_call_history"]:
        out.append(E("llm:state", "event-tail", max(row[0] for row in tables["llm_call_history"])))
        states += 1
    if turns:
        out.append(E("llm:state", "recent-turn", turns[-1][0]))
        states += 1
    counts = (len(tables["requests"]), len(tables["cooldowns"]), len(tables["native_calls"]), results,
              len(tables["llm_call_history"]), len(turns), len(tables["admission_recovery"]), states)
    return out, E("census", *counts, sum(counts))


def _size(value):
    if isinstance(value, str):
        return len(value.encode()) + 8
    if isinstance(value, tuple):
        return 8 + sum(_size(item) for item in value)
    return 12


def _batches(items):
    batch, size = [], 0
    for item in items:
        weight = _size(item)
        if batch and (size + weight > BATCH_BYTES or len(batch) >= BATCH_RECORDS):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += weight
    yield batch


def copy(source, socket, *, progress=print):
    from execution_gateway.cetta_worker import S
    import llm_policy
    census = _call("llm-census", (), socket)
    if tuple(census[1:]) != (0,) * 9:
        raise Refused("the target's provider collection is not empty")
    tables = read_source(source)
    items, expect = records(tables)
    origin = digest(source)
    batches = list(_batches(items))
    for number, batch in enumerate(batches):
        phase = "begin" if number == 0 else "more"
        last = number == len(batches) - 1
        if last:
            if number == 0:  # one batch: begin it empty, then finish with the rows
                answer = _call("llm-import", (S("begin"), origin, (), expect, 0.0, llm_policy._request()), socket)
                if answer != S("imported"):
                    raise Refused("the target refused the first batch: %r" % (answer,))
            phase = "finish"
        answer = _call("llm-import", (S(phase), origin, tuple(batch), expect, time.time(), llm_policy._request()), socket)
        if answer != S("imported"):
            raise Refused("the target refused batch %d of %d: %r" % (number + 1, len(batches), answer))
        progress("batch %d/%d: %d records" % (number + 1, len(batches), len(batch)))
    if _call("llm-selection", (), socket) != S("native"):
        raise Refused("the target does not select the native ledger after the copy")
    return expect


def _typed(value):
    return (type(value).__name__, repr(value))


def _native_rows(socket):
    """Every native record, as the source rows they stand for."""
    kinds = {kind: _call("llm-records", (kind,), socket) for kind in
             ("requests", "cooldowns", "calls", "results", "events", "recoveries")}
    results = {row[1]: row[2] for row in kinds["results"]}
    if len(results) != len(kinds["results"]):
        raise Refused("a call has two result records")
    calls = [row[1:] + (results.pop(row[1], None),) for row in kinds["calls"]]
    if results:
        raise Refused("%d result record(s) without their call" % len(results))
    return {"requests": [row[1:] for row in kinds["requests"]], "cooldowns": [row[1:] for row in kinds["cooldowns"]],
            "native_calls": calls, "llm_call_history": [row[1:] for row in kinds["events"]],
            "admission_recovery": [row[1:] for row in kinds["recoveries"]]}


def compare_records(tables, socket):
    mismatches = []
    native = _native_rows(socket)
    for name in TABLES:
        want = sorted(tuple(map(_typed, row)) for row in tables[name])
        have = sorted(tuple(map(_typed, row)) for row in native[name])
        if want != have:
            mismatches.append("%s: %d source rows, %d native, %d differ" % (
                name, len(want), len(have), len(set(want) ^ set(have))))
    return mismatches


def _status_rows(db):
    return [tuple(row) for row in db.execute(
        "SELECT id,state,created,updated,soft_elapsed,bytes,quiet_ms,reason FROM native_calls "
        "WHERE purpose='turn' ORDER BY created DESC LIMIT 12")]


def compare_readers(source, socket):
    """Each reader's answer from the source file and from the native ledger, compared."""
    import usage_history
    mismatches = []
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        def same(what, want, have):
            if [tuple(map(_typed, row)) for row in want] != [tuple(map(_typed, row)) for row in have]:
                mismatches.append(what)
        same("status", _status_rows(db), _call("llm-status", (), socket))
        same("notices", [tuple(row) for row in db.execute(
            "SELECT id,state FROM native_calls WHERE state IN ('late','uncertain') AND notified=0 "
            "AND purpose='turn' ORDER BY created")], _call("llm-notices", (), socket))
        calls = db.execute("SELECT id,epoch,state,result,reason,purpose FROM native_calls").fetchall()
        bad = 0
        for identity, epoch, state, result, reason, purpose in calls:
            if _call("llm-result", (identity,), socket) != (state, result, reason, purpose):
                bad += 1
            if _call("llm-call-state", (identity, epoch), socket) != (state, reason):
                bad += 1
            row = db.execute(
                "SELECT CASE WHEN r.status='completed' AND n.state='completed' AND n.result=? THEN 'committed' "
                "WHEN r.status='pending' AND n.state IN ('claimed','submitted') THEN 'pending' ELSE 'conflict' END "
                "FROM native_calls n JOIN requests r ON r.id=n.id WHERE n.id=? AND n.epoch=?",
                (result if result is not None else "", identity, epoch)).fetchone()
            expected = row[0] if row else "conflict"
            answer = _call("llm-completion-state", (identity, epoch, result if result is not None else ""), socket)
            if getattr(answer[1], "name", None) != expected:
                bad += 1
        if bad:
            mismatches.append("calls: %d answers differ" % bad)
        bad = 0
        for identity, status, mode in db.execute("SELECT id,status,mode FROM requests"):
            if _call("llm-pending-mode", (identity,), socket) != (mode if status == "pending" else None):
                bad += 1
        if bad:
            mismatches.append("pending modes: %d differ" % bad)
        counts = dict(db.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
        total = db.execute("SELECT COALESCE(SUM(COALESCE(output,reserved)),0) FROM requests").fetchone()[0]
        answer = _call("llm-requests", (), socket)
        if dict(answer[1]) != counts or answer[2] != total:
            mismatches.append("request counts")
        same("unknown", [tuple(row) for row in db.execute(
            "SELECT id,provider,mode FROM requests WHERE status='unknown' ORDER BY started DESC LIMIT 5")],
             _call("llm-unknown", (), socket))
        same("active", [tuple(row) for row in db.execute(
            "SELECT n.id,n.epoch,n.pid,n.process_start,n.state,n.updated,r.status,n.purpose FROM native_calls n "
            "LEFT JOIN requests r ON r.id=n.id WHERE n.state IN ('claimed','submitted') ORDER BY n.id")],
             sorted(_call("llm-active", (), socket)))
    until = time.time() + 1
    want = usage_history.report(source, since=0, until=until)
    saved = os.environ.get("METTACLAW_INPUT_LEDGER_SOCKET")
    os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = str(socket)
    try:
        have = usage_history.report_native(since=0, until=until)
    finally:
        if saved is None:
            os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET")
        else:
            os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = saved
    if json.dumps(want, sort_keys=True) != json.dumps(have, sort_keys=True):
        mismatches.append("usage report")
    return mismatches


def verify(source, socket):
    tables = read_source(source)
    _, expect = records(tables)
    census = _call("llm-census", (), socket)
    mismatches = [] if tuple(census) == tuple(expect) else ["census %r, expected %r" % (census[1:], expect[1:])]
    return mismatches + compare_records(tables, socket) + compare_readers(source, socket)


def export(socket, schema_from, output):
    output = Path(output)
    if output.exists():
        raise Refused("the export target exists")
    with closing(sqlite3.connect(Path(schema_from).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        schema = [row[0] for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
    native = _native_rows(socket)
    with closing(sqlite3.connect(output)) as db:
        with db:
            for statement in schema:
                db.execute(statement)
            db.executemany("INSERT INTO requests(%s) VALUES(%s)" % (",".join(REQUEST_COLUMNS), ",".join("?" * 10)),
                           native["requests"])
            db.executemany("INSERT INTO cooldowns(provider,until) VALUES(?,?)", native["cooldowns"])
            db.executemany("INSERT INTO native_calls(%s,result) VALUES(%s)" % (",".join(CALL_COLUMNS), ",".join("?" * 18)),
                           native["native_calls"])
            db.executemany("INSERT INTO llm_call_history(sequence,call_id,event,observed,detail) VALUES(?,?,?,?,?)",
                           sorted(native["llm_call_history"]))
            db.executemany("INSERT INTO admission_recovery(id,recovered_at,reason,cutoff) VALUES(?,?,?,?)",
                           native["admission_recovery"])
    return {name: len(rows) for name, rows in native.items()}


def discard(socket):
    import llm_policy
    from execution_gateway.cetta_worker import S
    answer = _call("llm-discard", (llm_policy._request(),), socket)
    if answer != S("discarded"):
        raise Refused("nothing to discard: %r" % (answer,))


def rollback(socket, reason):
    import llm_policy
    from execution_gateway.cetta_worker import S
    answer = _call("llm-rollback", (reason, llm_policy._request()), socket)
    if answer != S("rolled-back"):
        raise Refused("the target refused the rollback: %r" % (answer,))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("copy", "verify", "export", "rollback", "discard"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--schema-from", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        if args.action in ("copy", "verify"):
            if args.source is None:
                parser.error("%s needs --source" % args.action)
            if args.action == "copy":
                expect = copy(args.source, args.socket)
                print(json.dumps({"copied": list(expect[1:])}))
            mismatches = verify(args.source, args.socket)
            print(json.dumps({"mismatches": mismatches}))
            return 1 if mismatches else 0
        if args.action == "export":
            if args.schema_from is None or args.output is None:
                parser.error("export needs --schema-from and --output")
            print(json.dumps({"exported": export(args.socket, args.schema_from, args.output)}))
            return 0
        if args.action == "discard":
            discard(args.socket)
            print(json.dumps({"discarded": True}))
            return 0
        if not args.reason:
            parser.error("rollback needs --reason")
        rollback(args.socket, args.reason)
        print(json.dumps({"rolled_back": True}))
        return 0
    except Refused as refusal:
        print(json.dumps({"refused": str(refusal)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
