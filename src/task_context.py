"""Task-scoped projections of the managed ledger, with recoverable compaction.

Requests and command_attempts remain authoritative. These tables only retain
relationships, proposals and agent-authored notes; they never replay an effect
or certify a claim. A compacted pair has an address in the same ledger.
"""
import json

from observed_strategy import encoded, digest


def install(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS task_frames (
            request TEXT PRIMARY KEY, parent TEXT, trigger TEXT NOT NULL,
            chat TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS task_proposals (
            serial INTEGER PRIMARY KEY, request TEXT NOT NULL,
            commands TEXT NOT NULL, disposition TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_notes (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, request TEXT NOT NULL,
            text TEXT NOT NULL, sources TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_resolutions (
            call TEXT PRIMARY KEY, reason TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS task_parent ON task_frames(parent);
        CREATE INDEX IF NOT EXISTS task_proposal_request ON task_proposals(request);
        INSERT OR IGNORE INTO meta VALUES ('context_focus','null');
    """)


def current(store):
    with store.transaction() as db:
        return store.get(db, "context_focus")


def resume(store, request):
    """Closing an interruption returns to its still-open parent."""
    with store.transaction() as db:
        seen = set()
        while request and request not in seen:
            seen.add(request)
            if store.selectable(db, request):
                return request
            row = db.execute("SELECT parent FROM task_frames WHERE request=?", (request,)).fetchone()
            request = row[0] if row else None
    return None


def begin(store, request, trigger, previous=None, chat="", parent=None):
    with store.transaction() as db:
        if parent is None and previous != request and previous != "autonomous" and not str(previous).startswith("goal:"):
            parent = previous if previous and store.selectable(db, previous) else None
        if parent == request:
            parent = None
        if parent and not db.execute("SELECT 1 FROM requests WHERE id=?", (parent,)).fetchone():
            raise ValueError("unknown parent task")
        db.execute("INSERT OR IGNORE INTO task_frames VALUES (?,?,?,?)",
                   (request, parent, str(trigger), str(chat or "")))
        store.put(db, "context_focus", request)


def focus(store, request):
    with store.transaction() as db:
        store.put(db, "context_focus", request)


def proposal(store, serial, request, commands, disposition):
    with store.transaction() as db:
        old = db.execute("SELECT request,commands FROM task_proposals WHERE serial=?", (serial,)).fetchone()
        value = encoded(commands)
        if old and tuple(old) != (request, value):
            raise ValueError("proposal identity reused")
        db.execute("""INSERT INTO task_proposals VALUES (?,?,?,?)
            ON CONFLICT(serial) DO UPDATE SET disposition=excluded.disposition""",
                   (serial, request, value, disposition))


def _root(db, request):
    seen = set()
    while request and request not in seen:
        seen.add(request)
        row = db.execute("SELECT parent FROM task_frames WHERE request=?", (request,)).fetchone()
        if not row or not row[0]:
            return request
        request = row[0]
    raise ValueError("cyclic task ancestry")


def _members(db, root):
    return [r[0] for r in db.execute("""WITH RECURSIVE family(id) AS (
        SELECT ? UNION SELECT f.request FROM task_frames f JOIN family ON f.parent=family.id)
        SELECT id FROM family""", (root,))]


def _pairs(store, db, members):
    marks = ",".join("?" for _ in members)
    proposals = {r["serial"]: dict(r) for r in db.execute(
        "SELECT * FROM task_proposals WHERE request IN (" + marks + ")", members)}
    actions = {r["serial"]: dict(r) for r in db.execute(
        "SELECT * FROM actions WHERE request IN (" + marks + ")", members)}
    # Older journals already have complete admitted proposals and attempts.
    for serial, action in actions.items():
        if serial not in proposals:
            command = json.loads(action["command"])
            commands = command.get("commands", []) if isinstance(command, dict) else [command]
            proposals[serial] = dict(serial=serial, request=action["request"],
                                     commands=encoded(commands), disposition="legacy")
    attempts_exist = db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone()
    resolved = dict(db.execute("SELECT call,reason FROM task_resolutions"))
    owner = store.get(db, "owner")
    pairs = []
    for serial, proposed in sorted(proposals.items()):
        action = actions.get(serial)
        attempts = {} if not action or not attempts_exist else {
            r["position"]: dict(r) for r in db.execute(
                "SELECT * FROM command_attempts WHERE ticket=? ORDER BY position", (action["ticket"],))}
        for position, command in enumerate(json.loads(proposed["commands"])):
            identity = "%s:%s:%s" % (owner, serial, position)
            attempt = attempts.get(position)
            if attempt:
                result = json.loads(attempt["result"]) if attempt["result"] is not None else {
                    "disposition": "uncertain", "result": "Reserved; no terminal receipt. Do not replay."}
            else:
                result = {"disposition": "not_run", "result": proposed["disposition"]}
            pairs.append(dict(id=identity, request=proposed["request"], command=command,
                              **result, resolution=resolved.get(identity)))
    return pairs


def read(store, identity, offset=0, size=8192):
    """Read one complete pair in byte-bounded pages; offsets count characters."""
    offset, size = int(offset), int(size)
    if offset < 0 or size < 1:
        raise ValueError("invalid task result page")
    if str(identity).startswith("task-frame:"):
        frame = projection(store, str(identity)[len("task-frame:"):])
        frame.pop("transcript")
        return _page(identity, frame, offset, size)
    with store.transaction() as db:
        owner, serial, position = str(identity).rsplit(":", 2)
        if owner != store.get(db, "owner"):
            raise ValueError("call belongs to another ledger")
        row = db.execute("SELECT request FROM task_proposals WHERE serial=?", (int(serial),)).fetchone()
        if row is None:
            row = db.execute("SELECT request FROM actions WHERE serial=?", (int(serial),)).fetchone()
        pairs = _pairs(store, db, [row[0]]) if row else []
        pair = next((p for p in pairs if p["id"] == identity), None)
        if pair is None:
            raise ValueError("unknown call identity")
    return _page(identity, pair, offset, size)


def _page(identity, pair, offset, size):
    raw = encoded(pair)
    end = min(len(raw), offset + size)
    while end > offset and len(raw[offset:end].encode()) > size:
        end = offset + (end - offset) // 2
    if end == offset and offset < len(raw):
        raise ValueError("page size cannot hold the next UTF-8 character")
    return dict(id=identity, digest=digest(pair), offset=offset, text=raw[offset:end],
                next=end if end < len(raw) else None, characters=len(raw))


def note(store, request, text, sources="[]"):
    references = json.loads(str(sources))
    if not str(text).strip() or not isinstance(references, list) or any(not isinstance(r, str) for r in references):
        raise ValueError("use nonempty notes and a JSON list of call identities")
    for identity in references:
        read(store, identity, size=1)
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM requests WHERE id=?", (request,)).fetchone():
            raise ValueError("unknown task")
        db.execute("INSERT INTO task_notes(request,text,sources) VALUES (?,?,?)",
                   (request, str(text), encoded(references)))
    return "TASK_NOTE_SAVED: agent interpretation; references do not certify its claims"


def resolve(store, identity, reason):
    pair = read(store, identity)
    if not str(reason).strip():
        raise ValueError("a disposition needs a reason")
    with store.transaction() as db:
        db.execute("INSERT OR IGNORE INTO task_resolutions VALUES (?,?)", (identity, str(reason)))
    return "TASK_DECISION_RECORDED: no action was replayed or certified"


def projection(store, request, trigger="continuation-tick"):
    with store.transaction() as db:
        root = _root(db, request)
        members = _members(db, root)
        marks = ",".join("?" for _ in members)
        requests = [dict(r) for r in db.execute("""SELECT r.id,r.content,r.status,c.kind,c.note
            FROM requests r JOIN request_context c ON c.request=r.id
            WHERE r.id IN (""" + marks + ") ORDER BY r.rowid", members)]
        notes = [dict(r) for r in db.execute("SELECT * FROM task_notes WHERE request IN (" + marks + ") ORDER BY sequence", members)]
        waits = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='task_mailbox_waits'").fetchone():
            waits = [dict(r) for r in db.execute("SELECT * FROM task_mailbox_waits WHERE request IN (" + marks + ")",members)]
        pairs = _pairs(store, db, members)
        pending = [dict(id=p["id"], command=p["command"], disposition=p["disposition"])
                   for p in pairs if not p["resolution"] and p["disposition"] in
                   ("not_run", "withheld", "uncertain", "exception", "no_result")]
    return dict(root=root, focus=request, trigger=str(trigger), requests=requests,
                notes=notes, pending=pending, waits=waits, transcript=pairs, archive=None,
                semantics="Calls and results are evidence; notes are agent interpretations. Pending calls need a decision, never automatic replay.")


def compact(view, byte_budget=None):
    """Replace whole older pairs by addresses, keeping their result disposition.

    This is a view only. It does not discard source data or turn an output
    snippet into a claim of success. Notes and requests remain protected.
    """
    view = json.loads(encoded(view))
    if byte_budget is None:
        return view
    size = len(encoded(view).encode())
    if size <= byte_budget:
        return view
    for index, pair in enumerate(view["transcript"]):
        stub = dict(id=pair["id"], request=pair["request"],
            command=pair["command"], disposition=pair["disposition"],
            compacted=True, result_digest=digest(pair),
            retrieve=["task-read", pair["id"], 0], resolution=pair["resolution"])
        size += len(encoded(stub).encode()) - len(encoded(pair).encode())
        view["transcript"][index] = stub
        if size <= byte_budget:
            return view
    # Every omitted pair remains queryable through the task history index.
    pairs = view["transcript"]
    for index, pair in enumerate(pairs[:-1]):
        old = view["archive"]
        view["archive"] = dict(count=index+1, first=pairs[0]["id"], last=pair["id"],
                                retrieve=["task-history", view["root"], 0])
        size -= len(encoded(pair).encode()) + 1
        size += len(encoded(view["archive"]).encode()) - len(encoded(old).encode())
        if size <= byte_budget:
            view["transcript"] = pairs[index+1:]
            return view
    # A pasted request or a large pending index may itself exceed the window.
    # Keep an explicit recoverable frame, rather than crash or silently drop it.
    result = index_view(view,"task frame exceeds available context")
    if len(encoded(result).encode()) > byte_budget:
        raise ValueError("model window cannot hold the task frame; reduce other context sources")
    return result


def index_view(view, reason):
    result = dict(root=view["root"], focus=view["focus"], trigger=view["trigger"],
        requests=[dict(id=r["id"], status=r["status"], content=r["content"][:240],
                      truncated=len(r["content"]) > 240, retrieve=["request-read",r["id"],0])
                  for r in view["requests"] if r["id"] in (view["root"],view["focus"])],
        frame=dict(compacted=True, requests=len(view["requests"]), notes=len(view["notes"]),
                   pending=len(view["pending"]), retrieve=["task-read","task-frame:"+view["root"],0]),
        transcript=[], archive=dict(retrieve=["task-history",view["root"],0]),
        semantics="Task frame is paged. Retrieve its constraints, corrections, notes and pending work before making dependent decisions.")
    result["frame"]["reason"] = str(reason)
    return result


def history(store, request, offset=0, count=20):
    """An index for recovering pairs omitted by compaction."""
    offset = int(offset)
    if offset < 0:
        raise ValueError("invalid history offset")
    view = projection(store, request)
    pairs = view["transcript"]
    entries = [dict(id=p["id"], command=p["command"], disposition=p["disposition"],
                    retrieve=["task-read", p["id"], 0]) for p in pairs[offset:offset+count]]
    return dict(root=view["root"], entries=entries, total=len(pairs),
                next=offset+len(entries) if offset+len(entries) < len(pairs) else None)
