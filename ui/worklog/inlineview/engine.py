"""Append-only commit logic: typed events in, finished blocks out.

State comes from the vendored stream_sink.Projection (sessions, items, owners).
Text items additionally keep a small buffer so they can be committed one
paragraph at a time; tool items are committed once, when they finish.
Nothing printed is ever revised."""
import json
import re
import sys
import time
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.is_dir():            # standalone checkout; inside a release stream_sink is already on the path
    sys.path.insert(0, str(_VENDOR))
from stream_sink import Projection, control  # noqa: E402

from .render import FAILED, OK, PROPOSED, RUNNING, UNCERTAIN, classify
from .textsafe import command_text, one_line, span, truncate

TEXT_KINDS = ("text.delta", "text.snapshot", "text.reconcile")
SKIP_KINDS = ("attempt.open", "attempt.finished", "gap")
FINISHED = ("done", "failed", "stopped", "interrupted", "finished")
FULL_TAIL = 16777216
TAIL = 12000
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ASCII_SPINNER = "|/-\\"
SESSION_RESTAMP = 300       # seconds of quiet after which output is re-anchored by a session line
MAINTENANCE_OK = ("stop", "tool_calls", "finished", "completed", "done")


def order(event):
    return (event.get("time", 0), event.get("producer", ""), event.get("sequence", 0))


class Reader:
    """Same reading semantics as the existing viewer: every *.jsonl in the
    directory, bounded slices per pass, complete lines only, control.json epoch."""
    def __init__(self, root):
        self.root = Path(root)
        self.offsets = {}

    def read(self):
        output = []
        enabled, epoch = control(self.root)
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                stat = path.stat()
                inode, offset = self.offsets.get(path.name, (stat.st_ino, 0))
                if inode != stat.st_ino or offset > stat.st_size:
                    offset = 0
                if offset == stat.st_size:
                    continue
                with path.open("rb") as stream:
                    stream.seek(offset)
                    for _ in range(128):
                        line = stream.readline(16777217)
                        if not line or not line.endswith(b"\n"):
                            break
                        offset = stream.tell()
                        try:
                            event = json.loads(line)
                            if isinstance(event, dict) and enabled and event.get("epoch", "initial") == epoch:
                                output.append(event)
                        except (ValueError, UnicodeError):
                            pass
                self.offsets[path.name] = (stat.st_ino, offset)
            except OSError:
                continue
        return sorted(output, key=order)

    def read_all(self, passes=4096):
        records = []
        for _ in range(passes):
            batch = self.read()
            if not batch:
                break
            records += batch
        return sorted(records, key=order)


def split_ready(buffer):
    """Index just past the last paragraph break outside a code fence (0 if none)."""
    fence, pos, ready = None, 0, 0
    lines = buffer.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped[:3] in ("```", "~~~"):
            marker = stripped[:3]
            fence = None if fence == marker else (fence or marker)
        end = pos + len(line) + 1
        if not fence and not line.strip() and i + 1 < len(lines) and pos > 0:
            ready = min(end, len(buffer))
        pos = end
    return ready


class TextState:
    def __init__(self, title, channel, started, attempt):
        self.title, self.channel, self.started, self.attempt = title, channel, started, attempt
        self.buffer = ""          # received, not yet committed
        self.committed = ""       # raw text already printed
        self.opened = False       # a header has been printed
        self.closed = False


