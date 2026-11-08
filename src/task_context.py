"""Task-scoped projections of the managed ledger, with recoverable compaction.

Requests and command_attempts remain authoritative. These tables only retain
relationships, proposals and agent-authored notes; they never replay an effect
or certify a claim. A compacted pair has an address in the same ledger.
"""
import json

from observed_strategy import encoded, digest


def _native():
    """With METTACLAW_INPUT_LEDGER_SOCKET set the requests are records of the native input ledger
    (Ledger.accept, status_of, request_rows ...), not rows of the SQLite tables."""
    import input_ack
    return bool(input_ack._native_socket())


def current(store):
    return store._native_meta()["context_focus"]


def resume(store, request):
    """Closing an interruption returns to its still-open parent."""
    import input_ack
    return input_ack.task_resume(request)


def begin(store, request, trigger, previous=None, chat="", parent=None):
    import input_ack
    refusal = input_ack.task_begin(request, trigger, previous, chat, parent)
    if refusal:
        raise ValueError(refusal)
    return


def _covered_content(coverings, request, content):
    """input_content from the native coverings: {request: covering}."""
    covering = coverings.get(request)
    return ("[input batch covered by %s; original source via request-read %s]" % (covering, request)
            if covering is not None else content)


def focus(store, request):
    import input_ack
    input_ack.task_focus(request)
    return


def proposal(store, serial, request, commands, disposition):
    import input_ack
    if not input_ack.task_proposal(serial, request, encoded(commands), disposition):
        raise ValueError("proposal identity reused")
    return


def _compose_pairs(owner, proposals, actions, attempts):
    """The call pairs of the proposals and actions: serial -> {serial, request, commands} and serial ->
    {ticket, request, command}, both with JSON texts; attempts ticket -> {position: result JSON or None},
    or None when no attempt was ever recorded."""
    proposals = dict(proposals)
    # Older journals already have complete admitted proposals and attempts.
    for serial, action in actions.items():
        if serial not in proposals:
            command = json.loads(action["command"])
            commands = command.get("commands", []) if isinstance(command, dict) else [command]
            proposals[serial] = dict(serial=serial, request=action["request"],
                                     commands=encoded(commands), disposition="legacy")
    pairs = []
    for serial, proposed in sorted(proposals.items()):
        action = actions.get(serial)
        recorded = {} if not action or attempts is None else attempts.get(action["ticket"], {})
        for position, command in enumerate(json.loads(proposed["commands"])):
            identity = "%s:%s:%s" % (owner, serial, position)
            if position in recorded:
                result = json.loads(recorded[position]) if recorded[position] is not None else {
                    "disposition": "uncertain", "result": "Interrupted; outcome unknown. This call was not automatically replayed."}
            else:
                if proposed["disposition"] == "legacy":
                    continue
                result = {"disposition": "not_run", "result": proposed["disposition"]}
            pairs.append(dict(id=identity, request=proposed["request"], command=command,
                              **result))
    return pairs


def _native_owner(store, view):
    return view["owner"] if view["owner"] is not None else store._owner_candidate()


def _native_pairs(store, view):
    return _compose_pairs(_native_owner(store, view), view["proposals"], view["actions"], view["attempts"])


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
        found = store.native_request(request)
        row = None if found is None else (found["content"],)
        if row is None:
            raise ValueError("unknown input identity")
        return _page(identity, _input(request, row[0]), offset, size)
    import input_ack
    owner, serial, position = str(identity).rsplit(":", 2)
    if owner != store._native_meta()["owner"]:
        raise ValueError("call belongs to another ledger")
    request = input_ack.task_call(int(serial), "%s:%s" % (owner, int(serial)))
    pairs = _native_pairs(store, input_ack.task_view([request])) if request is not None else []
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
    import input_ack
    if not input_ack.task_note(request, str(text), encoded(references)):
        raise ValueError("unknown task")
    return "TASK_NOTE_SAVED: agent interpretation; references do not certify its claims"


def resolve(store, identity, reason):
    return "No resolution bookkeeping is required."


def projection(store, request, trigger="continuation-tick"):
    return _native_projection(store, request, trigger)


def _view_requests(view):
    return [dict(id=r["id"], content=_covered_content(view["coverings"], r["id"], r["content"]),
                 status=r["status"], kind=r["kind"], note=r["note"]) for r in view["requests"]]


