"""Bounded, idempotently keyed receipt notices from the control responder.

A notice does not consume input, complete work, or make a provider request.
The service owns the input and the actual delivery receipts.
"""

import hashlib
import json
import os
import time
import agent_checkpoint


TEXT = "Received. Your message is queued; /activity shows whether I'm waiting, retrying, or resting."
_pending = {}
_last_notice = {}
_limit = 256
_last_pump = 0.0


def _store():
    if not (os.environ.get("METTACLAW_STRATEGY_PATH") or
            os.environ.get("METTACLAW_ENGINE_STATE_PATH")):
        return None
    store = agent_checkpoint.ledger()
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS input_notices (
            task TEXT PRIMARY KEY, update_id TEXT NOT NULL, lane TEXT NOT NULL,
            send_key TEXT UNIQUE NOT NULL, created REAL NOT NULL,
            message_id INTEGER, processed INTEGER NOT NULL DEFAULT 0,
            delete_key TEXT, state TEXT NOT NULL DEFAULT 'registered', checked REAL NOT NULL DEFAULT 0)""")
        if "checked" not in {row[1] for row in db.execute("PRAGMA table_info(input_notices)")}:
            db.execute("ALTER TABLE input_notices ADD COLUMN checked REAL NOT NULL DEFAULT 0")
        db.execute("CREATE TABLE IF NOT EXISTS processed_inputs (id TEXT PRIMARY KEY, processed REAL NOT NULL)")
    return store


def processed_update_ids(ids):
    """Called only after successful cognition, saved history and durable input ack."""
    values = [str(value) for value in ids if str(value)]
    if not values:
        return 1
    store = _store()
    if store is None:
        return 1
    with store.transaction() as db:
        for identity in values:
            db.execute("INSERT OR IGNORE INTO processed_inputs VALUES (?,?)", (identity, time.time()))
            db.execute("UPDATE input_notices SET processed=1 WHERE update_id=?", (identity,))
        # Telegram cannot delete notices older than 48 hours. Keep metadata
        # beyond that interval to reconcile delayed acknowledgements/restarts.
        db.execute("DELETE FROM processed_inputs WHERE processed<?", (time.time() - 604800,))
    return 1


def text():
    return TEXT


def begin_scan(tasks):
    active = {str(task[0]) for task in tasks
              if isinstance(task, (list, tuple)) and len(task) == 2}
    for task in list(_pending):
        if task not in active:
            del _pending[task]
    lanes = {entry[2] for entry in _pending.values()}
    for lane in list(_last_notice):
        if lane not in lanes:
            del _last_notice[lane]
    return 1


def key(task, lane, kind, role, update="{}"):
    if (os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1"
            or str(kind) != "message" or str(role) != "operator"):
        return ""
    try:
        delay = float(os.environ.get("METTACLAW_INPUT_ACK_SECONDS", "15"))
    except ValueError:
        delay = 15
    if not 0 < delay < float("inf"):
        return ""
    task, lane = str(task), str(lane)
    if not task or not lane or len(task) > 128 or len(lane) > 128:
        return ""
    import attention
    attention.ingress(task, role, update)
    value = json.loads(str(update)) if isinstance(update, str) else update
    update_id = str(value.get("update_id", "")) if isinstance(value, dict) else ""
    store = _store()
    if store is not None:
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM processed_inputs WHERE id=?", (update_id,)).fetchone():
                return ""
    if task not in _pending:
        if len(_pending) >= _limit:
            return ""
        _pending[task] = [time.monotonic(), False, lane]
    since, submitted, captured_lane = _pending[task]
    now = time.monotonic()
    if (submitted or lane != captured_lane or now - since < delay
            or now - _last_notice.get(lane, -float("inf")) < 60):
        return ""
    # Lost submission replies and restarts retain the same key and immutable
    # bytes. The service reconciles duplicates and enforces lane permissions.
    identity = os.environ.get("METTACLAW_INSTANCE", "default") + ":input-receipt:" + task
    number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    send_key = lane + ".%020d" % number
    if store is not None:
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM processed_inputs WHERE id=?", (update_id,)).fetchone():
                return ""
            old = db.execute("SELECT * FROM input_notices WHERE task=?", (task,)).fetchone()
            if old and (old["lane"] != lane or old["send_key"] != send_key or old["processed"]):
                return ""
            count = db.execute("SELECT COUNT(*) FROM input_notices WHERE state IN ('registered','submitted')").fetchone()[0]
            if not old and count >= _limit:
                return ""
            db.execute("INSERT OR IGNORE INTO input_notices(task,update_id,lane,send_key,created) VALUES (?,?,?,?,?)",
                       (task, update_id, lane, send_key, time.time()))
    return send_key


def submitted(task):
    entry = _pending.get(str(task))
    if entry is not None:
        entry[1] = True
        _last_notice[entry[2]] = time.monotonic()
    store = _store()
    if store is not None:
        with store.transaction() as db:
            db.execute("UPDATE input_notices SET state='submitted' WHERE task=? AND state='registered'", (str(task),))
    return 1


def receipt(observation):
    """Save a correlated channel delivery before any reader acknowledges it."""
    value = json.loads(str(observation)) if isinstance(observation, str) else observation
    if (not isinstance(value, list) or len(value) != 5 or value[0] != "delivery"
            or value[2] != 0 or value[3] != 1):
        return 0
    store = _store()
    if store is None:
        return 0
    key, outcome = value[1], value[4]
    with store.transaction() as db:
        row = db.execute("SELECT * FROM input_notices WHERE send_key=? OR delete_key=?", (key, key)).fetchone()
        if not row:
            return 0
        if (not isinstance(outcome, list) or len(outcome) != 2 or outcome[0] != "delivered"
                or type(outcome[1]) is not int):
            return 1  # known failed/uncertain evidence grants no deletion
        if key == row["send_key"] and outcome[1] > 0:
            if row["message_id"] is not None and row["message_id"] != outcome[1]:
                return 0
            db.execute("UPDATE input_notices SET message_id=? WHERE task=?", (outcome[1], row["task"]))
        elif key == row["delete_key"] and row["processed"] and outcome[1] in (0, row["message_id"]):
            db.execute("UPDATE input_notices SET state='deleted' WHERE task=?", (row["task"],))
        else:
            return 0
    return 1


def pump(channel=None):
    """Reconcile owned delivery tasks and keyed deletion intents independently."""
    global _last_pump
    now = time.monotonic()
    if channel is None and now - _last_pump < 2:
        return 0
    _last_pump = now
    store = _store()
    if store is None:
        return 0
    import durable_telegram as protocol
    if channel is None:
        path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET")
        if not path:
            return 0
        channel = protocol.Channel(path, os.devnull, timeout=1)
    def collect():
        for task, observation in channel.pending(64):
            if receipt(observation):
                channel.acknowledge(task)
    count = 0
    try:
        collect()
        with store.transaction() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM input_notices WHERE state IN ('registered','submitted') ORDER BY processed DESC,checked,created LIMIT 32")]
        for row in rows:
            with store.transaction() as db:
                db.execute("UPDATE input_notices SET checked=? WHERE task=?", (time.time(), row["task"]))
            if time.time() - row["created"] > 172800:
                with store.transaction() as db:
                    db.execute("UPDATE input_notices SET state='expired' WHERE task=?", (row["task"],))
                continue
            if row["message_id"] is None or not row["processed"]:
                continue
            identity = "notice-delete:" + row["send_key"]
            number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
            delete_key = row["lane"] + ".%020d" % number
            with store.transaction() as db:
                db.execute("UPDATE input_notices SET delete_key=? WHERE task=?", (delete_key, row["task"]))
            code, _, _ = channel.rpc(protocol.SUBMIT, delete_key,
                                      json.dumps([["delete", row["message_id"]]], separators=(",", ":")))
            if code == protocol.STORED:
                count += 1
        collect()
    except (OSError, ValueError, protocol.ChannelError):
        pass  # durable intentions and unacknowledged receipt tasks remain
    return count
