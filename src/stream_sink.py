"""Best-effort display transport. No request, command, or acknowledgement API.

Each process appends to its own presentation cache. The authoritative transcript
and receipts remain elsewhere; slow disks or readers never block the producer.
"""
import atexit
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import uuid
from stream_lifecycle import TRANSITIONS

_writers = {}
_lock = threading.Lock()
_item_epochs = {}
KINDS = frozenset(("attempt.open", "attempt.finished", "text.delta", "text.snapshot",
    "text.reconcile", "tool.forming", "tool.dispatching", "tool.started", "tool.output",
    "tool.finished", "plan", "patch", "steering", "session.open", "session.finished",
    "maintenance", "gap"))
TERMINAL = frozenset(("attempt.finished", "tool.finished", "session.finished"))


def control(root):
    try:
        value = json.loads((Path(root) / "control.json").read_text())
        return bool(value["enabled"]), str(value["epoch"])
    except FileNotFoundError:
        return True, "initial"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "invalid"


def process_start(pid):
    try:
        fields=Path("/proc",str(int(pid)),"stat").read_text().rsplit(")",1)[1].split()
        return None if fields[0] in ("Z","X") else fields[19]
    except (OSError,ValueError,IndexError):
        return None


def configured():
    return bool(os.environ.get("METTACLAW_STREAM_DIR", "").strip())


def session_id():
    return os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE") or os.environ.get(
        "METTACLAW_INSTANCE", "agent")


def _safe(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120] or "agent"


