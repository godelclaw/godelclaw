"""Durable native call identities and evidence; no transport or dispatch.

Metadata survives restart; an ephemeral HTTP handle does not. Claimed calls
from a dead process become uncertain, never an automatic POST replay.
"""
import contextlib
import json
import os
from pathlib import Path
import time
import llm_policy

EVIDENCE_LIMIT = 32
EVIDENCE_SECONDS = 7 * 86400
RECEIPT_LIMIT = 256
RECEIPT_SECONDS = 30 * 86400


def _prune(db):
    """Bound terminal evidence without retiring any live transport authority."""
    now = time.time()
    terminal = "state NOT IN ('claimed','submitted')"
    db.execute(f"UPDATE native_calls SET result=NULL WHERE {terminal} AND "
               "(updated<? OR id NOT IN (SELECT id FROM native_calls WHERE result IS NOT NULL "
               f"AND {terminal} ORDER BY updated DESC LIMIT ?))",
               (now - EVIDENCE_SECONDS, EVIDENCE_LIMIT))
    db.execute(f"DELETE FROM native_calls WHERE {terminal} AND "
               "(updated<? OR id NOT IN (SELECT id FROM native_calls "
               f"WHERE {terminal} ORDER BY updated DESC LIMIT ?))",
               (now - RECEIPT_SECONDS, RECEIPT_LIMIT))


def process_start(pid):
    try:
        return Path("/proc", str(int(pid)), "stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return None


@contextlib.contextmanager
def transaction():
    with llm_policy.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS native_calls (
            id TEXT PRIMARY KEY, epoch TEXT NOT NULL, pid INTEGER NOT NULL, process_start TEXT,
            created REAL NOT NULL, updated REAL NOT NULL, state TEXT NOT NULL,
            guard TEXT, transport INTEGER, soft_elapsed INTEGER NOT NULL DEFAULT 0,
            bytes INTEGER NOT NULL DEFAULT 0, quiet_ms INTEGER NOT NULL DEFAULT 0,
            result TEXT, reason TEXT)""")
        _prune(db)
        yield db


def recover():
    with transaction() as db:
        for row in db.execute("SELECT id,pid,process_start,state FROM native_calls WHERE state IN ('claimed','submitted')").fetchall():
            if row[2] is not None and process_start(row[1]) == row[2]:
                continue
            db.execute("UPDATE native_calls SET state='uncertain',reason='process-interrupted-remote-outcome-unknown',updated=? WHERE id=?",
                       (time.time(), row[0]))
            db.execute("UPDATE requests SET status='failed',output=NULL,finish='interrupted-unknown' WHERE id=? AND status='pending'", (row[0],))


def claim(plan):
    recover()
    with transaction() as db:
        if db.execute("SELECT 1 FROM native_calls WHERE state IN ('claimed','submitted')").fetchone():
            return False
        now = time.time()
        db.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard) VALUES (?,?,?,?,?,?,'claimed',?)",
                   (plan.correlation, plan.epoch, os.getpid(), process_start(os.getpid()), now, now, getattr(plan, "guard", None)))
    return True


def submitted(plan, transport):
    with transaction() as db:
        db.execute("UPDATE native_calls SET state='submitted',transport=?,updated=? WHERE id=? AND epoch=? AND state='claimed'",
                   (int(transport), time.time(), plan.correlation, plan.epoch))


def progress(plan, count, quiet, soft):
    with transaction() as db:
        db.execute("UPDATE native_calls SET bytes=?,quiet_ms=?,soft_elapsed=?,updated=? WHERE id=? AND epoch=? AND state='submitted'",
                   (int(count), int(quiet), int(soft), time.time(), plan.correlation, plan.epoch))


def settle(plan, state, result=None, reason=None):
    if state not in ("completed", "late", "failed"):
        raise ValueError("invalid native call outcome")
    if result is not None and len(result.encode()) > plan.cap * 4:
        raise ValueError("native evidence exceeds its bound")
    with transaction() as db:
        return db.execute("UPDATE native_calls SET state=?,result=?,reason=?,updated=? WHERE id=? AND epoch=? AND state IN ('claimed','submitted')",
                          (state, result, reason, time.time(), plan.correlation, plan.epoch)).rowcount == 1


def status():
    recover()
    with transaction() as db:
        rows = db.execute("SELECT id,state,created,updated,soft_elapsed,bytes,quiet_ms,reason FROM native_calls ORDER BY created DESC LIMIT 12").fetchall()
    keys = ("id", "state", "created", "updated", "soft_elapsed", "bytes", "quiet_ms", "reason")
    return [dict(zip(keys, row)) for row in rows]


def result(identity):
    with transaction() as db:
        row = db.execute("SELECT state,result,reason FROM native_calls WHERE id=?", (str(identity),)).fetchone()
    return None if row is None else json.dumps(dict(id=str(identity), state=row[0], result=row[1],
        evidence_retained=row[1] is not None, reason=row[2]))
