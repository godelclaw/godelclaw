"""SQLite reference queue store and coherent backup helper for differential tests and reverse export."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "uncertain"}


class SqliteConflict(ValueError):
    pass


def snapshot_sqlite(source: str | Path, destination: str | Path) -> Path:
    """Create a coherent SQLite backup snapshot without modifying or renaming the source file."""
    src_path = Path(source).resolve(strict=True)
    dst_path = Path(destination)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    fd = os.open(dst_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    src_uri = f"file:{src_path}?mode=ro"
    src_conn = sqlite3.connect(src_uri, uri=True, timeout=5)
    try:
        dst_conn = sqlite3.connect(dst_path, timeout=5)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    os.chmod(dst_path, 0o600)
    return dst_path


class SqliteStore:
    """Exact original SQLite execution-gateway queue implementation kept as semantic oracle and rollback target."""

    def __init__(self, path, agent, *, conflict_cls=SqliteConflict):
        self.path = Path(path)
        self.agent = agent
        self._conflict_cls = conflict_cls
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owner (agent TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    request TEXT NOT NULL, effect TEXT NOT NULL,
                    state TEXT NOT NULL, cancel INTEGER NOT NULL DEFAULT 0,
                    result TEXT, error TEXT, sequence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL, state TEXT NOT NULL,
                    at REAL NOT NULL, detail TEXT NOT NULL
                );
            """)
            if "outputs" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN outputs TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS gateway_jobs_ready ON jobs(state,sequence)")
            owner = db.execute("SELECT agent FROM owner").fetchone()
            if owner and owner[0] != agent:
                raise self._conflict_cls("queue belongs to another agent")
            db.execute("INSERT OR IGNORE INTO owner VALUES (?)", (agent,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def note(db, request_id, state, detail):
        row = db.execute(
            "INSERT INTO receipts(id,state,at,detail) VALUES (?,?,?,?)",
            (request_id, state, time.time(), json.dumps(detail)),
        )
        return row.lastrowid

    def submit(self, request, fingerprint, effect):
        request_id = request["id"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT fingerprint FROM jobs WHERE id=?", (request_id,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise self._conflict_cls("request ID already names a different operation")
            else:
                seq = self.note(db, request_id, "queued", {})
                db.execute(
                    "INSERT INTO jobs(id,fingerprint,request,effect,state,sequence)"
                    " VALUES (?,?,?,?,?,?)",
                    (request_id, fingerprint, json.dumps(request), effect, "queued", seq),
                )
        return self.get(request_id)

    def get(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (request_id,)).fetchone()
            if row is None:
                return None
            receipts = db.execute(
                "SELECT sequence,state,at,detail FROM receipts WHERE id=? ORDER BY sequence",
                (request_id,),
            ).fetchall()
        return {
            "agent": self.agent,
            "id": request_id,
            "state": row["state"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
            "outputs": json.loads(row["outputs"]) if row["outputs"] else None,
            "receipts": [{**dict(r), "detail": json.loads(r["detail"])} for r in receipts],
        }

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            request = json.loads(row["request"])
            if request["deadline"] <= time.time():
                self.note(db, row["id"], "expired", {"dispatched": False})
                db.execute(
                    "UPDATE jobs SET state='expired',error='deadline-before-dispatch' WHERE id=?",
                    (row["id"],),
                )
                return None
            self.note(db, row["id"], "running", {"dispatch_attempt": 1})
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
        return dict(row) | {"request": request}

    def cancellation_requested(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT cancel FROM jobs WHERE id=?", (request_id,)).fetchone()
            return bool(row and row[0])

    def cancel(self, request_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,cancel FROM jobs WHERE id=?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "queued":
                self.note(db, request_id, "cancelled", {"dispatched": False})
                db.execute(
                    "UPDATE jobs SET state='cancelled',cancel=1 WHERE id=?", (request_id,)
                )
            elif row["state"] == "running" and not row["cancel"]:
                self.note(db, request_id, "cancel-requested", {})
                db.execute("UPDATE jobs SET cancel=1 WHERE id=?", (request_id,))
        return self.get(request_id)

    def finish(self, request_id, state, *, result=None, error=None, outputs=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE id=?", (request_id,)).fetchone()
            if row is None or row[0] != "running":
                raise self._conflict_cls("only a running attempt can finish")
            self.note(db, request_id, state, {"error": error} if error else {})
            db.execute(
                "UPDATE jobs SET state=?,result=?,error=?,outputs=? WHERE id=?",
                (
                    state,
                    json.dumps(result) if result is not None else None,
                    error,
                    json.dumps(outputs) if outputs is not None else None,
                    request_id,
                ),
            )

    def recover(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id,effect FROM jobs WHERE state='running' ORDER BY sequence"
            ).fetchall()
            for row in rows:
                state = "uncertain" if row["effect"] == "write" else "failed"
                self.note(db, row["id"], state, {"error": "gateway-restarted"})
                db.execute(
                    "UPDATE jobs SET state=?,error='gateway-restarted' WHERE id=?",
                    (state, row["id"]),
                )


Conflict = SqliteConflict
Store = SqliteStore
