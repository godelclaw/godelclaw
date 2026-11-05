"""Agent-owned topic notes and read access to historical model results."""
import json
import sqlite3
import re
import time
import agent_checkpoint
import observed_strategy as strategy

def _native():
    """With the native ledger the topics and the historical jobs are its records (src/input_ledger.metta)."""
    import input_ack
    return bool(input_ack._native_socket())


def _call(verb, arguments=()):
    import input_ack
    return input_ack._ledger_call(verb, tuple(arguments))


def _nonce():
    import uuid
    return uuid.uuid4().hex


def _native_topics():
    from execution_gateway.cetta_worker import S
    answer = _call("topics")
    if not (isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("ledger:topics")):
        raise RuntimeError("input ledger topics: unexpected answer %r" % (answer,))
    return answer[1], [dict(name=row[0], revision=row[1]) for row in answer[2]]


def _store():
    store = agent_checkpoint.ledger()
    with store.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS topic_contexts (name TEXT PRIMARY KEY, revision INTEGER NOT NULL, notes TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS model_jobs (id TEXT PRIMARY KEY,
            topic TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
            question TEXT NOT NULL, state TEXT NOT NULL, result TEXT, observed INTEGER NOT NULL DEFAULT 0,
            adopted INTEGER NOT NULL DEFAULT 0, boot TEXT NOT NULL, ticket TEXT, notified INTEGER NOT NULL DEFAULT 0)""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(model_jobs)")}
        if "finished_at" not in columns:
            # Historical results have no invented completion timestamp.
            db.execute("ALTER TABLE model_jobs ADD COLUMN finished_at REAL")
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES ('conversation',0,'')")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('active_topic','\"conversation\"')")
    return store


def _name(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("topic name must be 1-64 letters, digits, dots, underscores or hyphens")
    return value


def select(name):
    name = _name(name)
    if _native():
        _call("topic-select", (name, _nonce()))
        return "active topic: " + name
    store = _store()
    with store.transaction() as db:
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES (?,0,'')", (name,))
        store.put(db, "active_topic", name)
    return "active topic: " + name


def note(text):
    text = str(text)
    if _native():
        name = _call("topic-note", (text, _nonce()))
        return "saved topic notes: " + (name if isinstance(name, str) else _native_topics()[0])
    store = _store()
    with store.transaction() as db:
        name = store.get(db, "active_topic")
        db.execute("UPDATE topic_contexts SET revision=revision+1,notes=? WHERE name=?", (text, name))
    return "saved topic notes: " + name


def topics():
    if _native():
        active, rows = _native_topics()
        return strategy.encoded({"active": active, "topics": rows})
    store = _store()
    with store.transaction() as db:
        return strategy.encoded({"active": store.get(db, "active_topic"), "topics": [dict(row) for row in db.execute("SELECT name,revision FROM topic_contexts ORDER BY name")]})


def foreground_revision():
    if _native():
        active, rows = _native_topics()
        return next(([row["name"], row["revision"]] for row in rows if row["name"] == active), None)
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        return list(db.execute("SELECT name,revision FROM topic_contexts WHERE name=?", (active,)).fetchone())


def context():
    if _native():
        row = _call("topic-context")
        return ("TOPIC_NOTES " + strategy.encoded(dict(name=row[0], notes=row[1]))
                if isinstance(row, tuple) and len(row) == 2 and row[1] else "")
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        row = db.execute("SELECT name,notes FROM topic_contexts WHERE name=?", (active,)).fetchone()
    return "TOPIC_NOTES " + strategy.encoded(dict(row)) if row and row["notes"] else ""


def results(job):
    if _native():
        row = _call("model-job", (str(job),))
        return (strategy.encoded(dict(id=row[0], topic=row[1], state=row[2], result=row[3]))
                if isinstance(row, tuple) and len(row) == 4 else "unknown historical model job")
    with _store().transaction() as db:
        row = db.execute("SELECT id,topic,state,result FROM model_jobs WHERE id=?",(str(job),)).fetchone()
    return strategy.encoded(dict(row)) if row else "unknown historical model job"


def status():
    if _native():
        return strategy.encoded([dict(id=row[0], topic=row[1], state=row[2]) for row in _call("model-jobs")])
    with _store().transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT id,topic,state FROM model_jobs ORDER BY rowid DESC")]
    return strategy.encoded(rows)


def activity():
    return "use model-jobs for archived results"


def notify(store=None):
    return 0
