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
        CREATE INDEX IF NOT EXISTS task_parent ON task_frames(parent);
        CREATE INDEX IF NOT EXISTS task_proposal_request ON task_proposals(request);
        CREATE INDEX IF NOT EXISTS events_capture
            ON events(json_extract(event,'$.serial'),sequence)
            WHERE json_extract(event,'$.kind')='capture';
        CREATE INDEX IF NOT EXISTS events_deliver
            ON events(json_extract(event,'$.request'),sequence)
            WHERE json_extract(event,'$.kind')='deliver';
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
            prior = db.execute("SELECT status FROM requests WHERE id=?", (previous,)).fetchone()
            # A correction received while a task awaits a reply still belongs
            # to that task. An ordinary new message is a new frame; otherwise
            # one forgotten active request turns the whole future into a
            # single unbounded task family.
            if prior and prior[0] == "waiting-user":
                parent = previous
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
    marks = ",".join("?" for _ in members) if members is not None else "SELECT id FROM requests"
    parameters = members if members is not None else ()
    proposals = {r["serial"]: dict(r) for r in db.execute(
        "SELECT * FROM task_proposals WHERE request IN (" + marks + ")", parameters)}
    actions = {r["serial"]: dict(r) for r in db.execute(
        "SELECT * FROM actions WHERE request IN (" + marks + ") ORDER BY rowid", parameters)}
    # Older journals already have complete admitted proposals and attempts.
    for serial, action in actions.items():
        if serial not in proposals:
            command = json.loads(action["command"])
            commands = command.get("commands", []) if isinstance(command, dict) else [command]
            proposals[serial] = dict(serial=serial, request=action["request"],
                                     commands=encoded(commands), disposition="legacy")
    attempts_exist = db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone()
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
                    "disposition": "uncertain", "result": "Interrupted; outcome unknown. This call was not automatically replayed."}
            else:
                if proposed["disposition"] == "legacy":
                    continue
                result = {"disposition": "not_run", "result": proposed["disposition"]}
            pairs.append(dict(id=identity, request=proposed["request"], command=command,
                              **result))
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
    if str(identity).startswith("task-input:"):
        request = str(identity)[len("task-input:"):]
        with store.transaction() as db:
            row = db.execute("SELECT content FROM requests WHERE id=?", (request,)).fetchone()
        if row is None:
            raise ValueError("unknown input identity")
        return _page(identity, _input(request, row[0]), offset, size)
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
    return "No resolution bookkeeping is required."


def projection(store, request, trigger="continuation-tick"):
    if str(request).startswith("conversation:"):
        with store.transaction() as db:
            if request != "conversation:" + store.get(db, "owner"):
                raise ValueError("conversation belongs to another ledger")
            focus = store.get(db, "context_focus")
        return conversation_projection(store, focus, trigger)
    with store.transaction() as db:
        root = _root(db, request)
        members = _members(db, root)
        marks = ",".join("?" for _ in members)
        requests = [dict(r) for r in db.execute("""SELECT r.id,r.content,r.status,c.kind,c.note
            FROM requests r JOIN request_context c ON c.request=r.id
            WHERE r.id IN (""" + marks + ") ORDER BY r.rowid", members)]
        notes = [dict(r) for r in db.execute("SELECT * FROM task_notes WHERE request IN (" + marks + ") ORDER BY sequence", members)]
        pairs = _pairs(store, db, members)
    return dict(root=root, focus=request, trigger=str(trigger), requests=requests,
                notes=notes, transcript=pairs, archive=None)


def _input(request, content):
    return dict(id="task-input:" + request, request=request, command=None,
                disposition="input", result=content)


def _conversation_pairs(store, db):
    # The existing delivery/capture events give causal order. No second event
    # journal or change to task ancestry is needed.
    delivered, captured = {}, {}
    last = 0
    for row in db.execute("SELECT sequence,event FROM events ORDER BY sequence"):
        last = row[0]
        event = json.loads(row[1])
        if event.get("kind") == "deliver":
            delivered.setdefault(event["request"], row[0])
        elif event.get("kind") == "capture":
            captured.setdefault(event["serial"], row[0])
    ordered = [(delivered.get(r["id"], 0), 0, n, _input(r["id"], r["content"]))
               for n, r in enumerate(db.execute("SELECT id,content FROM requests ORDER BY rowid"))]
    for pair in _pairs(store, db, None):
        _, serial, position = pair["id"].rsplit(":", 2)
        ordered.append((captured.get(int(serial), last + int(serial) + 1),
                        1, int(position), pair))
    return [row[3] for row in sorted(ordered, key=lambda row: row[:3])]


