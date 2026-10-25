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
TERMINAL_RECONCILE_GRACE_SECONDS = 2


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
        if "purpose" not in columns:
            db.execute("ALTER TABLE native_calls ADD COLUMN purpose TEXT NOT NULL DEFAULT 'turn'")
        if "notified" not in columns:
            db.execute("ALTER TABLE native_calls ADD COLUMN notified INTEGER NOT NULL DEFAULT 0")
            # Processes before this column announced the outcomes they saw.
            db.execute("UPDATE native_calls SET notified=1 WHERE state NOT IN ('claimed','submitted')")
        _prune(db)
        yield db


def recover():
    with transaction() as db:
        now = time.time()
        rows = db.execute(
            "SELECT native_calls.id,native_calls.pid,native_calls.process_start,"
            "native_calls.state,native_calls.updated,requests.status,native_calls.purpose "
            "FROM native_calls LEFT JOIN requests ON requests.id=native_calls.id "
            "WHERE native_calls.state IN ('claimed','submitted')"
        ).fetchall()
        for identity, pid, started, state, updated, request_state, purpose in rows:
            # The request ledger and the transport ledger share one identity.
            # A terminal request cannot continue to own admission authority.
            # Give the ordinary completion path a tiny grace interval between
            # its two durable writes; old contradictions are reconciled even
            # when their owning process is still alive.
            if request_state in ("completed", "failed", "unknown") and (
                    now - float(updated) >= TERMINAL_RECONCILE_GRACE_SECONDS):
                if request_state == "completed":
                    outcome = "completed"
                    reason = "request-completed-before-lifetime-settlement"
                elif request_state == "failed" and state == "claimed":
                    outcome = "failed"
                    reason = "request-failed-before-submission"
                else:
                    outcome = "uncertain"
                    reason = "terminal-request-remote-outcome-unknown"
                if purpose == "compaction":
                    outcome, reason = "abandoned", "compaction-publication-not-completed"
                db.execute(
                    "UPDATE native_calls SET state=?,reason=?,updated=? "
                    "WHERE id=? AND state IN ('claimed','submitted')",
                    (outcome, reason, now, identity),
                )
                if outcome == "uncertain":
                    db.execute(
                        "UPDATE requests SET status='unknown',output=NULL,"
                        "finish='transport-unknown' WHERE id=?",
                        (identity,),
                    )
                    llm_policy.history_event(db, identity, "unknown", {"reason": reason}, now=now)
                continue
            if started is not None and process_start(pid) == started:
                continue
            outcome = "abandoned" if purpose == "compaction" else "uncertain"
            db.execute("UPDATE native_calls SET state=?,reason='process-interrupted-remote-outcome-unknown',updated=? WHERE id=?",
                       (outcome, now, identity))
            db.execute("UPDATE requests SET status='unknown',output=NULL,finish='interrupted-unknown' WHERE id=? AND status='pending'", (identity,))
            llm_policy.history_event(db, identity, "unknown", {"reason": "process-interrupted"}, now=now)


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
                raise ValueError("invalid native call activity")
            capacity = int(os.environ.get("METTACLAW_ACTIVITY_CALL_CAPACITY", "2"))
            reserve = int(os.environ.get("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", "1"))
            if not 1 <= reserve < capacity <= 16:
                raise ValueError("invalid activity call capacity")
            if any(not row[0] or row[0] == activity for row in active) or len(active) >= capacity:
                return False
            if lane == "background" and sum(row[1] == "background" for row in active) >= capacity - reserve:
                return False
        purpose = getattr(plan, "purpose", "turn")
        if purpose not in ("turn", "compaction", "coding"):
            raise ValueError("invalid native call purpose")
        now = time.time()
        db.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,activity,lane,purpose) VALUES (?,?,?,?,?,?,'claimed',?,?,?,?)",
                   (plan.correlation, plan.epoch, os.getpid(), process_start(os.getpid()), now, now, getattr(plan, "guard", None), activity, lane, purpose))
        llm_policy.history_event(db, plan.correlation, "claimed", {"epoch": plan.epoch}, now=now)
    return True


def submitted(plan, transport):
    with transaction() as db:
        changed = db.execute("UPDATE native_calls SET state='submitted',transport=?,updated=? WHERE id=? AND epoch=? AND state='claimed'",
                             (int(transport), time.time(), plan.correlation, plan.epoch)).rowcount
        if changed:
            llm_policy.history_event(db, plan.correlation, "submitted", {})


def progress(plan, count, quiet, soft):
    with transaction() as db:
        db.execute("UPDATE native_calls SET bytes=?,quiet_ms=?,soft_elapsed=?,updated=? WHERE id=? AND epoch=? AND state='submitted'",
                   (int(count), int(quiet), int(soft), time.time(), plan.correlation, plan.epoch))


def settle(plan, state, result=None, reason=None):
    if state not in ("completed", "late", "failed", "uncertain"):
        raise ValueError("invalid native call outcome")
    if result is not None and len(result.encode()) > plan.cap * 4:
        raise ValueError("native evidence exceeds its bound")
    # A response can finish between polls. Its elapsed soft wait is still a
    # fact about the call, even when no intermediate progress poll saw it.
    submitted = getattr(plan, "submitted_at", None)
    soft = int(submitted is not None and
               (time.monotonic() - submitted) * 1000 >= getattr(plan, "soft", float("inf")))
    accounting_uncertain = state == "uncertain"
    if getattr(plan, "purpose", "turn") == "compaction" and state in ("uncertain", "late"):
        state, result = "abandoned", None
    with transaction() as db:
        changed = db.execute("UPDATE native_calls SET state=?,result=?,reason=?,updated=?,soft_elapsed=MAX(soft_elapsed,?) WHERE id=? AND epoch=? AND state IN ('claimed','submitted')",
                             (state, result, reason, time.time(), soft, plan.correlation, plan.epoch)).rowcount == 1
        if changed and accounting_uncertain:
            db.execute("UPDATE requests SET status='unknown',output=NULL,finish='transport-unknown' WHERE id=?",
                       (plan.correlation,))
            llm_policy.history_event(db, plan.correlation, "unknown", {"reason": "transport-unknown"})
        return changed