class Engine:
    def __init__(self, renderer, write, selected=None, full=False, now=time.time):
        self.r, self.write, self.selected, self.full, self.now = renderer, write, selected, full, now
        self.proj = Projection(tail_chars=FULL_TAIL if full else TAIL)
        self.muted = False
        self.headers = set()
        self.last_status = {}
        self.last_plan = {}
        self.last_gap = {}
        self.done = set()
        self.texts = {}
        self.exit_codes = {}
        self.last_item = None
        self.flush_running = False
        self.last_sid = None        # session of the last printed block (a switch prints a session line)
        self.last_time = 0
        self.blank = True           # the output so far ends with a blank line
        self.models = {}            # last non-empty model per session (attempt.open sends "" first)
        self.rests = {}             # sid -> (since, seconds or None) after a (rest ...) returned RESTING
        self.reasons = {}           # sid -> reason of the last model attempt that did not complete

    # -- helpers -----------------------------------------------------------
    def reset(self):
        self.__init__(self.r, self.write, self.selected, self.full, self.now)

    def visible(self, sid):
        if not self.selected:
            return True
        s = self.proj.sessions.get(sid, {})
        return sid == self.selected or s.get("parent") == self.selected

    def _out(self, text):
        if text:
            self.write(text)
            self.blank = text.endswith("\n\n") or (self.blank and text == "\n")

    def _emit(self, text, sid=None):
        """Write a block; first say whose it is when that changed, or after a long quiet."""
        if self.muted or not text:
            return
        if sid is not None:
            now = self.proj.sessions[sid].get("updated") or self.now()
            first = sid not in self.headers
            if first or sid != self.last_sid or now - self.last_time > SESSION_RESTAMP:
                self.headers.add(sid)
                if not self.blank:
                    self._out("\n")
                session = dict(self.proj.sessions[sid])
                session["model"] = self.models.get(sid) or session.get("model", "")
                self._out(self.r.session_line(sid, session, first=first, seconds=now))
            self.last_sid, self.last_time = sid, now
        self._out(text)

    # -- ingest ------------------------------------------------------------
    def apply(self, records):
        """Apply records to the projection and commit what became ready."""
        for rec in records:
            if not self.proj.apply(rec):
                continue
            sid = rec.get("session", "agent")
            if isinstance(rec.get("exit_code"), int) and not isinstance(rec.get("exit_code"), bool):
                self.exit_codes[(sid, self._rec_key(rec))] = rec["exit_code"]
            self.muted = False
            self._after(sid, rec)
        self._prune()

    def _rec_key(self, rec):
        return (rec.get("attempt", ""), rec.get("item", ""), rec.get("part"), rec.get("channel", ""))

    def _prune(self):
        """Bound long-lived state (a console can run for days): forget finished text
        buffers beyond the newest few hundred and commit marks of items that left the projection."""
        if len(self.texts) > 400:
            for tid in [t for t, st in self.texts.items() if st.closed][:len(self.texts) - 300]:
                del self.texts[tid]
        if len(self.done) > 4000 or len(self.exit_codes) > 4000:
            live = {(sid, key) for sid, s in self.proj.sessions.items() for key in s["items"]}
            self.done &= live
            self.exit_codes = {k: v for k, v in self.exit_codes.items() if k in live}

    def _track(self, sid, session, kind, key, rec):
        """Facts for the status area that the projection does not keep."""
        if kind == "attempt.open" and rec.get("model"):
            self.models[sid] = str(rec["model"])
        if kind in ("attempt.open", "maintenance") or (kind == "tool.dispatching" and not
                                                       command_text(rec.get("command", "")).startswith("(rest")):
            self.rests.pop(sid, None)
        if kind == "attempt.finished":
            if rec.get("status") == "completed":
                self.reasons.pop(sid, None)
            else:
                self.reasons[sid] = str(rec.get("reason") or rec.get("status") or "")
        if kind == "tool.finished":
            item = session["items"].get(key, {})
            command = command_text(item.get("command") or rec.get("command") or "")
            result = str(rec.get("text", ""))
            if command.startswith("(rest") and result.lstrip().startswith("(RESTING"):
                timed = re.search(r"sleep-interval-seconds (\d+)", result)
                self.rests[sid] = (rec.get("time") or self.now(), int(timed.group(1)) if timed else None)

    def _after(self, sid, rec):
        session = self.proj.sessions[sid]
        if not self.visible(sid):
            self.muted = True
        kind = rec.get("kind")
        key = self._rec_key(rec)
        self._track(sid, session, kind, key, rec)
        if kind in TEXT_KINDS:
            item = session["items"].get(key)
            if item is not None:
                self._text_event(sid, session, key, rec, item)
        elif kind == "attempt.finished":
            self._attempt_finished(sid, session, rec)
        self._sweep(sid)
        if kind == "plan":
            self._plan(sid, session, rec.get("time"))
        if kind == "session.finished":
            took = (session.get("updated") or 0) - (session.get("started") or 0)
            self._emit(self.r.report(session, sid, took if session.get("started") and took > 0 else None), sid)
        if session.get("gap") and session.get("gap") != self.last_gap.get(sid):
            self.last_gap[sid] = session["gap"]
            self._emit(self.r.gap(session["gap"]), sid)
        self._status(sid, session)

    def _status(self, sid, session):
        """Thinking/ready transitions belong to the status line; only an interrupted model
        call is worth a line in scrollback."""
        old = self.last_status.get(sid)
        self.last_status[sid] = session["status"]
        if old is not None and old != session["status"] and session["status"] == "interrupted" and sid in self.headers:
            self._emit(self.r.interrupted(sid, session, self.reasons.get(sid, ""), session.get("call_took"),
                                          session.get("updated")), sid)

    def _plan(self, sid, session, seconds=None):
        if session.get("plan") != self.last_plan.get(sid):
            self.last_plan[sid] = session.get("plan")
            if session.get("plan"):
                self._emit(self.r.plan(session, None), sid)

    # -- text items --------------------------------------------------------
    def _title(self, session, channel, item):
        if channel == "reasoning-summary":
            return "Reasoning summary"
        if channel == "commentary":
            return session.get("instance", "Agent")
        return item.get("name") or item.get("kind", "text").replace(".", " ")

    def _state(self, sid, session, key, item, rec):
        tid = (sid, key)
        st = self.texts.get(tid)
        if st is None:
            st = TextState(self._title(session, key[3], item), key[3], item.get("started") or rec.get("time", 0), key[0])
            self.texts[tid] = st
        return st

    def _text_event(self, sid, session, key, rec, item):
        st = self._state(sid, session, key, item, rec)
        if st.closed:
            return
        kind = rec["kind"]
        if kind == "text.delta":
            self._feed(sid, st, rec.get("text", ""))
        else:
            final = rec.get("text", "")
            self._adopt(sid, key, st, final)
            self._finish_text(sid, st, final, item.get("status"))

    def _adopt(self, sid, key, st, final):
        """A final text replaces streamed items of the same attempt/channel. When it extends
        what was streamed (printed or still buffered), only the rest is printed, once."""
        for tid, other in list(self.texts.items()):
            if tid[0] != sid or tid[1] == key or other.closed:
                continue
            if other.attempt == st.attempt and other.channel == st.channel:
                if not st.committed and (other.committed or other.buffer) and final.startswith(other.committed):
                    st.committed, st.opened, st.started = other.committed, other.opened, other.started
                    other.closed, other.buffer = True, ""
                    if self.last_item == (sid, id(other)):
                        self.last_item = (sid, id(st))
                else:
                    self._finish_text(sid, other, None, None)

    def _feed(self, sid, st, text):
        st.buffer += text
        cut = split_ready(st.buffer)
        if not cut and len(st.buffer) > 2000 and "\n" in st.buffer:
            cut = st.buffer.rfind("\n") + 1
        if cut:
            chunk, st.buffer = st.buffer[:cut], st.buffer[cut:]
            self._commit_text(sid, st, chunk, close=False)

    def _finish_text(self, sid, st, final, status, running=False):
        """Commit what remains. `final` (if given) is the authoritative full text."""
        if st.closed:
            return
        note = None
        if final is None:
            rest = st.buffer
        elif final.startswith(st.committed):
            rest = final[len(st.committed):]
        else:
            rest, note = final, "(final text differs from what was streamed; shown in full)"
        state = RUNNING if running else classify(status, True, text_item=True) if status else OK
        st.closed = True
        st.buffer = ""
        self._commit_text(sid, st, rest, close=True, state=state, note=note)
        st.committed = final if note else st.committed + rest

    def _commit_text(self, sid, st, raw, close, state=None, note=None):
        chunk = raw.strip("\n")
        empty = not chunk.strip()
        if not close:
            st.committed += raw
            if empty:
                return
        elif empty and note is None and state != UNCERTAIN:
            return
        header = (not st.opened) or self.last_item != (sid, id(st))
        out = self.r.text_chunk(st.title, st.channel, st.started, chunk, state=state, header=header and not (empty and st.opened),
                                close=close, cont=st.opened and header, tail_note=note)
        if not self.muted:
            self._emit(out, sid)
            self.last_item = None if close else (sid, id(st))
            st.opened = True

    def _attempt_finished(self, sid, session, rec):
        keys = {(rec.get("attempt", ""), t["item"], t.get("part"), t["channel"])
                for t in (rec.get("texts") or []) if isinstance(t, dict) and "item" in t and "channel" in t}
        for tid, st in list(self.texts.items()):
            if tid[0] != sid or st.attempt != rec.get("attempt", "") or st.closed:
                continue
            if tid[1] in keys:
                continue
            self._finish_text(sid, st, None, rec.get("status", "interrupted"))
        for t in rec.get("texts") or []:
            if not (isinstance(t, dict) and "item" in t and "channel" in t):
                continue
            key = (rec.get("attempt", ""), t["item"], t.get("part"), t["channel"])
            item = session["items"].get(key)
            if item is None:
                continue
            st = self._state(sid, session, key, item, rec)
            if not st.closed:
                self._adopt(sid, key, st, t.get("text", ""))
                self._finish_text(sid, st, t.get("text", ""), rec.get("status", "completed"))

    # -- tool-like items -----------------------------------------------------
    def _is_text(self, key, item):
        return bool(item.get("channel")) or str(item.get("kind", "")).startswith("text.")

    def _sweep(self, sid, flush_running=False):
        session = self.proj.sessions[sid]
        for key, item in list(session["items"].items()):
            tid = (sid, key)
            if tid in self.done or self._is_text(key, item):
                continue
            if item.get("kind") in SKIP_KINDS:
                self.done.add(tid)
                continue
            if item.get("terminal") or flush_running:
                self.done.add(tid)
                self._tool(sid, session, key, item)

    def _tool(self, sid, session, key, item):
        if self.muted:
            return
        code = self.exit_codes.get((sid, key))
        terminal = bool(item.get("terminal"))
        kind = str(item.get("kind", ""))
        text = item.get("text", "")
        state = classify(item.get("status"), terminal, code, result=text)
        if kind in ("patch", "steering") and terminal and not item.get("status") in (None, "", "forming"):
            state = classify(item.get("status"), True, code, result=text)
        if state == PROPOSED:
            return          # the executor's own item reports what became of the proposed call
        status = item.get("status") if terminal else "running"
        if kind == "maintenance" and terminal:
            ok = str(item.get("status", "")).lower() in MAINTENANCE_OK
            state, status = (OK, "") if ok else (FAILED, item.get("status"))
            if ok and str(text).startswith("Task history compaction"):
                text = "Compacted task history"
        if kind == "tool.forming" and state == UNCERTAIN:
            status = "call not run"
        began = item.get("dispatched") or (item.get("started") if kind == "maintenance" else None)
        ran = (item.get("updated") or 0) - (began or 0)
        took = span(ran) if began and terminal and ran >= 0 else ""
        retained = len(str(text).encode("utf-8", "replace"))
        truncated = bool(item.get("artifact")) and retained >= (FULL_TAIL if self.full else TAIL) - 16
        self._emit(self.r.tool(kind, str(item.get("name") or ""), item.get("command") or "",
                               item.get("started") or item.get("updated"), state, status, took, code, text,
                               item.get("artifact"), truncated, seconds=item.get("updated")), sid)
        self.last_item = None

    def sweep_all(self):
        """After owner refresh: items whose process vanished become terminal."""
        for sid in list(self.proj.sessions):
            self.muted = not self.visible(sid)
            self._sweep(sid)
            self._status(sid, self.proj.sessions[sid])
        self.muted = False

    def refresh(self):
        if self.proj.refresh_owners():
            self.sweep_all()
            return True
        return False

    # -- backfill / snapshot ---------------------------------------------------
    def snapshot(self, records, cutoff=0, limit=18, flush_running=False):
        """Absorb history silently, then print what falls inside the window,
        grouped per session. Everything printed or older is marked committed."""
        self.flush_running = flush_running
        for rec in records:
            if self.proj.apply(rec):
                sid = rec.get("session", "agent")
                if isinstance(rec.get("exit_code"), int) and not isinstance(rec.get("exit_code"), bool):
                    self.exit_codes[(sid, self._rec_key(rec))] = rec["exit_code"]
                self._track(sid, self.proj.sessions[sid], rec.get("kind"), self._rec_key(rec), rec)
        self.proj.refresh_owners()
        sessions = sorted(self.proj.sessions.items(), key=lambda p: p[1]["updated"])
        for sid, session in sessions:
            self.last_status[sid] = session["status"]
            self.last_plan[sid] = session.get("plan")
            self.last_gap[sid] = session.get("gap")
            self.muted = not self.visible(sid) or session["updated"] < cutoff
            items = sorted(((k, i) for k, i in session["items"].items() if i.get("kind") not in SKIP_KINDS),
                           key=lambda p: p[1].get("started") or p[1].get("updated") or 0)
            window = [(k, i) for k, i in items if (i.get("updated") or 0) >= cutoff]
            hidden = max(0, len(window) - limit) if not self.full else 0
            shown = window[hidden:]
            shown_ids = {k for k, _ in shown}
            if not self.muted:
                self.headers.add(sid)
                self._out(self.r.session_header(sid, session))
                self.last_sid, self.last_time = sid, self.now()
                if session.get("plan"):
                    self._out(self.r.plan(session))
                if hidden:
                    self._out(self.r.note("%s %d earlier item%s not shown (--full shows all)" % (self.r.ell, hidden, "" if hidden == 1 else "s")))
            for key, item in items:
                tid = (sid, key)
                visible_item = key in shown_ids and not self.muted
                self.muted = not visible_item
                if self._is_text(key, item):
                    self._snapshot_text(sid, session, key, item)
                elif item.get("terminal") or flush_running:
                    self.done.add(tid)
                    self._tool(sid, session, key, item)
                elif not visible_item:
                    pass  # stale running item outside the window: prints when it finishes
            self.muted = not self.visible(sid) or session["updated"] < cutoff
            if not self.muted and session.get("gap"):
                self._out(self.r.gap(session["gap"]))
            if not self.muted and session.get("report") and session["status"] in FINISHED:
                self._out(self.r.report(session, sid))
        self.muted = False

    def _snapshot_text(self, sid, session, key, item):
        st = TextState(self._title(session, key[3], item), key[3], item.get("started") or 0, key[0])
        tid = (sid, key)
        self.texts[tid] = st
        text = item.get("text", "")
        if item.get("terminal"):
            self._finish_text(sid, st, text, item.get("status"))
        elif self.flush_running:
            self._finish_text(sid, st, text, "running", running=True)
        else:
            self._feed(sid, st, text)

    # -- live state for the status area -----------------------------------------
    def doing(self, sid, now=None):
        """What one session is doing now: dict(state, since, busy, command, seconds)."""
        now = self.now() if now is None else now
        s = self.proj.sessions[sid]
        running = [i for i in s["items"].values() if not i.get("terminal") and i.get("kind") != "tool.forming"]
        compacting = [i for i in running if i.get("kind") == "maintenance"]
        commands = sorted((i for i in running if i.get("command")),
                          key=lambda i: i.get("dispatched") or i.get("started") or 0)
        if compacting:
            return dict(state="compacting history", since=compacting[-1].get("started"), busy=True)
        if commands:
            i = commands[-1]
            return dict(state="running", since=i.get("dispatched") or i.get("started"), busy=True,
                        command=command_text(i["command"]), more=len(commands) - 1)
        if s["status"] == "thinking":
            return dict(state="thinking", since=s.get("call_started"), busy=True)
        if sid in self.rests:
            since, seconds = self.rests[sid]
            return dict(state="resting", since=since, busy=False, seconds=seconds)
        return dict(state=s["status"], since=None, busy=False)

    def live(self):
        """Structured state of the attached agent and its active work sessions (no output)."""
        now = self.now()
        sessions = [(sid, s) for sid, s in self.proj.sessions.items() if self.visible(sid)]
        agent = self.selected if self.selected in self.proj.sessions else None
        if agent is None and sessions:
            roots = [p for p in sessions if not p[1].get("parent")]
            agent = max(roots or sessions, key=lambda p: p[1]["updated"])[0]
        work = sorted(((sid, s) for sid, s in sessions if sid != agent and s.get("parent")
                       and s["status"] not in FINISHED), key=lambda p: p[1]["updated"])
        value = dict(now=now, agent=None, work=[])
        if agent is not None:
            s = self.proj.sessions[agent]
            value["agent"] = dict(sid=agent, model=self.models.get(agent) or s.get("model", ""),
                                  last_call=s.get("call_took"), **self.doing(agent, now))
        for sid, s in reversed(work):
            value["work"].append(dict(sid=sid, model=self.models.get(sid) or s.get("model", ""),
                                      last_call=s.get("call_took"), **self.doing(sid, now)))
        return value

    def footer(self, width, color=False, ascii_only=False):
        """One plain line summarising the live state (no trailing newline)."""
        value = self.live()
        if value["agent"] is None:
            return "waiting for agent or coding-session output" + ("..." if ascii_only else "…")
        now, sep = value["now"], (" - " if ascii_only else " · ")
        bits = []
        for entry in [value["agent"]] + value["work"][:1]:
            word = entry["state"] + (" " + span(now - entry["since"]) if entry.get("since") else "")
            bits.append(one_line(entry["sid"]) + " " + word)
        if len(value["work"]) > 1:
            bits.append("+%d more sessions" % (len(value["work"]) - 1))
        return truncate(sep.join(bits), max(10, width - 1), "..." if ascii_only else "…")

    def active(self):
        for sid, s in self.proj.sessions.items():
            if self.visible(sid) and s["status"] not in FINISHED and s["status"] != "ready":
                return True
        return False
