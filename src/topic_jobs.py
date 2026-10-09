"""Owned topic contexts and bounded, read-only concurrent model strands.

Each worker is a separate CeTTa process. Its native HTTP transport shares the
agent's admission ledger and service allowance. Results never execute tools.
They return to their captured topic and can be adopted once after foreground
observation and a version/authority check. Restart preserves uncertainty.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid
import agent_checkpoint
import observed_strategy as strategy

_boot = uuid.uuid4().hex
_recovered = False
_lock = threading.RLock()
_workers = {}


def _store():
    store = agent_checkpoint.ledger()
    with store.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS topic_contexts (name TEXT PRIMARY KEY, revision INTEGER NOT NULL, notes TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS model_jobs (id TEXT PRIMARY KEY,
            topic TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
            question TEXT NOT NULL, state TEXT NOT NULL, result TEXT, observed INTEGER NOT NULL DEFAULT 0,
            adopted INTEGER NOT NULL DEFAULT 0, boot TEXT NOT NULL, ticket TEXT, notified INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES ('conversation',0,'')")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('active_topic','\"conversation\"')")
    return store


def recover():
    global _recovered
    with _lock:
        if _recovered:
            return
        store = _store()
        with store.transaction() as db:
            db.execute("UPDATE model_jobs SET state='uncertain' WHERE state IN ('queued','running') AND boot<>?", (_boot,))
            db.execute("UPDATE model_jobs SET notified=0 WHERE boot<>? AND state NOT IN ('queued','running','retired')", (_boot,))
        _recovered = True


def _name(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("topic name must be 1-64 letters, digits, dots, underscores or hyphens")
    return value


def select(name):
    name = _name(name)
    store = _store()
    with store.transaction() as db:
        count = db.execute("SELECT COUNT(*) FROM topic_contexts").fetchone()[0]
        if count >= 64 and not db.execute("SELECT 1 FROM topic_contexts WHERE name=?", (name,)).fetchone():
            return "topic limit reached"
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES (?,0,'')", (name,))
        store.put(db, "active_topic", name)
    return "active topic: " + name


def context():
    recover()
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        topic = dict(db.execute("SELECT * FROM topic_contexts WHERE name=?", (active,)).fetchone())
        jobs = [dict(row) for row in db.execute("SELECT id,topic,state,observed,adopted FROM model_jobs ORDER BY rowid DESC LIMIT 12")]
    return "TOPIC_WORKSPACE " + strategy.encoded({"active": topic, "strands": jobs}) + (
        "\nNew operator input remains reachable regardless of selected topic. "
        "Background strands return evidence only; use model-result before model-adopt. "
        "A stale answer may be read but cannot overwrite current notes. "
        "Declare shared-topic dependencies in the background question as a JSON list via background-model's third argument.")


def note(text):
    text = str(text)
    if len(text.encode()) > 16384:
        return "topic-note refused: notes exceed 16384 bytes"
    store = _store()
    with store.transaction() as db:
        name = store.get(db, "active_topic")
        db.execute("UPDATE topic_contexts SET revision=revision+1,notes=? WHERE name=?", (text, name))
    return "saved topic notes: " + name


def start(topic, question, dependencies="[]"):
    import strategy_host as host
    if not host.enabled() or os.environ.get("METTACLAW_ACTIVE_ENGINE") != "cetta":
        return "background-model requires managed CeTTa"
    recover()
    topic, question = _name(topic), str(question)
    names = json.loads(str(dependencies))
    if (not isinstance(names, list) or len(names) > 16 or
            any(not isinstance(name, str) for name in names) or not question.strip() or len(question.encode()) > 32768):
        return "background-model refused: invalid question or dependencies"
    names = [_name(name) for name in names]
    binary = os.environ.get("CETTA_BIN")
    if not binary or not os.path.isfile(binary):
        return "background-model refused: CeTTa binary is unavailable"
    store = _store()
    job = uuid.uuid4().hex
    captured_guard = host.guard(include_topic=False)
    with store.transaction() as db:
        # One background slot by default leaves the foreground responsive.
        maximum = max(0, min(8, int(os.environ.get("METTACLAW_BACKGROUND_MODELS", "1"))))
        occupied = db.execute("SELECT COUNT(*) FROM model_jobs WHERE state IN ('queued','running','uncertain','returned','failed') AND adopted=0").fetchone()[0]
        if occupied >= maximum:
            return "background-model deferred: retained-call capacity; inspect or retire a model job"
        if not host._focus or not host._ticket:
            return "background-model refused: no brokered request owner"
        if db.execute("SELECT 1 FROM model_jobs WHERE topic=? AND state IN ('queued','running','uncertain')", (topic,)).fetchone():
            return "background-model deferred: this topic already has a pending call"
        if (db.execute("SELECT COUNT(*) FROM topic_contexts").fetchone()[0] >= 64
                and not db.execute("SELECT 1 FROM topic_contexts WHERE name=?", (topic,)).fetchone()):
            return "background-model refused: topic limit reached"
        db.execute("INSERT OR IGNORE INTO topic_contexts VALUES (?,0,'')", (topic,))
        context = dict(db.execute("SELECT * FROM topic_contexts WHERE name=?", (topic,)).fetchone())
        dependency_views = []
        for name in sorted(set(names)):
            row = db.execute("SELECT * FROM topic_contexts WHERE name=?", (name,)).fetchone()
            if not row:
                return "background-model refused: unknown dependency " + name
            dependency_views.append(dict(row))
        snapshot = {"owner": store.get(db, "owner"), "focus": host._focus,
                    "control_guard": captured_guard, "dependencies": dependency_views,
                    "notes": context["notes"], "broker_ticket": host._ticket,
                    "peers": [dict(row) for row in db.execute(
                        "SELECT id,topic,state FROM model_jobs WHERE state IN ('queued','running','uncertain') ORDER BY rowid DESC LIMIT 8")],
                    "goal": host._sample.get("goal") if host._sample else None}
        db.execute("INSERT INTO model_jobs(id,topic,revision,snapshot,question,state,boot) VALUES (?,?,?,?,?,'queued',?)",
                   (job, topic, context["revision"], strategy.encoded(snapshot), question, _boot))
        store.event(db, "model-fork", id=job, topic=topic, revision=context["revision"])
    environment = dict(os.environ, METTACLAW_TOPIC_WORKER_ID=job)
    import llm_policy
    environment["METTACLAW_LLM_LEDGER_PATH"] = str(llm_policy.ledger_path())
    environment.pop("METTACLAW_MODE_RUNTIME", None)
    # Workers cannot write the foreground health/completion observations.
    environment["METTACLAW_COGNITIVE_HEALTH_PATH"] = os.devnull
    worker = Path(__file__).with_name("topic_worker.metta")
    try:
        process = subprocess.Popen([binary, "--lang", "petta", "--import-mode", "ancestor-walk", "--quiet", str(worker)],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        with store.transaction() as db:
            db.execute("UPDATE model_jobs SET state='failed',result=? WHERE id=?", ("worker-start-failed", job))
        return "model job " + job + " failed to start; no provider was called"
    with _lock:
        _workers[job] = process
    threading.Thread(target=_watch, args=(job, process), name="model-job-" + job[:8], daemon=True).start()
    return "model job " + job + " started for topic " + topic + "; foreground work can continue"


def provider_admitted(job, ticket):
    """Shared service charge; called after provider budget admission, before POST."""
    import lifecycle
    import strategy_host as host
    if not lifecycle.cognition_enabled():
        raise RuntimeError("cognition is suspended")
    store = _store()
    live_guard = host.guard(include_topic=False)
    with store.transaction() as db:
        row = db.execute("SELECT * FROM model_jobs WHERE id=?", (str(job),)).fetchone()
        if not row or row["state"] != "queued" or not ticket:
            raise RuntimeError("model job has no submission authority")
        snapshot = json.loads(row["snapshot"])
        if snapshot["control_guard"] != live_guard or snapshot["owner"] != store.get(db, "owner"):
            raise RuntimeError("model context or control changed before admission")
        for view in [{"name": row["topic"], "revision": row["revision"]}] + snapshot["dependencies"]:
            current = db.execute("SELECT revision FROM topic_contexts WHERE name=?", (view["name"],)).fetchone()
            if not current or current[0] != view["revision"]:
                raise RuntimeError("model dependency changed before admission")
        allowance = store.get(db, "allowance")
        if store.get(db, "paused") or (allowance is not None and allowance <= 0) or time.time() < store.get(db, "cooldown"):
            raise RuntimeError("model job admission is unavailable")
        active = db.execute("SELECT status FROM requests WHERE id=?", (snapshot["focus"],)).fetchone()
        if not active or active[0] != "active":
            raise RuntimeError("model job owner is no longer active")
        if allowance is not None:
            store.put(db, "allowance", allowance - 1)
        goal = snapshot.get("goal")
        if goal:
            import fair_goals
            account = next((value for value in fair_goals.accounts(db, time.time()) if value["id"] == goal["id"] and value["round"] == goal["round"]), None)
            if not account or account["blocked"] or account["finished"]:
                raise RuntimeError("goal changed before background admission")
            account["service"] += 1
            fair_goals.save(db, account)
        db.execute("UPDATE model_jobs SET state='running',ticket=? WHERE id=?", (ticket, job))
        store.event(db, "model-admit", id=job, ticket=ticket, focus=snapshot["focus"])


def record(job, state, result):
    if state not in ("returned", "failed") or len(str(result).encode()) > 65536:
        raise ValueError("invalid model result")
    store = _store()
    with store.transaction() as db:
        count = db.execute("UPDATE model_jobs SET state=?,result=? WHERE id=? AND state IN ('queued','running','uncertain')",
                           (state, str(result), str(job))).rowcount
        if count:
            store.event(db, "model-result", id=str(job), state=state, digest=strategy.digest(str(result)))
    return count


def _watch(job, process):
    process.wait()
    store = _store()
    with store.transaction() as db:
        db.execute("UPDATE model_jobs SET state='uncertain',result='worker ended without a terminal receipt; do not replay the POST' WHERE id=? AND state IN ('queued','running')", (job,))
    with _lock:
        _workers.pop(job, None)
    notify()


def notify():
    store = _store()
    with store.transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT id,topic,state FROM model_jobs WHERE state NOT IN ('queued','running','retired') AND notified=0")]
    for row in rows:
        try:
            import telegram
            telegram.enqueue_runtime_event("model job " + row["id"] + " for topic " + row["topic"] + " is " + row["state"] + "; use model-result to observe it", "model result ready")
        except Exception:
            continue
        with store.transaction() as db:
            db.execute("UPDATE model_jobs SET notified=1 WHERE id=?", (row["id"],))


def _evidence(row):
    value = {k: row[k] for k in ("id", "topic", "revision", "state", "result", "adopted")}
    full = str(row["result"] or "")
    value.update(result_digest=strategy.digest(full), result_chars=len(full), truncated=False)
    # Keep the entire evidence view inside the protected receipt projection,
    # including its escaped representation in the command return record.
    while len(strategy.encoded(value)) > 2400:
        value["result"] = str(value["result"] or "")[:len(str(value["result"] or "")) // 2]
        value["truncated"] = True
    return value


def results(job):
    store = _store()
    with store.transaction() as db:
        row = db.execute("SELECT * FROM model_jobs WHERE id=?", (str(job),)).fetchone()
        if not row:
            return "unknown model job"
        if row["state"] in ("queued", "running"):
            return strategy.encoded({"id": row["id"], "topic": row["topic"], "state": row["state"]})
        # Retrieving evidence is distinct from its observation on the next
        # model step. The strategy adapter records presented result digests.
        return strategy.encoded(_evidence(row))


def observe_receipts(presented):
    """Only actual model-result receipts inside the protected prompt count."""
    store = _store()
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone():
            return
        for ticket, projections in presented.items():
            if not any(value.get("ticket") == ticket and value.get("truncated") is False
                       for value in projections):
                continue
            for attempt in db.execute("SELECT command,result FROM command_attempts WHERE ticket=? AND result IS NOT NULL", (ticket,)).fetchall():
                command, result = json.loads(attempt[0]), json.loads(attempt[1])
                if len(command) != 2 or command[0] != "model-result" or result.get("disposition") != "returned":
                    continue
                row = db.execute("SELECT * FROM model_jobs WHERE id=?", (str(command[1]),)).fetchone()
                if not row or row["state"] not in ("returned", "failed", "uncertain"):
                    continue
                evidence = strategy.encoded(_evidence(row))
                if result.get("result") != evidence:
                    continue
                db.execute("UPDATE model_jobs SET observed=1 WHERE id=?", (row["id"],))
                store.event(db, "model-observe", id=row["id"], digest=strategy.digest(evidence))


def status():
    recover()
    store = _store()
    with store.transaction() as db:
        return strategy.encoded([dict(row) for row in db.execute("SELECT id,topic,revision,state,observed,adopted FROM model_jobs ORDER BY rowid DESC LIMIT 20")])


def activity():
    """Read-only control-plane observation; never restore foreground jobs."""
    try:
        with sqlite3.connect(agent_checkpoint.path().resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            rows = db.execute("SELECT topic,state FROM model_jobs WHERE adopted=0 ORDER BY rowid DESC LIMIT 8").fetchall()
        return ",".join(topic + ":" + state for topic, state in rows) or "none"
    except (RuntimeError, sqlite3.Error):
        return "unavailable"


def topics():
    store = _store()
    with store.transaction() as db:
        return strategy.encoded({"active": store.get(db, "active_topic"), "topics": [dict(row) for row in db.execute("SELECT name,revision FROM topic_contexts ORDER BY name")]})


def foreground_revision():
    store = _store()
    with store.transaction() as db:
        active = store.get(db, "active_topic")
        return list(db.execute("SELECT name,revision FROM topic_contexts WHERE name=?", (active,)).fetchone())


def adopt(job):
    import strategy_host as host
    store = _store()
    live_guard = host.guard(include_topic=False)
    with store.transaction() as db:
        row = db.execute("SELECT * FROM model_jobs WHERE id=?", (str(job),)).fetchone()
        if not row or row["state"] != "returned" or not row["observed"] or row["adopted"]:
            return "model-adopt withheld: result must be returned, observed and not previously adopted"
        snapshot = json.loads(row["snapshot"])
        topic = db.execute("SELECT * FROM topic_contexts WHERE name=?", (row["topic"],)).fetchone()
        if snapshot["owner"] != store.get(db, "owner") or snapshot["control_guard"] != live_guard or topic["revision"] != row["revision"]:
            return "model-adopt withheld: stale owner, control or topic; result remains readable"
        for dependency in snapshot["dependencies"]:
            current = db.execute("SELECT revision FROM topic_contexts WHERE name=?", (dependency["name"],)).fetchone()
            if not current or current[0] != dependency["revision"]:
                return "model-adopt withheld: a declared dependency changed"
        evidence = _evidence(row)
        notes = topic["notes"] + "\n[model evidence " + row["id"] + "]\n" + str(evidence["result"] or "")
        if evidence["truncated"]:
            notes += "\n[excerpt; complete result retained under job " + row["id"] + "; digest " + evidence["result_digest"] + "]"
        if len(notes.encode()) > 16384:
            return "model-adopt withheld: topic note capacity; summarize with topic-note after reviewing"
        db.execute("UPDATE topic_contexts SET revision=revision+1,notes=? WHERE name=?", (notes, row["topic"]))
        db.execute("UPDATE model_jobs SET adopted=1 WHERE id=?", (row["id"],))
        store.event(db, "model-adopt", id=row["id"], topic=row["topic"])
    return "adopted model evidence once into topic " + row["topic"]


def retire(job):
    store = _store()
    with store.transaction() as db:
        row = db.execute("SELECT * FROM model_jobs WHERE id=?", (str(job),)).fetchone()
        if not row or not row["observed"] or row["state"] in ("queued", "running"):
            return "model-retire withheld: observe a terminal or uncertain result first"
        db.execute("UPDATE model_jobs SET state='retired',adopted=1 WHERE id=?", (str(job),))
    return "retired model job; evidence and provider accounting remain preserved"