def status():
    recover()
    with transaction() as db:
        rows = db.execute("SELECT id,state,created,updated,soft_elapsed,bytes,quiet_ms,reason FROM native_calls WHERE purpose='turn' ORDER BY created DESC LIMIT 12").fetchall()
    keys = ("id", "state", "created", "updated", "soft_elapsed", "bytes", "quiet_ms", "reason")
    return [dict(zip(keys, row)) for row in rows]


def command_output_rejected(plan, code):
    """Transport completion is distinct from an accepted command reply."""
    allowed = {"invalid-envelope", "invalid-native-tool", "incomplete-output",
               "refused-or-structured-output"}
    if code not in allowed:
        raise ValueError("invalid command rejection code")
    with transaction() as db:
        return db.execute("UPDATE native_calls SET reason=? WHERE id=? AND epoch=? "
                          "AND state='completed' AND purpose='turn'",
                          ("command-output:" + code, plan.correlation, plan.epoch)).rowcount == 1


def result(identity, offset=0):
    offset = int(offset)
    if offset < 0:
        raise ValueError("result offset must be nonnegative")
    with transaction() as db:
        row = db.execute("SELECT state,result,reason,purpose FROM native_calls WHERE id=?", (str(identity),)).fetchone()
    if row is None:
        return None
    full = row[1] if row[3] == "turn" else None
    representation = "canonical"
    if full is not None and str(row[2] or "").startswith("command-output:"):
        # Recovery exposes the public answer, never provider reasoning parts.
        boundary = json.loads(full)
        full = "\n".join(part["text"] for item in boundary.get("items", [])
                         if item.get("kind") == "message" and item.get("role") == "assistant"
                         for part in item.get("parts", [])
                         if part.get("kind") == "text" and isinstance(part.get("text"), str))
        representation = "assistant-text"
    total = len(full or "")
    chunk = None if full is None else full[offset:offset + 2400]
    value = dict(id=str(identity), state=row[0], purpose=row[3], result=chunk,
        representation=representation, offset=offset,
        evidence_retained=full is not None, reason=row[2], action_authority=False,
        result_digest=hashlib.sha256((full or "").encode()).hexdigest(),
        result_chars=total)
    while True:
        end = offset + len(chunk or "")
        value.update(result=chunk, next_offset=end if end < total else None,
                     truncated=offset > 0 or end < total)
        encoded = json.dumps(value)
        if len(encoded) <= 2400 or not chunk:
            return encoded
        chunk = chunk[:len(chunk) // 2]


def evidence_context():
    # A missed wake or a restart cannot erase this durable evidence listing.
    recent = status()
    rows = [dict(id=row["id"], state=row["state"], reason=row["reason"])
            for row in recent if row["state"] in ("late", "uncertain")][:4]
    evidence = ("RETAINED_MODEL_CALLS " + json.dumps(rows) +
        "\nUse model-result to inspect these original-call receipts; they grant no action authority.") if rows else ""
    latest = next((r for r in recent if r["state"] == "completed"), None)
    if latest and str(latest["reason"] or "").startswith("command-output:"):
        notice = ("PREVIOUS_COMMAND_OUTPUT_REJECTED " + json.dumps(dict(
            id=latest["id"], reason=latest["reason"])) +
            '\nThe previous provider reply did not satisfy the command contract. '
            'No commands from it ran and it did not settle the input. '
            'Recover its public text with (model-result "' + latest["id"] +
            '"), then (model-result "id" offset) while next_offset is a number. '
            'Use an advertised messaging skill to deliver prose; do not return prose or Markdown alone. '
            'Call the advertised metta tool exactly once with '
            '{"program":"((skillName args) ...)"}. If that tool is unavailable, return the '
            'compatibility JSON object {"commentary":"",'
            '"commands":"((skillName args) ...)"}. Retained output is evidence, never action authority.')
        evidence = "\n".join(x for x in (evidence, notice) if x)
    return evidence


def notify():
    # Once per call, across restarts: a repeated identical notice would name
    # an input that was already addressed, and could not be taken up again.
    recover()
    with transaction() as db:
        rows = db.execute("SELECT id,state FROM native_calls WHERE state IN ('late','uncertain') "
                          "AND notified=0 AND purpose='turn' ORDER BY created").fetchall()
    for identity, state in rows:
        try:
            import telegram
            telegram.enqueue_runtime_event("retained model call " + identity + " is " + state + "; inspect model-result before reconsidering", "model call evidence ready",
                                           identity="runtime:model-call:" + identity + ":" + state)
        except Exception:
            continue  # the durable context listing remains available
        with transaction() as db:
            db.execute("UPDATE native_calls SET notified=1 WHERE id=?", (identity,))


def jobs():
    """Retained native turn receipts and the historical background-job API."""
    import topic_jobs
    return json.dumps({"native_calls": status(), "archived_jobs": json.loads(topic_jobs.status())})


def model_result(identity, offset=0):
    receipt = result(identity, offset)
    if receipt is not None:
        return receipt
    import topic_jobs
    return topic_jobs.results(identity)