class Writer:
    def __init__(self, root, session, capacity=256):
        self.root, self.session = Path(root), session
        self.producer = uuid.uuid4().hex
        self.process_start = process_start(os.getpid())
        self.path = self.root / (_safe(session) + "." + self.producer + ".jsonl")
        self.queue = queue.Queue(maxsize=capacity)
        self.sequence = 0
        self.dropped = 0
        self.failed = False
        self.lock = threading.Lock()
        self.terminals = {}
        self.worker = threading.Thread(target=self._write, daemon=True)
        self.worker.start()

    def emit(self, event):
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            return 0
        # Payloads are already projected. Never serialize a plan or provider
        # response as a fallback on this boundary.
        with self.lock:
            self.sequence += 1
            record = {**event, "version": 1, "session": self.session,
                "instance": os.environ.get("METTACLAW_INSTANCE", "agent"),
                "parent_session": os.environ.get("METTACLAW_STREAM_PARENT", ""),
                "producer": self.producer, "sequence": self.sequence,
                "pid": os.getpid(),
                "process_start": self.process_start,
                "time": time.time()}
            enabled, epoch = control(self.root)
            if not enabled or record.get("epoch", epoch) != epoch:
                return 0
            record["epoch"] = epoch
            if self.dropped:
                record["display_gap"] = self.dropped
            if self.terminals:
                self.dropped += 1
                if record["kind"] in TERMINAL:
                    self.terminals[(record.get("attempt"), record.get("item"))] = record
                return 0
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                self.dropped += 1
                if record["kind"] in TERMINAL:
                    # Only terminal facts have reserved slots. Dropped text is
                    # recoverable from the finalized transcript/output file.
                    self.terminals[(record.get("attempt"), record.get("item"))] = record
                return 0
            self.dropped = 0
            return 1

    def _write(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                while True:
                    event = self.queue.get()
                    batch = [event]
                    try:
                        while len(batch) < 64:
                            batch.append(self.queue.get_nowait())
                    except queue.Empty:
                        pass
                    enabled, epoch = control(self.root)
                    for item in batch:
                        if enabled and item["epoch"] == epoch:
                            output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                    output.flush()
                    for _ in batch:
                        self.queue.task_done()
                    with self.lock:
                        while self.terminals and not self.queue.full():
                            key = next(iter(self.terminals))
                            self.queue.put_nowait(self.terminals.pop(key))
        except (OSError, ValueError, TypeError):
            self.failed = True
            # Drop further observations instead of blocking on a dead writer.
            while True:
                self.queue.get()
                self.queue.task_done()

    def flush(self, seconds=.25):
        deadline = time.monotonic() + seconds
        while (self.queue.unfinished_tasks or self.terminals) and time.monotonic() < deadline:
            time.sleep(.005)
        return not self.queue.unfinished_tasks and not self.terminals


def receipt(identity, command, result, disposition):
    epoch = _item_epochs.pop(str(identity), control(os.environ.get("METTACLAW_STREAM_DIR", ""))[1])
    return emit(dict(kind="tool.finished",item=str(identity),command=str(command),text=str(result),
                     status=str(disposition),source="executor",epoch=epoch))


def writer():
    root = os.environ.get("METTACLAW_STREAM_DIR", "").strip()
    if not root:
        return None
    key = (root, session_id(), os.getpid())
    with _lock:
        if key not in _writers:
            _writers[key] = Writer(root, session_id())
        return _writers[key]


def emit(value):
    try:
        event = json.loads(str(value)) if not isinstance(value, dict) else value
        sink = writer()
        return sink.emit(event) if sink else 0
    except Exception:
        return 0


def event(kind, item="", text="", status="", name="", attempt=""):
    value = dict(kind=str(kind), item=str(item), text=str(text),
        status=str(status), name=str(name), attempt=str(attempt), source="executor")
    if str(kind) in ("tool.dispatching", "tool.started"):
        value["command"], value["text"] = value["text"], ""
        _item_epochs.setdefault(str(item), control(os.environ.get("METTACLAW_STREAM_DIR", ""))[1])
    if str(item) in _item_epochs:
        value["epoch"] = _item_epochs[str(item)]
    return emit(value)


def tool_finished(identity, state, code, artifact):
    try:
        with open(str(artifact), "rb") as source:
            source.seek(0, 2)
            size = source.tell()
            source.seek(max(0, size - 12000))
            tail = source.read(12000).decode("utf-8", "replace")
    except OSError:
        tail = "Output artifact unavailable."
    epoch = _item_epochs.pop(str(identity), control(os.environ.get("METTACLAW_STREAM_DIR", ""))[1])
    return emit(dict(kind="tool.finished", item=str(identity), source="executor", epoch=epoch,
        status=str(state), exit_code=int(code), artifact=str(artifact), text=tail))


def begin(plan):
    if not configured():
        plan.display_enabled = False
        return 0
    plan.display_enabled = True
    plan.display_offset = 0
    plan.display_disabled = False
    plan.display_seen = 0
    plan.display_finished = False
    plan.display_epoch = control(os.environ["METTACLAW_STREAM_DIR"])[1]
    purpose = getattr(plan, "purpose", "turn")
    if purpose == "compaction":
        plan.display_enabled = False
        return emit(dict(kind="maintenance", attempt=plan.correlation,
            item=plan.correlation, text="Compacting task history", status="running"))
    return emit(dict(kind="attempt.open", attempt=plan.correlation,
        item=plan.correlation, purpose=purpose, provider=plan.provider,
        model=getattr(plan, "display_model", ""), epoch=plan.display_epoch, status="awaiting"))


def readable(plan):
    active = (getattr(plan, "display_enabled", False) and
              not getattr(plan, "display_disabled", False))
    if active:
        enabled, epoch = control(os.environ.get("METTACLAW_STREAM_DIR", ""))
        active = enabled and epoch == getattr(plan, "display_epoch", "initial")
    return "true" if active else "false"


def offset(plan):
    return int(getattr(plan, "display_offset", 0))


def advance(plan, next_offset, text):
    expected = offset(plan) + len(str(text).encode("utf-8"))
    if int(next_offset) != expected:
        unavailable(plan)
        return 0
    plan.display_offset = expected
    return 1


def projected(plan, raw):
    try:
        if readable(plan) != "true": return 0
        value = json.loads(str(raw))
        if value.get("channel") not in (None, "commentary", "reasoning-summary"):
            return 0
        if not isinstance(value.get("item"), str) or not value["item"]:
            raise ValueError("missing item identity")
        plan.display_seen += 1
        return emit({**value, "attempt": plan.correlation, "source": "provider",
                     "epoch": plan.display_epoch})
    except Exception:
        return unavailable(plan)


def unavailable(plan, why="incremental observation unavailable"):
    if getattr(plan, "display_disabled", False):
        return 0
    plan.display_disabled = True
    return emit(dict(kind="gap", attempt=plan.correlation,
        text=str(why), source="observer", epoch=getattr(plan,"display_epoch","initial")))


def final(plan, message=None, reason="failed"):
    if getattr(plan,"display_finished",False): return 0
    plan.display_finished = True
    if getattr(plan, "purpose", "") == "compaction":
        return emit(dict(kind="maintenance", attempt=plan.correlation,
            item=plan.correlation, text="Task history compaction", status=str(reason)))
    if not getattr(plan, "display_enabled", False) or control(os.environ.get(
            "METTACLAW_STREAM_DIR", "")) != (True, getattr(plan, "display_epoch", "initial")):
        return 0
    message = message if isinstance(message, dict) else {}
    texts = []
    text = message.get("content")
    if isinstance(text, str):
        texts.append(dict(item="answer", channel="commentary", text=text))
        emit(dict(kind="text.reconcile", attempt=plan.correlation, item="answer",
            channel="commentary", text=text, source="terminal", epoch=plan.display_epoch))
    for i, block in enumerate(message.get("reasoning_details") or []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "reasoning":
            for part, summary in enumerate(block.get("summary") or []):
                if isinstance(summary, dict) and summary.get("type") == "summary_text":
                    texts.append(dict(item=block.get("id") or "reasoning-" + str(i), part=part,
                                      channel="reasoning-summary", text=summary.get("text", "")))
                    emit(dict(kind="text.snapshot", attempt=plan.correlation,
                        item=block.get("id") or "reasoning-" + str(i), part=part,
                        channel="reasoning-summary", text=summary.get("text", ""),
                        source="terminal", epoch=plan.display_epoch))
        elif block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
            texts.append(dict(item=str(i), part=i, channel="reasoning-summary", text=block["thinking"]))
            # Only the readable summary is copied; signatures stay in replay.
            emit(dict(kind="text.snapshot", attempt=plan.correlation,
                item=str(i), part=i, channel="reasoning-summary",
                text=block["thinking"], source="terminal", epoch=plan.display_epoch))
    state = "completed" if reason in ("stop", "tool_calls") else "interrupted"
    return emit(dict(kind="attempt.finished", attempt=plan.correlation,
        status=state, reason=str(reason), source="terminal", epoch=plan.display_epoch,
        texts=texts if state=="completed" else None))


def flush():
    for sink in list(_writers.values()):
        sink.flush()


atexit.register(flush)


class Projection:
    """Read-only display state; bounded text tails, final artifacts stay owned."""
    def __init__(self, tail_chars=12000, max_items=96):
        self.tail_chars = tail_chars
        self.max_items = max_items
        self.seen = {}
        self.sessions = {}

    def apply(self, event):
        producer, sequence = event.get("producer"), event.get("sequence")
        if not isinstance(producer, str) or type(sequence) is not int:
            return False
        if sequence <= self.seen.get(producer, 0):
            return False
        self.seen[producer] = sequence
        sid = event.get("session", "agent")
        session = self.sessions.setdefault(sid, dict(items={}, status="running",
            title=sid, instance=event.get("instance", "Agent"), parent=event.get("parent_session", ""), updated=0))
        session["updated"] = event.get("time", 0)
        session["owner"]=(event.get("pid"),event.get("process_start"))
        kind = event.get("kind")
        if kind == "attempt.open":
            session["status"] = "thinking"
            session["model"] = event.get("model", "")
        elif kind == "attempt.finished":
            session["status"] = "ready" if event.get("status") == "completed" else "interrupted"
        if kind == "session.open":
            session["title"] = event.get("text", sid)
        elif kind == "session.finished":
            session["status"] = event.get("status", "finished")
            session["report"] = event.get("text", "")
        elif kind == "plan":
            session["plan"] = event.get("text", "")
        elif kind in KINDS:
            key = (event.get("attempt", ""), event.get("item", ""),
                   event.get("part"), event.get("channel", ""))
            if kind == "text.reconcile":
                for old_key in list(session["items"]):
                    if old_key[0] == key[0] and old_key[3] == event.get("channel"):
                        del session["items"][old_key]
            item = session["items"].setdefault(key, dict(text="", kind=kind, status="forming", lifecycle="Forming"))
            if item.get("terminal") and kind not in ("text.reconcile", "text.snapshot"):
                return False
            source = event.get("source", "provider")
            transition = {"text.delta": "ProviderText", "tool.output": "ProviderText",
                "tool.forming": "ProviderTool", "tool.started": "ExecutorStart",
                "tool.finished": "ExecutorFinish", "text.snapshot": "TerminalFinish",
                "text.reconcile": "TerminalFinish", "attempt.finished": "TerminalFinish"}.get(kind)
            if kind in ("tool.started", "tool.finished") and source != "executor":
                return False
            if transition:
                item["lifecycle"] = TRANSITIONS[(item["lifecycle"], transition)]
                item["status"] = item["lifecycle"].lower()
            if kind in ("text.delta", "tool.output"):
                item["text"] = (item["text"] + event.get("text", ""))[-self.tail_chars:]
            elif "text" in event:
                item["text"] = event["text"][-self.tail_chars:]
            for field in ("name", "status", "channel", "source", "command", "artifact"):
                if event.get(field):
                    item[field] = event[field]
            item["kind"] = kind
            item["updated"] = event.get("time", 0)
            item["owner"]=session["owner"]
            if kind in TERMINAL:
                item["terminal"] = True
            if kind in ("text.snapshot", "text.reconcile"):
                item["terminal"] = True
            if kind == "attempt.finished":
                if isinstance(event.get("texts"), list):
                    for old_key in list(session["items"]):
                        if old_key[0] == key[0] and old_key[3] in ("commentary", "reasoning-summary"):
                            del session["items"][old_key]
                    for text in event["texts"]:
                        text_key=(key[0],text["item"],text.get("part"),text["channel"])
                        session["items"][text_key]={**text, "text": text["text"][-self.tail_chars:],
                            "kind":"text.snapshot", "lifecycle":"Finished", "terminal":True}
                for old_key, old in session["items"].items():
                    if old_key[0] == key[0] and old.get("channel"):
                        old["status"] = event.get("status", "interrupted")
                        old["terminal"] = True
            while len(session["items"]) > self.max_items:
                del session["items"][next(iter(session["items"]))]
        if kind == "gap":
            session["gap"] = event.get("text", "observation gap")
        if event.get("display_gap"):
            session["gap"] = event["display_gap"]
        return True

    def refresh_owners(self):
        changed=False
        for session in self.sessions.values():
            for item in session["items"].values():
                pid,birth=item.get("owner",(None,None))
                if birth and not item.get("terminal") and process_start(pid)!=birth:
                    item.update(terminal=True,status="interrupted; outcome unknown",lifecycle="Finished")
                    changed=True
            pid,birth=session.get("owner",(None,None))
            if birth and session["status"] not in ("done","failed","stopped","interrupted") and process_start(pid)!=birth:
                session["status"]="interrupted";changed=True
        return changed