def _native_projection(store, request, trigger):
    import input_ack
    if str(request).startswith("conversation:"):
        meta = store._native_meta()
        if request != "conversation:" + meta["owner"]:
            raise ValueError("conversation belongs to another ledger")
        return conversation_projection(store, meta["context_focus"], trigger)
    root, members = input_ack.task_family(request)
    view = input_ack.task_view(members)
    return dict(root=root, focus=request, trigger=str(trigger), requests=_view_requests(view),
                notes=view["notes"], transcript=_native_pairs(store, view), archive=None)


def _ordered(requests, pairs, delivered, captured, last):
    """The conversation's evidence in causal order: each request at its delivery, each call at its capture."""
    ordered = [(delivered.get(r["id"], 0), 0, n, _input(r["id"], r["content"])) for n, r in enumerate(requests)]
    for pair in pairs:
        _, serial, position = pair["id"].rsplit(":", 2)
        ordered.append((captured.get(int(serial), last + int(serial) + 1), 1, int(position), pair))
    return [row[3] for row in sorted(ordered, key=lambda row: row[:3])]


def _input(request, content):
    return dict(id="task-input:" + request, request=request, command=None,
                disposition="input", result=content)


def conversation_projection(store, request, trigger="continuation-tick"):
    """The conversation spans frames; focus does not change its evidence set.

    Task ancestry and task-specific retrieval remain separate. The native
    working view additionally pushes its saved offset and result excerpts
    into SQL, so it need not reload archived result bodies on every turn.
    """
    import input_ack
    view = input_ack.task_view("all")
    if not request:
        raise ValueError("cyclic task ancestry")  # what _root answers for no request
    task_root = input_ack.task_family(request)[0]
    requests = _view_requests(view)
    positions = list(view["arrivals"].values()) + list(view["captures"].values())
    pairs = _ordered(requests, _native_pairs(store, view), view["arrivals"], view["captures"],
                     max(positions) if positions else 0)
    return dict(root="conversation:" + _native_owner(store, view), task_root=task_root, focus=request,
                trigger=str(trigger), requests=requests, notes=view["notes"], transcript=pairs,
                archive=None, offset=0)


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
    newest_stub = None
    if pairs:
        newest = pairs[-1]
        text = str(newest.get("result", ""))
        preview = text if len(text) <= 200 else text[:100] + "\n[output excerpt]\n" + text[-100:]
        newest_stub = dict(id=newest["id"], command=newest["command"],
                           disposition=newest["disposition"], compacted=True,
                           retrieve=["task-read", newest["id"], 0],
                           result_preview=preview)
    # Input and the latest effect observation share the window. Reserve the
    # observation's identity, disposition and retrieval before fitting input.
    input_budget = max(0, budget - (measure(encoded(newest_stub)) + 200 if newest_stub else 0))
    def archive(count):
        return dict(count=count, retrieve=["task-history", view["root"], 0]) if count else None
    # Prefer current request/ancestry, notes and live continuations. Completed
    # sibling conversations are recoverable but need not fill every next turn.
    if measure(encoded(result)) > input_budget // 2:
        originals = result["requests"]
        result["requests"] = [r for r in originals if r["id"] in (view["root"],view["focus"])]
        result["frame"] = dict(compacted=True, retrieve=["task-read", "task-frame:"+view["root"], 0],
                               requests=len(originals), notes=len(result["notes"]))
        # Prefer the full current input to older tool output. Crossing half a
        # window is not a reason to cut a source that fits the actual budget.
        while result["notes"] and measure(encoded(result)) > input_budget:
            result["notes"].pop(0)
        allowance = max(256, input_budget // max(4, 2*len(result["requests"])))
        for item in result["requests"]:
            if measure(encoded(result)) <= input_budget:
                break
            text = item["content"]
            while measure(text) > allowance and len(text) > 512:
                text = text[:len(text)//4] + "\n[excerpt; full request via request-read]\n" + text[-len(text)//4:]
            if text != item["content"]:
                item.update(content=text, truncated=True, retrieve=["request-read", item["id"], 0])
        while result["notes"] and measure(encoded(result)) > input_budget:
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
            text = str(pairs[-1].get("result", ""))
            for width in (2000, 1000, 500, 100):
                preview = text if len(text) <= 2*width else text[:width] + "\n[output excerpt]\n" + text[-width:]
                stub = dict(newest_stub, result_preview=preview)
                result["transcript"]=[stub]
                if measure(encoded(result)) <= budget:
                    chosen=[stub]
                    break
        result.update(transcript=chosen, archive=archive(len(pairs)-len(chosen)))
        break
    if measure(encoded(result)) > budget or (pairs and not result["transcript"]):
        raise ValueError("context budget cannot hold the input references and latest receipt")
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


def index_view(view, reason):
    result=compact(view,4096)
    result["context_note"]=str(reason)
    return result
