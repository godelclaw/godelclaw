"""Durable managed-host transitions corresponding to ObservedStrategy.lean.

SQLite is the checkpoint, not the replaceable prompt history. Reservations
precede effects; an interrupted reservation is uncertain and is never replayed.
Completion checkers are host supplied, never inferred from provider prose.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid



def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


class IdentityConflict(ValueError):
    """A retained identity already names different immutable source data."""


class Ledger:
    def __init__(self, path, *, initialize=True, timeout=10):
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self._owner = None
        if not initialize:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL,
                    status TEXT NOT NULL, checker TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    serial INTEGER PRIMARY KEY, snapshot TEXT NOT NULL,
                    status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (
                    ticket TEXT PRIMARY KEY, request TEXT NOT NULL,
                    serial INTEGER NOT NULL, tool TEXT NOT NULL, command TEXT NOT NULL,
                    status TEXT NOT NULL, receipt TEXT, observed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS request_status ON requests(status);
                CREATE INDEX IF NOT EXISTS action_status ON actions(status);
                CREATE INDEX IF NOT EXISTS action_observation ON actions(observed);
                CREATE TABLE IF NOT EXISTS request_context (
                    request TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS processed_inputs (
                    id TEXT PRIMARY KEY, processed REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS request_inputs (
                    request TEXT NOT NULL, input TEXT NOT NULL,
                    PRIMARY KEY(request, input));
                CREATE INDEX IF NOT EXISTS request_input ON request_inputs(input);
            """)
            import task_context
            task_context.install(db)
            # executescript ends an open transaction. Migration of coverage,
            # request states and its marker must commit as one transition.
            db.execute("BEGIN IMMEDIATE")
            # Existing input identities were created at the delivery boundary,
            # not by a task checker. Migration changes their kind, not outcome.
            db.execute("""INSERT OR IGNORE INTO request_context(request,kind)
                SELECT id, CASE WHEN id LIKE 'input:%' THEN 'message'
                    WHEN id LIKE 'goal:%' THEN 'goal'
                    WHEN id='autonomous' THEN 'autonomous' ELSE 'task' END FROM requests""")
            for key, value in {"owner": uuid.uuid4().hex, "revision": 0,
                               "mode": "godelclaw", "paused": False,
                               "next_sample": 0, "pending": None}.items():
                db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, encoded(value)))
            task_context.reconcile_inputs(self, db)
            self._owner = self.get(db, "owner")
        self.path.chmod(0o600)

    def _owner_candidate(self):
        """The owner of this ledger's SQLite meta: the native store takes it at its first capture. A plain
        read, which callers may make inside their own write transaction on this file."""
        if self._owner is None:
            db = sqlite3.connect(self.path, timeout=self.timeout)
            try:
                self._owner = json.loads(db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()[0])
            finally:
                db.close()
        return self._owner

    @staticmethod
    def _native():
        """Whether the requests, turns and actions are records of the native ledger (METTACLAW_INPUT_LEDGER_SOCKET)."""
        import input_ack
        return bool(input_ack._native_socket())

    # The meta keys of a new ledger; on the native store a key never written has this value.
    INITIAL_META = {"revision": 0, "mode": "godelclaw", "paused": False, "next_sample": 0, "pending": None,
                    "context_focus": None}

    def _native_meta(self):
        """The meta keys of the native store as the SQLite meta table holds them (decoded)."""
        import input_ack
        meta = dict(self.INITIAL_META)
        meta.update(input_ack.ledger_state())
        for counter in ("clock", "notes", "attempts"):  # the store's own numbering, not meta keys
            meta.pop(counter, None)
        if "owner" not in meta:  # no turn was captured yet: the owner the first capture takes
            meta["owner"] = self._owner_candidate()
        if "router_revision" in meta:
            meta["router_revision"] = json.loads(meta["router_revision"])
        return meta

    @staticmethod
    def _native_action(ticket, request):
        """SELECT * FROM actions WHERE ticket=? AND request=?, from the native store: a dict or None."""
        import input_ack
        found = [a for a in input_ack.actions(("ticket", ticket)) if a["request"] == request]
        return found[0] if found else None

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=%d" % int(self.timeout * 1000))
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db, key):
        return json.loads(db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])

    @staticmethod
    def put(db, key, value):
        db.execute("UPDATE meta SET value=? WHERE key=?", (encoded(value), key))

    def advance(self, db):
        self.put(db, "revision", self.get(db, "revision") + 1)

    @staticmethod
    def event(db, kind, **fields):
        db.execute("INSERT INTO events(event) VALUES (?)", (encoded({"kind": kind, **fields}),))

    def accept(self, key, content, checker="operator", *, kind=None):
        kind = kind or ("message" if key.startswith("input:") else
                        "goal" if key.startswith("goal:") else
                        "autonomous" if key == "autonomous" else "task")
        if kind not in ("message", "notification", "task", "goal", "autonomous"):
            raise ValueError("unknown request kind")
        import input_ack
        if input_ack._native_socket():
            content = str(content)
            answer = input_ack.request_accept(key, content, checker, kind, content[:180], digest(content))
            if answer == "conflict":
                raise IdentityConflict("request identity reused for different intent")
            return answer == "created"
        with self.transaction() as db:
            old = db.execute("SELECT content,checker FROM requests WHERE id=?", (key,)).fetchone()
            if old:
                if old["content"] != str(content) or old["checker"] != checker:
                    raise IdentityConflict("request identity reused for different intent")
                return False
            db.execute("INSERT INTO requests VALUES (?,?,?,?)", (key, str(content), "active", checker))
            db.execute("INSERT INTO request_context(request,kind) VALUES (?,?)", (key, kind))
            self.advance(db)
            self.event(db, "deliver", request=key)
            return True

    def control(self, *, mode=None, paused=None, router_revision=None):
        if self._native():
            import input_ack
            input_ack.ledger_control(mode, paused, None if router_revision is None else encoded(router_revision))
            return
        with self.transaction() as db:
            changed = False
            for key, value in (("mode", mode), ("paused", paused)):
                if value is not None and value != self.get(db, key):
                    self.put(db, key, value)
                    changed = True
            if router_revision is not None:
                old = db.execute("SELECT value FROM meta WHERE key='router_revision'").fetchone()
                if not old or json.loads(old[0]) != router_revision:
                    db.execute("INSERT OR REPLACE INTO meta VALUES ('router_revision',?)", (encoded(router_revision),))
                    changed = True
            if changed:
                self.advance(db)
                self.event(db, "control", mode=self.get(db, "mode"), paused=self.get(db, "paused"))


    def restore(self):
        if self._native():
            import input_ack
            input_ack.ledger_restore()
            return
        with self.transaction() as db:
            pending = self.get(db, "pending")
            if pending is not None:
                db.execute("UPDATE samples SET status='interrupted' WHERE serial=? AND status='pending'", (pending,))
            self.put(db, "pending", None)
            # The effect may have happened before a crash. Never treat it as
            # unissued, and permit a late, correctly correlated result.
            db.execute("UPDATE actions SET status='uncertain' WHERE status='issued'")
            self.advance(db)
            self.event(db, "restore")

    @staticmethod
    def selectable(db, key):
        import input_ack
        if input_ack._native_socket():
            row = input_ack.request_get(str(key))
            return bool(row and (row["status"] == "active" or
                                 (row["status"] == "processed" and row["kind"] in ("message", "notification"))))
        row = db.execute("SELECT r.status,c.kind FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
        return bool(row and (row[0] == "active" or
                            (row[0] == "processed" and row[1] in ("message", "notification"))))

    def processed(self, key):
        """A durable input handover settles delivery, not the requested work."""
        import input_ack
        if input_ack._native_socket():
            return input_ack.request_settle(str(key), time.time())
        with self.transaction() as db:
            return self._processed(db, key)

    def _processed(self, db, key):
        row = db.execute("SELECT kind,processed FROM request_context WHERE request=?", (key,)).fetchone()
        if not row or (row[0] not in ("message", "notification") and not key.startswith("input:")) or row[1]:
            return False
        db.execute("UPDATE request_context SET processed=1 WHERE request=?", (key,))
        if row[0] in ("message", "notification"):
            db.execute("UPDATE requests SET status='processed' WHERE id=? AND status='active'", (key,))
        self.event(db, "input-processed", request=key)
        return True

    def checkpoint(self, working, input_ids=(), request=None):
        """Commit the successor and its settled input identities atomically.

        Transport acknowledgement follows this commit. After a crash between
        them, the service's still-pending input is acknowledged from this
        journal without presenting it to cognition a second time.

        With METTACLAW_INPUT_LEDGER_SOCKET set the native input ledger owns the whole
        checkpoint: one native transaction stores the working set, the settled
        input ids, their notices' processed flags and the request's processed status
        (the request is a record of the same store: see input_ack.turn_checkpoint).
        Nothing of it is written to SQLite then, and
        an unreachable server raises (LedgerUnavailable, a sqlite3.OperationalError):
        a fallback would split the ledger.
        """
        import input_ack
        if input_ack._native_socket():
            return self._native_checkpoint(working, input_ids, request)
        with self.transaction() as db:
            db.execute("INSERT INTO meta VALUES ('working_set',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encoded(working),))
            for identity in input_ids:
                db.execute("INSERT OR IGNORE INTO processed_inputs VALUES (?,?)", (str(identity), working["saved_at"]))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='input_notices'").fetchone():
                    db.execute("UPDATE input_notices SET processed=1 WHERE update_id=?", (str(identity),))
            if request:
                self._processed(db, request)
            self.event(db, "turn-checkpoint", settled_inputs=len(input_ids))

    def _native_checkpoint(self, working, input_ids, request):
        import input_ack
        ids = [str(identity) for identity in input_ids]
        # The time of the processed records is the working set's own, as in SQLite
        # (a KeyError there too, and nothing written, when ids come without it).
        saved = working["saved_at"] if ids else (working.get("saved_at") if isinstance(working, dict) else None)
        # Whether the request exists and may be settled is decided in the same native transaction.
        input_ack.turn_checkpoint(encoded(working), ids, str(request) if request else "", float(saved or 0.0))

    def working_checkpoint(self):
        import input_ack
        if input_ack._native_socket():
            text = input_ack.working_set()
            return None if text is None else json.loads(text)
        with self.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='working_set'").fetchone()
            return json.loads(row[0]) if row else None

    def link_inputs(self, request, inputs):
        """Record which arrivals a request was formed from."""
        inputs = {str(i) for i in inputs}
        if self._native():
            import input_ack
            if not input_ack.link_inputs(request, inputs):
                raise IdentityConflict("input envelope identity reused for different occurrences")
            return
        with self.transaction() as db:
            prior = {r[0] for r in db.execute("SELECT input FROM request_inputs WHERE request=?", (request,))}
            if request.startswith("input:") and prior and prior != inputs:
                raise IdentityConflict("input envelope identity reused for different occurrences")
            db.executemany("INSERT OR IGNORE INTO request_inputs(request,input) VALUES (?,?)",
                           [(request, str(i)) for i in inputs])

    def requests_with_input(self, input_id):
        if self._native():
            import input_ack
            return input_ack.linked(input_id)
        with self.transaction() as db:
            requests = [r for (r,) in db.execute("SELECT request FROM request_inputs WHERE input=?", (input_id,))]
            return [dict(request=r, inputs=[i for (i,) in db.execute(
                "SELECT input FROM request_inputs WHERE request=? ORDER BY input", (r,))]) for r in requests]

    def disposition(self, key, disposition, note):
        """Agent workflow decisions never produce checker-certified achievement.
        A refusal leaves its reason in self.refusal."""
        statuses = {"keep": "active", "waiting-user": "waiting-user",
                    "defer": "deferred", "addressed": "addressed", "reopen": "active"}
        if disposition not in statuses or not isinstance(note, str) or not 1 <= len(note) <= 800:
            raise ValueError("use keep, waiting-user, defer, addressed or reopen with a short reason")
        import input_ack
        if input_ack._native_socket():
            self.refusal = input_ack.request_disposition(key, disposition, note)
            return self.refusal is None
        with self.transaction() as db:
            row = db.execute("SELECT r.status,r.checker,c.kind,c.processed FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
            self.refusal = (
                "unknown request ID" if not row else
                "the request is already %s" % row[0] if row[0] in ("achieved", "cancelled") else
                "goal cycles are not disposed; retire the goal with goal-drop" if row[2] == "goal" else
                "autonomous work has no disposition" if row[2] == "autonomous" else
                # exact-output and other configured checks remain authoritative
                "this request has its own checker and completes when the check passes"
                if disposition == "addressed" and row[1] != "operator" else
                "a message is addressed only after a turn has processed it"
                if disposition == "addressed" and row[2] in ("message", "notification") and not row[3] else
                None)
            if self.refusal:
                return False
            if row[0] == "covered":
                if disposition not in ("keep", "reopen"):
                    self.refusal = "input batch is covered; reopen it to create independent work"
                    return False
                db.execute("DELETE FROM input_supersessions WHERE request=?", (key,))
            kind = "task" if disposition == "keep" or row[0] == "covered" else row[2]
            db.execute("UPDATE requests SET status=? WHERE id=?", (statuses[disposition], key))
            db.execute("UPDATE request_context SET kind=?,note=? WHERE request=?", (kind, note, key))
            self.advance(db)
            self.event(db, "request-disposition", request=key, disposition=disposition, note=note,
                       certified_achievement=False)
            return True

    def request_page(self, offset=0, limit=8):
        offset, limit = max(0, int(offset)), max(1, int(limit))
        import input_ack
        if input_ack._native_socket():
            total, rows = input_ack.request_page(offset, limit)
            return dict(records=rows, total=total, next_offset=offset + len(rows) if offset + len(rows) < total else None)
        with self.transaction() as db:
            rows = [dict(r) for r in db.execute("""SELECT r.id,r.status,r.checker,c.kind,c.processed,
                substr(r.content,1,180) AS summary,c.note FROM requests r LEFT JOIN request_context c ON c.request=r.id
                ORDER BY r.rowid DESC LIMIT ? OFFSET ?""", (limit, offset))]
            total = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        return dict(records=rows, total=total, next_offset=offset + len(rows) if offset + len(rows) < total else None)

    def request_record(self, key, offset=0):
        offset = max(0, int(offset))
        import input_ack
        if input_ack._native_socket():
            found = input_ack.request_record(key, offset)
            if found is None:
                return dict(id=key, status="unknown")
            return dict(id=found["id"], status=found["status"], checker=found["checker"], kind=found["kind"],
                        processed=found["processed"], note=found["note"], content=found["slice"],
                        content_digest=found["digest"], offset=offset,
                        next_offset=offset+4000 if offset+4000 < found["length"] else None)
        with self.transaction() as db:
            row = db.execute("SELECT r.*,c.kind,c.processed,c.note FROM requests r LEFT JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
        if not row:
            return dict(id=key, status="unknown")
        result = dict(row); content = result.pop("content")
        result.update(content=content[offset:offset+4000], content_digest=digest(content), offset=offset,
                      next_offset=offset+4000 if offset+4000 < len(content) else None)
        return result

    @staticmethod
    def _native_active(request):
        """SELECT * FROM requests WHERE id=? AND status='active', from the native store: a dict or None."""
        import input_ack
        found = input_ack.request_get(str(request), text=True)
        if not found or found["status"] != "active":
            return None
        return dict(id=found["id"], content=found["content"], status=found["status"], checker=found["checker"])

    def status_of(self, key):
        """requests.status of a request, or None when there is none (SELECT status FROM requests WHERE id=?)."""
        import input_ack
        if input_ack._native_socket():
            found = input_ack.request_get(str(key))
            return found["status"] if found else None
        with self.transaction() as db:
            row = db.execute("SELECT status FROM requests WHERE id=?", (key,)).fetchone()
        return row[0] if row else None

    def request_rows(self, ids):
        """id, content, status, kind, note of the requests among `ids` (all of them for None), oldest first:
        the join of requests and request_context the task projections read. Native store only (the
        projections keep their own SQL on the SQLite tables)."""
        import input_ack
        return [dict(id=r["id"], content=r["content"], status=r["status"], kind=r["kind"], note=r["note"])
                for r in input_ack.request_rows(list(ids) if ids is not None else "all")]

    @staticmethod
    def native_request(key):
        """One request whole from the native store (id, kind, checker, status, processed, note, content), or None."""
        import input_ack
        return input_ack.request_get(str(key), text=True)

    def protected(self, focus, result_chars=6000):
        import input_ack
        # The requests come from the native store (one snapshot) when it is configured; the
        # actions and the owner are not requests and stay in SQLite.
        native = input_ack.request_protected(focus) if input_ack._native_socket() else None
        with self.transaction() as db:
            if native is None:
                active = [{"id": r["id"], "summary": r["content"][:180],
                           "content_digest": digest(r["content"]), "checker": r["checker"]}
                          for r in db.execute("SELECT * FROM requests WHERE status='active' ORDER BY (id=?) DESC,rowid DESC LIMIT 8", (focus,))]
                row = db.execute("SELECT content FROM requests WHERE id=?", (focus,)).fetchone()
                request, request_length = (row[0][:4000], len(row[0])) if row else ("", 0)
                counts = dict(db.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
            else:
                active, counts = native["unfinished"], native["counts"]
                request, request_length = native["focus"] if native["focus"] else ("", 0)
            results = []
            for item in (self._native_results(focus) if native is not None else db.execute("""SELECT ticket,receipt FROM actions
                    WHERE receipt IS NOT NULL AND
                    (observed=0 OR ticket=(SELECT ticket FROM actions
                        WHERE request=? AND receipt IS NOT NULL ORDER BY rowid DESC LIMIT 1))
                    ORDER BY rowid LIMIT 2""", (focus,))):
                receipt = json.loads(item["receipt"])
                payload = str(receipt["payload"])
                results.append({**receipt, "payload": payload[:result_chars],
                                "payload_digest": digest(payload),
                                "truncated": len(payload) > result_chars})
            return {"owner": self._native_meta()["owner"] if native is not None else self.get(db, "owner"), "focus": focus,
                    "request": request, "unfinished": active,
                    "counts": counts, "omitted_active": max(0, counts.get("active", 0) - len(active)),
                    "request_truncated": request_length > 4000,
                    "request_index": {"retrieve": ["request-page", 0], "scope": "all retained requests"},
                    "results": results}

    @staticmethod
    def _native_results(focus):
        """The receipts the protected view shows, from the native store: those not yet observed and the
        newest of the focus, oldest first, at most two (as rows with the receipt's JSON text)."""
        import input_ack
        return [{"ticket": a["ticket"], "receipt": encoded(a["receipt"])}
                for a in input_ack.actions(("protected", "" if focus is None else focus))]

    def active(self):
        import input_ack
        if input_ack._native_socket():
            return [dict(id=r["id"], content=r["content"], status=r["status"], checker=r["checker"])
                    for r in input_ack.request_rows("active")]
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM requests WHERE status='active' ORDER BY rowid")]

    def recover_recorded_actions(self):
        """Recover only batches whose admitted commands all have durable results."""
        if self._native():
            import input_ack
            ready = []
            for action in input_ack.actions(("status", "uncertain")):
                proposal = json.loads(action["command"])
                if not isinstance(proposal, dict) or "limit" not in proposal:
                    continue
                commands = proposal["commands"]
                expected = min(len(commands), proposal["limit"])
                attempts = input_ack.attempts(ticket=action["ticket"])
                if (len(attempts) == expected and all(a["result"] is not None for a in attempts)
                        and all(a["position"] == i and json.loads(a["command"]) == commands[i]
                                for i, a in enumerate(attempts))):
                    ready.append((action["ticket"], {"recovered_results": [json.loads(a["result"]) for a in attempts],
                                                     "deferred_commands": commands[expected:]}))
            for ticket, result in ready:
                self.report(ticket, encoded(result))
            return len(ready)
        with self.transaction() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone()
            if not exists:
                return 0
            ready = []
            for action in db.execute("SELECT * FROM actions WHERE status='uncertain'"):
                proposal = json.loads(action["command"])
                if not isinstance(proposal, dict) or "limit" not in proposal:
                    continue
                commands = proposal["commands"]
                expected = min(len(commands), proposal["limit"])
                attempts = list(db.execute("SELECT * FROM command_attempts WHERE ticket=? ORDER BY position", (action["ticket"],)))
                if (len(attempts) == expected and all(a["result"] is not None for a in attempts)
                        and all(a["position"] == i and json.loads(a["command"]) == commands[i]
                                for i, a in enumerate(attempts))):
                    ready.append((action["ticket"], {"recovered_results": [json.loads(a["result"]) for a in attempts],
                                                     "deferred_commands": commands[expected:]}))
        for ticket, result in ready:
            self.report(ticket, encoded(result))
        return len(ready)

    def finish_exact_results(self):
        """Run request-specific, operator-registered exact-output checkers."""
        import input_ack
        if input_ack._native_socket():
            # requests.checker LIKE 'sha256:%' (ASCII case-insensitive) joined with the observed receipts of
            # actions: the requests are read from the native store, the actions stay in SQLite.
            exact = {r["id"]: r["checker"] for r in input_ack.request_rows("active")
                     if r["checker"][:7].isascii() and r["checker"][:7].lower() == "sha256:"}
            found = [a for key in exact for a in input_ack.actions(("request", key))
                     if a["observed"] and a["receipt"] is not None]
            candidates = [dict(id=a["request"], checker=exact[a["request"]], ticket=a["ticket"])
                          for a in sorted(found, key=lambda action: action["serial"])]
            return self._finish_exact(candidates)
        with self.transaction() as db:
            candidates = list(db.execute("SELECT requests.id,requests.checker,actions.ticket FROM requests JOIN actions ON requests.id=actions.request WHERE requests.status='active' AND actions.observed=1 AND actions.receipt IS NOT NULL AND requests.checker LIKE 'sha256:%'"))
        return self._finish_exact(candidates)

    def _finish_exact(self, candidates):
        count = 0
        for candidate in candidates:
            expected = candidate["checker"].removeprefix("sha256:")
            checker = lambda work, receipt: digest(str(receipt["payload"])) == expected
            if self.attest(candidate["id"], candidate["ticket"], checker):
                count += self.finish(candidate["id"], candidate["ticket"], {candidate["checker"]: checker})
        return count

    OBSERVE_CHUNK = 20

    def observe_presented(self, calls):
        """Mark observed the actions an answered prompt presented: `calls` are the (ticket, position) calls it
        carried with their results; an action counts when its receipt exists and every attempt of it was
        carried (lean/pettaclaw/ObservedReceipts.lean). Answers how many it marked."""
        calls = [(str(ticket), int(position)) for ticket, position in calls]
        if not calls:
            return 0
        if self._native():
            import input_ack
            pairs = input_ack.presented_receipts(calls)
            # Small transactions: a transaction's own edits are searched linearly, so its cost grows with
            # the square of its marks; twenty cost well under a second.
            for start in range(0, len(pairs), self.OBSERVE_CHUNK):
                input_ack.action_observe(pairs[start:start + self.OBSERVE_CHUNK])
            return len(pairs)
        carried = {}
        for ticket, position in calls:
            carried.setdefault(ticket, set()).add(position)
        count = 0
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_attempts'").fetchone():
                return 0
            for ticket, positions in carried.items():
                if not db.execute("SELECT 1 FROM actions WHERE ticket=? AND observed=0 AND receipt IS NOT NULL",
                                  (ticket,)).fetchone():
                    continue
                attempts = db.execute("SELECT position, result FROM command_attempts WHERE ticket=?", (ticket,)).fetchall()
                if attempts and all(result is not None and position in positions for position, result in attempts):
                    db.execute("UPDATE actions SET observed=1 WHERE ticket=?", (ticket,))
                    self.event(db, "observe", ticket=ticket)
                    count += 1
        return count

    def observe(self, tickets, presented):
        """The exact correlated result projection must be in the sent request."""
        if self._native():
            # The store compares the presented digest with its receipt's: the same check, in its transaction.
            import input_ack
            pairs = []
            for ticket in tickets:
                if ticket in presented:
                    projection = next((r for r in presented[ticket] if r.get("ticket") == ticket), None)
                    if projection and isinstance(projection.get("payload_digest"), str):
                        pairs.append((ticket, projection["payload_digest"]))
            input_ack.action_observe(pairs)
            return
        with self.transaction() as db:
            for ticket in tickets:
                row = db.execute("SELECT receipt FROM actions WHERE ticket=? AND receipt IS NOT NULL", (ticket,)).fetchone()
                if row and ticket in presented:
                    # Check content as well as correlation; a bare ID cannot
                    # attest that the result projection was presented.
                    projection = next((r for r in presented[ticket] if r.get("ticket") == ticket), None)
                    if projection and projection.get("payload_digest") == digest(str(json.loads(row[0])["payload"])):
                        db.execute("UPDATE actions SET observed=1 WHERE ticket=?", (ticket,))
                        self.event(db, "observe", ticket=ticket)

    def admission_denial(self, focus, now):
        if self._native():
            return "cognition-paused" if self._native_meta()["paused"] else None
        with self.transaction() as db:
            return "cognition-paused" if self.get(db, "paused") else None

    def capture(self, focus, boundary, router, now, guard="", wake_seconds=600, goal_lease=None):
        if self._native():
            import input_ack
            captured = input_ack.turn_capture(focus, digest(boundary), encoded(router), guard, self._owner_candidate())
            if captured is None:
                return None
            serial, revision, mode, owner = captured
            return {"serial": serial, "revision": revision, "mode": mode, "owner": owner,
                    "focus": focus, "boundary_digest": digest(boundary), "router": router, "guard": guard}
        with self.transaction() as db:
            if self.get(db, "paused"):
                return None
            serial = self.get(db, "next_sample")
            snap = {"serial": serial, "revision": self.get(db, "revision"),
                    "mode": self.get(db, "mode"), "owner": self.get(db, "owner"),
                    "focus": focus, "boundary_digest": digest(boundary),
                    "router": router, "guard": guard}
            db.execute("INSERT INTO samples VALUES (?,?,'pending')", (serial, encoded(snap)))
            self.put(db, "next_sample", serial + 1)
            self.put(db, "pending", serial)
            self.event(db, "capture", serial=serial, focus=focus,
                       boundary_digest=digest(boundary), router=router)
            return snap

    def decision_pending(self, serial):
        """Whether this exact captured turn may still be decided."""
        if self._native():
            return self._native_meta()["pending"] == serial
        with self.transaction() as db:
            return self.get(db, "pending") == serial

    def decide(self, serial, response, now, guard="", retry_after=1):
        if self._native():
            import input_ack
            kind = response.get("kind", "text")
            if kind == "command" and "tool" not in response:
                # SQLite reads the tool only for the pending turn, after the check.
                if self._native_meta()["pending"] != serial:
                    return "ignored"
                raise KeyError("tool")
            command = encoded(response.get("command")) if kind == "command" else ""
            return input_ack.turn_decide(serial, kind, response["tool"] if kind == "command" else "", command)
        with self.transaction() as db:
            if self.get(db, "pending") != serial:
                return "ignored"
            row = db.execute("SELECT snapshot FROM samples WHERE serial=?", (serial,)).fetchone()
            snap = json.loads(row[0])
            self.put(db, "pending", None)
            kind = response.get("kind", "text")
            if kind == "command":
                ticket = "%s:%s" % (snap["owner"], serial)
                db.execute("INSERT INTO actions(ticket,request,serial,tool,command,status) VALUES (?,?,?,?,?,'issued')",
                           (ticket, snap["focus"], serial, response["tool"], encoded(response.get("command"))))
            db.execute("UPDATE samples SET status=? WHERE serial=?", (kind, serial))
            self.event(db, "reply", serial=serial, disposition=kind)
            return ticket if kind == "command" else kind

    def report(self, ticket, payload, disposition="returned", domain="unknown"):
        if self._native():
            import input_ack
            return input_ack.action_report(ticket, payload, disposition, domain, digest(str(payload)))
        with self.transaction() as db:
            row = db.execute("SELECT receipt,status FROM actions WHERE ticket=?", (ticket,)).fetchone()
            if not row or row["receipt"] is not None or row["status"] not in ("issued", "uncertain"):
                return False
            receipt = {"ticket": ticket, "payload": str(payload),
                       "disposition": disposition, "domain": domain}
            db.execute("UPDATE actions SET receipt=?,status='returned' WHERE ticket=?", (encoded(receipt), ticket))
            self.advance(db)
            self.event(db, "report", ticket=ticket, disposition=disposition, domain=domain)
            return True

    def finish(self, request, ticket, checkers, *, now=None):
        import time
        now = time.time() if now is None else now
        if self._native():
            # The checker runs here on what the store holds; the store then checks the request, the
            # action and its attested receipt again and moves the request, in one transaction.
            import input_ack
            work = self._native_active(request)
            action = self._native_action(ticket, request)
            if not work or not action or not action["observed"]:
                return False
            receipt = action["receipt"]
            checker = checkers.get(work["checker"])
            if (receipt["disposition"] != "returned" or receipt["domain"] != "success"
                    or checker is None or not checker(dict(work), receipt)):
                return False
            return input_ack.action_finish(request, ticket)
        with self.transaction() as db:
            work = db.execute("SELECT * FROM requests WHERE id=? AND status='active'", (request,)).fetchone()
            action = db.execute("SELECT * FROM actions WHERE ticket=? AND request=?", (ticket, request)).fetchone()
            if (not work or not action or not action["observed"]):
                return False
            receipt = json.loads(action["receipt"])
            checker = checkers.get(work["checker"])
            if (receipt["disposition"] != "returned" or receipt["domain"] != "success"
                    or checker is None or not checker(dict(work), receipt)):
                return False
            db.execute("UPDATE requests SET status='achieved' WHERE id=?", (request,))
            self.event(db, "finish", request=request, ticket=ticket)
            return True

    def retire(self, prefix, note):
        """Close the open cycles of a goal its agent retired; never an achievement."""
        import input_ack
        if input_ack._native_socket():
            return input_ack.request_retire(prefix, note[:800])
        with self.transaction() as db:
            rows = [r[0] for r in db.execute(
                "SELECT id FROM requests WHERE substr(id,1,?)=? AND status IN ('active','deferred','waiting-user')",
                (len(prefix), prefix))]
            for request in rows:
                db.execute("UPDATE requests SET status='cancelled' WHERE id=?", (request,))
                db.execute("UPDATE request_context SET note=? WHERE request=?", (note[:800], request))
                self.event(db, "goal-retire", request=request, note=note[:800], certified_achievement=False)
            if rows:
                self.advance(db)
            return rows

    def cancel(self, request, operator_authorized=False):
        if not operator_authorized:
            return False
        import input_ack
        if input_ack._native_socket():
            return input_ack.request_cancel(request)
        with self.transaction() as db:
            changed = db.execute("UPDATE requests SET status='cancelled' WHERE id=? AND status='active'", (request,)).rowcount
            if changed:
                self.event(db, "cancel", request=request)
            return bool(changed)

    def compact(self, keep=0):
        # Replaceable presentation history is separate from requests, issued
        # actions and receipts. Logical history remains queryable in SQLite.
        if self._native():
            return  # the native store keeps no event journal
        with self.transaction() as db:
            self.event(db, "compact", keep=max(0, int(keep)))

    def attest(self, request, ticket, checker):
        """A trusted task oracle may establish domain success for its receipt."""
        if self._native():
            import input_ack
            work = self._native_active(request)
            action = self._native_action(ticket, request)
            if not work or not action or not action["receipt"] or not action["observed"]:
                return False
            if action["receipt"]["disposition"] != "returned" or not checker(dict(work), dict(action["receipt"])):
                return False
            return input_ack.action_attest(request, ticket)
        with self.transaction() as db:
            work = db.execute("SELECT * FROM requests WHERE id=? AND status='active'", (request,)).fetchone()
            action = db.execute("SELECT receipt,observed FROM actions WHERE ticket=? AND request=?", (ticket, request)).fetchone()
            if not work or not action or not action["receipt"] or not action["observed"]:
                return False
            receipt = json.loads(action["receipt"])
            if receipt["disposition"] != "returned" or not checker(dict(work), receipt):
                return False
            receipt["domain"] = "success"
            db.execute("UPDATE actions SET receipt=? WHERE ticket=?", (encoded(receipt), ticket))
            self.event(db, "attest", request=request, ticket=ticket)
            return True


    def projection(self):
        import input_ack
        native = input_ack.request_rows("all") if input_ack._native_socket() else None
        if native is not None:
            actions = [dict(ticket=a["ticket"], request=a["request"], serial=a["serial"], tool=a["tool"],
                            command=a["command"], status=a["status"],
                            receipt=None if a["receipt"] is None else encoded(a["receipt"]), observed=a["observed"])
                       for a in input_ack.actions("all")]
            return {"meta": self._native_meta(),
                    "requests": [dict(id=r["id"], content=r["content"], status=r["status"], checker=r["checker"])
                                 for r in native],
                    "actions": actions, "events": []}
        with self.transaction() as db:
            return {"meta": {r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM meta")},
                    "requests": [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY rowid")]
                    if native is None else [dict(id=r["id"], content=r["content"], status=r["status"], checker=r["checker"])
                                            for r in native],
                    "actions": [dict(r) for r in db.execute("SELECT * FROM actions ORDER BY rowid")],
                    "events": [json.loads(r[0]) for r in db.execute("SELECT event FROM events ORDER BY sequence")]}


def search_observation(scope, revision, report):
    if (report.get("scope") != scope or report.get("revision") != revision
            or not report.get("finished")):
        return "unavailable"
    if report.get("hits"):
        return "known"
    return "absent" if report.get("complete_coverage") else "unavailable"
