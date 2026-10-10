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


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
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
            """)
            # Existing input identities were created at the delivery boundary,
            # not by a task checker. Migration changes their kind, not outcome.
            db.execute("""INSERT OR IGNORE INTO request_context(request,kind)
                SELECT id, CASE WHEN id LIKE 'input:%' THEN 'message'
                    WHEN id LIKE 'goal:%' THEN 'goal'
                    WHEN id='autonomous' THEN 'autonomous' ELSE 'task' END FROM requests""")
            for key, value in {"owner": uuid.uuid4().hex, "revision": 0,
                               "mode": "godelclaw", "paused": False,
                               "next_sample": 0, "pending": None,
                               "cooldown": 0, "allowance": None,
                               "next_wake": None, "wake_loops": 0}.items():
                db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, encoded(value)))
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
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
        with self.transaction() as db:
            old = db.execute("SELECT content,checker FROM requests WHERE id=?", (key,)).fetchone()
            if old:
                if old["content"] != str(content) or old["checker"] != checker:
                    raise ValueError("request identity reused for different intent")
                return False
            db.execute("INSERT INTO requests VALUES (?,?,?,?)", (key, str(content), "active", checker))
            db.execute("INSERT INTO request_context(request,kind) VALUES (?,?)", (key, kind))
            self.advance(db)
            self.event(db, "deliver", request=key)
            return True

    def control(self, *, mode=None, paused=None, router_revision=None):
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

    def policy(self, *, allowance=None, cooldown=None):
        with self.transaction() as db:
            if allowance is not None:
                self.put(db, "allowance", max(0, int(allowance)))
            if cooldown is not None:
                self.put(db, "cooldown", max(self.get(db, "cooldown"), cooldown))

    def restore(self):
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
        row = db.execute("SELECT r.status,c.kind FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
        return bool(row and (row[0] == "active" or
                            (row[0] == "processed" and row[1] in ("message", "notification"))))

    def processed(self, key):
        """A durable input handover settles delivery, not the requested work."""
        with self.transaction() as db:
            row = db.execute("SELECT kind,processed FROM request_context WHERE request=?", (key,)).fetchone()
            if not row or (row[0] not in ("message", "notification") and not key.startswith("input:")) or row[1]:
                return False
            db.execute("UPDATE request_context SET processed=1 WHERE request=?", (key,))
            if row[0] in ("message", "notification"):
                db.execute("UPDATE requests SET status='processed' WHERE id=? AND status='active'", (key,))
            self.event(db, "input-processed", request=key)
            return True

    def disposition(self, key, disposition, note):
        """Agent workflow decisions never produce checker-certified achievement."""
        statuses = {"keep": "active", "waiting-user": "waiting-user",
                    "defer": "deferred", "addressed": "addressed", "reopen": "active"}
        if disposition not in statuses or not isinstance(note, str) or not 1 <= len(note) <= 800:
            raise ValueError("use keep, waiting-user, defer, addressed or reopen with a short reason")
        with self.transaction() as db:
            row = db.execute("SELECT r.status,r.checker,c.kind,c.processed FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
            if not row or row[0] in ("achieved", "cancelled") or row[2] in ("goal", "autonomous"):
                return False
            if disposition == "addressed" and row[1] != "operator":
                return False  # exact-output and other configured checks remain authoritative
            if disposition == "addressed" and row[2] in ("message", "notification") and not row[3]:
                return False
            kind = "task" if disposition == "keep" else row[2]
            db.execute("UPDATE requests SET status=? WHERE id=?", (statuses[disposition], key))
            db.execute("UPDATE request_context SET kind=?,note=? WHERE request=?", (kind, note, key))
            self.advance(db)
            self.event(db, "request-disposition", request=key, disposition=disposition, note=note,
                       certified_achievement=False)
            return True

    def request_page(self, offset=0, limit=8):
        offset, limit = max(0, int(offset)), max(1, min(20, int(limit)))
        with self.transaction() as db:
            rows = [dict(r) for r in db.execute("""SELECT r.id,r.status,r.checker,c.kind,c.processed,
                substr(r.content,1,180) AS summary,c.note FROM requests r JOIN request_context c ON c.request=r.id
                ORDER BY r.rowid DESC LIMIT ? OFFSET ?""", (limit, offset))]
            total = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        return dict(records=rows, total=total, next_offset=offset + len(rows) if offset + len(rows) < total else None)

    def request_record(self, key, offset=0):
        offset = max(0, int(offset))
        with self.transaction() as db:
            row = db.execute("SELECT r.*,c.kind,c.processed,c.note FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id=?", (key,)).fetchone()
        if not row:
            return dict(id=key, status="unknown")
        result = dict(row); content = result.pop("content")
        result.update(content=content[offset:offset+4000], content_digest=digest(content), offset=offset,
                      next_offset=offset+4000 if offset+4000 < len(content) else None)
        return result

    def protected(self, focus, result_chars=6000):
        with self.transaction() as db:
            active = [{"id": r["id"], "summary": r["content"][:180],
                       "content_digest": digest(r["content"]), "checker": r["checker"]}
                      for r in db.execute("SELECT * FROM requests WHERE status='active' ORDER BY (id=?) DESC,rowid DESC LIMIT 8", (focus,))]
            row = db.execute("SELECT content FROM requests WHERE id=?", (focus,)).fetchone()
            counts = dict(db.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
            results = []
            for item in db.execute("""SELECT ticket,receipt FROM actions
                    WHERE receipt IS NOT NULL AND
                    (observed=0 OR ticket=(SELECT ticket FROM actions
                        WHERE request=? AND receipt IS NOT NULL ORDER BY rowid DESC LIMIT 1))
                    ORDER BY rowid LIMIT 2""", (focus,)):
                receipt = json.loads(item["receipt"])
                payload = str(receipt["payload"])
                results.append({**receipt, "payload": payload[:result_chars],
                                "payload_digest": digest(payload),
                                "truncated": len(payload) > result_chars})
            uncertain = [r[0] for r in db.execute("SELECT ticket FROM actions WHERE status IN ('issued','uncertain') ORDER BY rowid LIMIT 8")]
            return {"owner": self.get(db, "owner"), "focus": focus,
                    "request": row[0][:4000] if row else "", "unfinished": active,
                    "counts": counts, "omitted_active": max(0, counts.get("active", 0) - len(active)),
                    "request_truncated": bool(row and len(row[0]) > 4000),
                    "results": results, "uncertain": uncertain}

    def active(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM requests WHERE status='active' ORDER BY rowid")]

    def recover_recorded_actions(self):
        """Recover only batches whose admitted commands all have durable results."""
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
        with self.transaction() as db:
            candidates = list(db.execute("SELECT requests.id,requests.checker,actions.ticket FROM requests JOIN actions ON requests.id=actions.request WHERE requests.status='active' AND actions.observed=1 AND actions.receipt IS NOT NULL AND requests.checker LIKE 'sha256:%'"))
        count = 0
        for candidate in candidates:
            expected = candidate["checker"].removeprefix("sha256:")
            checker = lambda work, receipt: digest(str(receipt["payload"])) == expected
            if self.attest(candidate["id"], candidate["ticket"], checker):
                count += self.finish(candidate["id"], candidate["ticket"], {candidate["checker"]: checker})
        return count

    def observe(self, tickets, presented):
        """The exact correlated result projection must be in the sent request."""
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

    def capture(self, focus, boundary, router, now, guard="", wake_seconds=600, goal_lease=None):
        with self.transaction() as db:
            available = self.selectable(db, focus)
            busy = db.execute("SELECT 1 FROM actions WHERE status IN ('issued','uncertain') OR (receipt IS NOT NULL AND observed=0) LIMIT 1").fetchone()
            allowance = self.get(db, "allowance")
            if (not available or self.get(db, "paused")
                    or self.get(db, "pending") is not None or busy
                    or (allowance is not None and allowance <= 0)
                    or now < self.get(db, "cooldown")):
                return None
            serial = self.get(db, "next_sample")
            goal_charge = None
            if goal_lease is not None:
                import fair_goals
                goal_charge = fair_goals.charge_capture(self, db, goal_lease, focus, serial, now)
                if goal_charge is None:
                    return None
            snap = {"serial": serial, "revision": self.get(db, "revision"),
                    "mode": self.get(db, "mode"), "owner": self.get(db, "owner"),
                    "focus": focus, "boundary_digest": digest(boundary),
                    "router": router, "guard": guard}
            if goal_charge is not None:
                snap["goal"] = goal_charge
            db.execute("INSERT INTO samples VALUES (?,?,'pending')", (serial, encoded(snap)))
            self.put(db, "next_sample", serial + 1)
            self.put(db, "pending", serial)
            if allowance is not None:
                self.put(db, "allowance", allowance - 1)
            self.put(db, "next_wake", now + max(0, wake_seconds))
            self.event(db, "capture", serial=serial, focus=focus,
                       boundary_digest=digest(boundary), router=router)
            return snap

    def decide(self, serial, response, now, guard="", retry_after=1):
        with self.transaction() as db:
            if self.get(db, "pending") != serial:
                return "ignored"
            row = db.execute("SELECT snapshot FROM samples WHERE serial=?", (serial,)).fetchone()
            snap = json.loads(row[0])
            current = (snap["revision"] == self.get(db, "revision")
                       and snap["mode"] == self.get(db, "mode")
                       and not self.get(db, "paused") and snap["guard"] == guard)
            self.put(db, "pending", None)
            kind = response.get("kind", "text") if current else "stale"
            if kind == "command":
                router = snap["router"]
                if (response.get("request") != snap["focus"]
                        or response.get("owner") != snap["owner"]
                        or response.get("tool") not in router["executable"]
                        or response.get("tool") not in router["permitted"]):
                    kind = "denied"
                else:
                    ticket = "%s:%s" % (snap["owner"], serial)
                    db.execute("INSERT INTO actions(ticket,request,serial,tool,command,status) VALUES (?,?,?,?,?,'issued')",
                               (ticket, snap["focus"], serial, response["tool"], encoded(response.get("command"))))
            if kind == "failure":
                self.put(db, "cooldown", max(self.get(db, "cooldown"), now + max(1, retry_after)))
            db.execute("UPDATE samples SET status=? WHERE serial=?", (kind, serial))
            self.event(db, "reply", serial=serial, disposition=kind)
            return ticket if kind == "command" else kind

    def report(self, ticket, payload, disposition="returned", domain="unknown"):
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
        import fair_goals
        import time
        now = time.time() if now is None else now
        with self.transaction() as db:
            work = db.execute("SELECT * FROM requests WHERE id=? AND status='active'", (request,)).fetchone()
            action = db.execute("SELECT * FROM actions WHERE ticket=? AND request=?", (ticket, request)).fetchone()
            busy = db.execute("SELECT 1 FROM actions WHERE status IN ('issued','uncertain') OR (receipt IS NOT NULL AND observed=0) LIMIT 1").fetchone()
            if (not work or not action or not action["observed"] or busy or self.get(db, "pending") is not None
                    or not fair_goals.may_complete(self, db, request, now)):
                return False
            receipt = json.loads(action["receipt"])
            checker = checkers.get(work["checker"])
            if (receipt["disposition"] != "returned" or receipt["domain"] != "success"
                    or checker is None or not checker(dict(work), receipt)):
                return False
            db.execute("UPDATE requests SET status='achieved' WHERE id=?", (request,))
            fair_goals.completed(self, db, request, now)
            self.event(db, "finish", request=request, ticket=ticket)
            return True

    def cancel(self, request, operator_authorized=False):
        if not operator_authorized:
            return False
        with self.transaction() as db:
            changed = db.execute("UPDATE requests SET status='cancelled' WHERE id=? AND status='active'", (request,)).rowcount
            if changed:
                import fair_goals
                import time
                fair_goals.cancelled(self, db, request, time.time())
                self.event(db, "cancel", request=request)
            return bool(changed)

    def compact(self, keep=0):
        # Replaceable presentation history is separate from requests, issued
        # actions and receipts. Logical history remains queryable in SQLite.
        with self.transaction() as db:
            self.event(db, "compact", keep=max(0, int(keep)))

    def attest(self, request, ticket, checker):
        """A trusted task oracle may establish domain success for its receipt."""
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

    def wake(self, now):
        with self.transaction() as db:
            deadline = self.get(db, "next_wake")
            if deadline is not None and now > deadline:
                self.put(db, "wake_loops", 2)
                self.put(db, "next_wake", None)
                self.event(db, "wake", now=now)
                return 2
            return self.get(db, "wake_loops")

    def projection(self):
        with self.transaction() as db:
            return {"meta": {r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM meta")},
                    "requests": [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY rowid")],
                    "actions": [dict(r) for r in db.execute("SELECT * FROM actions ORDER BY rowid")],
                    "events": [json.loads(r[0]) for r in db.execute("SELECT event FROM events ORDER BY sequence")]}


def search_observation(scope, revision, report):
    if (report.get("scope") != scope or report.get("revision") != revision
            or not report.get("finished")):
        return "unavailable"
    if report.get("hits"):
        return "known"
    return "absent" if report.get("complete_coverage") else "unavailable"
