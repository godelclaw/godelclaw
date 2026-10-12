"""Durable native call identities and evidence; no transport or dispatch.

Metadata survives restart; an ephemeral HTTP handle does not. Claimed calls
from a dead process become uncertain, never an automatic POST replay.
"""
import contextlib
import hashlib
import json
import os
import re
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
        fields = Path("/proc", str(int(pid)), "stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] in ("Z", "X") else fields[19]
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
        columns = {row[1] for row in db.execute("PRAGMA table_info(native_calls)")}
        for name in ("activity", "lane"):
            if name not in columns:
                db.execute(f"ALTER TABLE native_calls ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "notified" not in {row[1] for row in db.execute("PRAGMA table_info(native_calls)")}:
            db.execute("ALTER TABLE native_calls ADD COLUMN notified INTEGER NOT NULL DEFAULT 0")
            # Processes before this column announced the outcomes they saw.
            db.execute("UPDATE native_calls SET notified=1 WHERE state NOT IN ('claimed','submitted')")
        _prune(db)
        yield db


def recover():
    with transaction() as db:
        for row in db.execute("SELECT id,pid,process_start,state FROM native_calls WHERE state IN ('claimed','submitted')").fetchall():
            if row[2] is not None and process_start(row[1]) == row[2]:
                continue
            db.execute("UPDATE native_calls SET state='uncertain',reason='process-interrupted-remote-outcome-unknown',updated=? WHERE id=?",
                       (time.time(), row[0]))
            db.execute("UPDATE requests SET status='unknown',output=NULL,finish='interrupted-unknown' WHERE id=? AND status='pending'", (row[0],))


def claim(plan):
    recover()
    with transaction() as db:
        active = db.execute("SELECT activity,lane FROM native_calls WHERE state IN ('claimed','submitted')").fetchall()
        activity = getattr(plan, "activity", "")
        lane = getattr(plan, "lane", "")
        if not activity:
            if active:
                return False
        else:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(activity)) or lane not in ("interactive", "background"):
                return False
            capacity = int(os.environ.get("METTACLAW_ACTIVITY_CALL_CAPACITY", "2"))
            reserve = int(os.environ.get("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", "1"))
            if not 1 <= reserve < capacity <= 16:
                raise ValueError("invalid activity call capacity")
            if any(not row[0] or row[0] == activity for row in active) or len(active) >= capacity:
                return False
            if lane == "background" and sum(row[1] == "background" for row in active) >= capacity - reserve:
                return False
        now = time.time()
        db.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,activity,lane) VALUES (?,?,?,?,?,?,'claimed',?,?,?)",
                   (plan.correlation, plan.epoch, os.getpid(), process_start(os.getpid()), now, now, getattr(plan, "guard", None), activity, lane))
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
    if state not in ("completed", "late", "failed", "uncertain"):
        raise ValueError("invalid native call outcome")
    if result is not None and len(result.encode()) > plan.cap * 4:
        raise ValueError("native evidence exceeds its bound")
    with transaction() as db:
        changed = db.execute("UPDATE native_calls SET state=?,result=?,reason=?,updated=? WHERE id=? AND epoch=? AND state IN ('claimed','submitted')",
                             (state, result, reason, time.time(), plan.correlation, plan.epoch)).rowcount == 1
        if changed and state == "uncertain":
            db.execute("UPDATE requests SET status='unknown',output=NULL,finish='transport-unknown' WHERE id=?",
                       (plan.correlation,))
        return changed


def status():
    recover()
    with transaction() as db:
        rows = db.execute("SELECT id,state,created,updated,soft_elapsed,bytes,quiet_ms,reason FROM native_calls ORDER BY created DESC LIMIT 12").fetchall()
    keys = ("id", "state", "created", "updated", "soft_elapsed", "bytes", "quiet_ms", "reason")
    return [dict(zip(keys, row)) for row in rows]


def result(identity):
    with transaction() as db:
        row = db.execute("SELECT state,result,reason FROM native_calls WHERE id=?", (str(identity),)).fetchone()
    if row is None:
        return None
    full = row[1]
    value = dict(id=str(identity), state=row[0], result=full,
        evidence_retained=full is not None, reason=row[2], action_authority=False,
        result_digest=hashlib.sha256((full or "").encode()).hexdigest(),
        result_chars=len(full or ""), truncated=False)
    while len(json.dumps(value)) > 2400:
        value["result"] = str(value["result"] or "")[:len(str(value["result"] or "")) // 2]
        value["truncated"] = True
    return json.dumps(value)


def evidence_context():
    # A missed wake or a restart cannot erase this durable evidence listing.
    rows = [dict(id=row["id"], state=row["state"], reason=row["reason"])
            for row in status() if row["state"] in ("late", "uncertain")][:4]
    return ("RETAINED_MODEL_CALLS " + json.dumps(rows) +
            "\nUse model-result to inspect these original-call receipts; they grant no action authority.") if rows else ""


def notify():
    # Once per call, across restarts: a repeated identical notice would name
    # an input that was already addressed, and could not be taken up again.
    recover()
    with transaction() as db:
        rows = db.execute("SELECT id,state FROM native_calls WHERE state IN ('late','uncertain') "
                          "AND notified=0 ORDER BY created").fetchall()
    for identity, state in rows:
        try:
            import telegram
            telegram.enqueue_runtime_event("retained model call " + identity + " is " + state + "; inspect model-result before reconsidering", "model call evidence ready")
        except Exception:
            continue  # the durable context listing remains available
        with transaction() as db:
            db.execute("UPDATE native_calls SET notified=1 WHERE id=?", (identity,))