def conversation_projection(store, request, trigger="continuation-tick"):
    """The conversation spans frames; focus does not change its evidence set.

    Task ancestry and task-specific retrieval remain separate. The native
    working view additionally pushes its saved offset and result excerpts
    into SQL, so it need not reload archived result bodies on every turn.
    """
    with store.transaction() as db:
        root = "conversation:" + store.get(db, "owner")
        task_root = _root(db, request)
        requests = [dict(r) for r in db.execute("""SELECT r.id,r.content,r.status,c.kind,c.note
            FROM requests r JOIN request_context c ON c.request=r.id
            WHERE r.id IN (SELECT id FROM requests) ORDER BY r.rowid""")]
        notes = [dict(r) for r in db.execute("""SELECT * FROM task_notes
            WHERE request IN (SELECT id FROM requests) ORDER BY sequence""")]
        pairs = _conversation_pairs(store, db)
    return dict(root=root, task_root=task_root, focus=request, trigger=str(trigger),
                requests=requests, notes=notes, transcript=pairs, archive=None, offset=0)


def compact(view, byte_budget=None, *, measure=None):
    """Fit recent whole pairs; older evidence remains addressable on disk.

    The caller supplies a token estimator in production. Byte length remains
    the default for offline consumers of the original API.
    """
    measure = measure or (lambda text: len(text.encode()))
    if byte_budget is None:
        return view
    budget = max(0, int(byte_budget))
    result = json.loads(encoded(view))
    pairs = result.pop("transcript")
    result.update(transcript=[], archive=None)
    def archive(count):
        return dict(count=count, retrieve=["task-history", view["root"], 0]) if count else None
    # Prefer current request/ancestry, notes and live continuations. Completed
    # sibling conversations are recoverable but need not fill every next turn.
    if measure(encoded(result)) > budget // 2:
        originals = result["requests"]
        result["requests"] = [r for r in originals if r["id"] in (view["root"],view["focus"])]
        result["frame"] = dict(compacted=True, retrieve=["task-read", "task-frame:"+view["root"], 0],
                               requests=len(originals), notes=len(result["notes"]))
        # Long pasted inputs are paged visibly, retaining both their beginning
        # and newest end. The full input is never overwritten.
        allowance = max(256, budget // max(4, 2*len(result["requests"])))
        for item in result["requests"]:
            text = item["content"]
            while measure(text) > allowance and len(text) > 512:
                text = text[:len(text)//4] + "\n[excerpt; full request via request-read]\n" + text[-len(text)//4:]
            if text != item["content"]:
                item.update(content=text, truncated=True, retrieve=["request-read", item["id"], 0])
        while result["notes"] and measure(encoded(result)) > budget // 2:
            result["notes"].pop(0)
    chosen=[]
    for index in range(len(pairs)-1,-1,-1):
        candidate = [pairs[index]] + chosen
        result.update(transcript=candidate, archive=archive(index))
        if measure(encoded(result)) <= budget:
            chosen=candidate
            continue
        # The newest oversized output has an explicit pointer and preview;
        # an oversized older output does not displace newer evidence.
        if not chosen:
            pair=pairs[index]
            text=str(pair.get("result", ""))
            stub=dict(id=pair["id"], command=pair["command"], disposition=pair["disposition"],
                      compacted=True, retrieve=["task-read",pair["id"],0])
            stub["result_preview"]=text[:2000] + ("\n[output excerpt]\n" + text[-2000:] if len(text)>4000 else text[2000:])
            result["transcript"]=[stub]
            if measure(encoded(result)) <= budget:
                chosen=[stub]
        result.update(transcript=chosen, archive=archive(len(pairs)-len(chosen)))
        break
    return result


def index_view(view, reason):
    result=compact(view,4096)
    result["context_note"]=str(reason)
    return result


def history(store, request, offset=0, count=20):
    """An index for recovering pairs omitted by compaction."""
    offset, count = int(offset), int(count)
    if offset < 0 or count < 0:
        raise ValueError("invalid history offset")
    view = projection(store, request)
    pairs = view["transcript"]
    selected = pairs[offset:] if count == 0 else pairs[offset:offset+count]
    entries = [dict(id=p["id"], command=p["command"], disposition=p["disposition"],
                    retrieve=["task-read", p["id"], 0]) for p in selected]
    return dict(root=view["root"], entries=entries, total=len(pairs),
                next=offset+len(entries) if offset+len(entries) < len(pairs) else None)
