"""Agent-owned topic notes and read access to historical model results."""
import json
import sqlite3
import re
import time
import agent_checkpoint
import observed_strategy as strategy

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
    store = _store()
    with store.transaction() as db:
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES (?,0,'')", (name,))
        store.put(db, "active_topic", name)
    return "active topic: " + name


def note(text):
    text = str(text)
    store = _store()
    with store.transaction() as db:
        name = store.get(db, "active_topic")
        db.execute("UPDATE topic_contexts SET revision=revision+1,notes=? WHERE name=?", (text, name))
    return "saved topic notes: " + name


def topics():
    store = _store()
    with store.transaction() as db:
        return strategy.encoded({"active": store.get(db, "active_topic"), "topics": [dict(row) for row in db.execute("SELECT name,revision FROM topic_contexts ORDER BY name")]})


def foreground_revision():
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        return list(db.execute("SELECT name,revision FROM topic_contexts WHERE name=?", (active,)).fetchone())


def context():
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        row = db.execute("SELECT name,notes FROM topic_contexts WHERE name=?", (active,)).fetchone()
    return "TOPIC_NOTES " + strategy.encoded(dict(row)) if row and row["notes"] else ""


def results(job):
    with _store().transaction() as db:
        row = db.execute("SELECT id,topic,state,result FROM model_jobs WHERE id=?",(str(job),)).fetchone()
    return strategy.encoded(dict(row)) if row else "unknown historical model job"


def status():
    with _store().transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT id,topic,state FROM model_jobs ORDER BY rowid DESC")]
    return strategy.encoded(rows)


def activity():
    return "use model-jobs for archived results"


def notify(store=None):
    return 0
