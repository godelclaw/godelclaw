"""Event blocks for a console that already owns its prompt.

The console keeps its single prompt_toolkit editor; this module only turns
display events into text made of WHOLE lines (every block ends with a newline,
nothing is ever emitted mid-line), which is what patch_stdout can place above a
draft without touching it, plus the two live rows around the editor: an activity
row above it and a labelled status row below it. The only control sequences
produced are SGR colours, and only with `color=True`; all event text is
sanitised before it is laid out (textsafe), and guard_output drops any other ESC.

Loss-preserving fallback. The console's reader has already advanced past a batch
when it is handed to feed(), so a rendering fault must not drop it. Each event is
processed on its own: if the engine raises for one, whatever that event had
already written is rolled back and the event is shown instead as plain,
sanitised, whole-line text (labelled as a fallback; reported tool status and exit
code are quoted, never turned into a success mark). The remaining events of the
batch are still processed normally. Display only: nothing here submits, replays
or re-runs anything."""
import threading
import time

from rich.cells import cell_len

from .engine import ASCII_SPINNER, SPINNER, Engine
from .render import ASCII_FOLD, Renderer
from .textsafe import clean, command_text, guard_output, one_line, span, truncate

FALLBACK_LINE_LIMIT = 500
ERROR_LIMIT = 160
TEXT_KINDS = ("text.delta", "text.snapshot", "text.reconcile")


def fallback_text(events):
    """Plain whole-line text for events the normal view could not render."""
    blocks, notes = [], []          # blocks: [kind, label, text]; consecutive fragments of one item merge
    streamed = {}
    for rec in events:
        if not isinstance(rec, dict):
            continue
        kind = str(rec.get("kind", ""))
        who = one_line(rec.get("session", "")) or "?"
        item = str(rec.get("item", ""))
        text = rec.get("text", "")
        text = text if isinstance(text, str) else str(text)
        if kind == "text.delta" or kind == "tool.output":
            label = ("tool output " if kind == "tool.output" else (one_line(rec.get("channel", "")) or "text") + " ") + "(" + who + (" · " + one_line(item) if item else "") + ")"
            if blocks and blocks[-1][0] == kind and blocks[-1][1] == label:
                blocks[-1][2] += text
            else:
                blocks.append([kind, label, text])
            streamed[(kind, who, item)] = streamed.get((kind, who, item), "") + text
        elif kind in ("text.snapshot", "text.reconcile"):
            seen = streamed.get(("text.delta", who, item), "")
            label = "final text (" + who + ")"
            if seen and text.startswith(seen):
                blocks.append(["final", label + ", after the streamed part above", text[len(seen):]])
            else:
                blocks.append(["final", label, text])
        elif kind == "tool.finished":
            parts = ["status=" + (one_line(rec.get("status", "")) or "not reported")]
            code = rec.get("exit_code")
            if isinstance(code, int) and not isinstance(code, bool):
                parts.append("exit_code=%d" % code)
            blocks.append(["line", "tool finished (" + who + ") · " + " · ".join(parts), ""])
            if text and not streamed.get(("tool.output", who, item)):
                blocks.append(["tool.output", "tool output (" + who + (" · " + one_line(item) if item else "") + ")", text])
        elif kind in ("tool.forming", "tool.dispatching", "tool.started"):
            what = rec.get("command") or rec.get("name") or ""
            blocks.append(["line", kind.replace(".", " ") + " (" + who + "): " + truncate(one_line(command_text(what)), 200), ""])
        elif kind in ("attempt.open", "attempt.finished"):
            blocks.append(["line", kind.replace(".", " ") + " (" + who + ")" + ((" · status=" + one_line(rec.get("status", ""))) if rec.get("status") else ""), text])
        else:
            blocks.append(["line", (one_line(kind) or "event") + " (" + who + ")" + ((" · status=" + one_line(rec.get("status", ""))) if rec.get("status") else ""), text])
    lines = ["! display fallback: plain text; the normal view failed for the events below"]
    for kind, label, body in blocks:
        body = clean(body).replace("\t", "    ")
        if kind == "line":
            lines.append(label + (":" if body.strip() else ""))
        else:
            lines.append(label + ":")
        for line in body.split("\n") if body.strip() else []:
            lines.append("  " + line)
    if len(lines) > FALLBACK_LINE_LIMIT:
        hidden = len(lines) - FALLBACK_LINE_LIMIT
        lines = lines[:FALLBACK_LINE_LIMIT] + ["  … %d more lines not shown in the fallback" % hidden]
    return guard_output("\n".join(lines) + "\n", False)


class EventBlocks:
    def __init__(self, agent, columns, now=time.time, ascii_only=False, color=False):
        self.agent, self.columns, self.now, self.ascii = agent, columns, now, ascii_only
        self.lock = threading.Lock()
        self.out = []
        self.consumed = 0       # events of the current batch already handled (rendered or fallen back)
        self.fault = ""         # last renderer error, bounded and sanitised ("" when the last batch was clean)
        self.renderer = Renderer(width=80, color=color, ascii_only=ascii_only, now=now())
        self.engine = Engine(self.renderer, self.out.append, selected=agent, now=now)

    def _width(self):
        # One cell short of the terminal so a full-width line never autowraps under the prompt.
        return max(20, int(self.columns()) - 1)

    def _note(self, error):
        self.fault = truncate(one_line("%s: %s" % (type(error).__name__, error)), ERROR_LIMIT)

    def feed(self, events):
        """Apply events; return the text that became ready ('' or whole lines)."""
        with self.lock:
            events = list(events)
            self.consumed, self.fault = 0, ""
            self.renderer.width, self.renderer.now = self._width(), self.now()
            failed = []                           # consecutive events that fell back; shown together, in order
            for rec in events:
                mark = len(self.out)
                try:
                    self.engine.apply([rec])
                except Exception as error:        # the engine may have changed state and written part of a block
                    del self.out[mark:]              # drop that partial output; the event is shown as fallback text
                    self._note(error)
                    failed.append(rec)
                else:
                    if failed:                       # keep chronological order: fallback text goes before this event's output
                        self.out[mark:mark] = [fallback_text(failed)]
                        failed = []
                self.consumed += 1
            try:
                self.engine.refresh()
            except Exception as error:
                self._note(error)
            if failed:
                self.out.append(fallback_text(failed))
            text, self.out[:] = "".join(self.out), []
            return text

    def recover(self, events, error):
        """Last resort when feed() itself escaped: return what it had already produced plus the fallback
        rendering of the events it had not handled yet, so the batch is never lost."""
        with self.lock:
            events = list(events)
            done = min(max(self.consumed, 0), len(events))
            self._note(error)
            produced, self.out[:] = "".join(self.out), []
            rest = events[done:]
            if not rest:
                return produced
            try:
                return produced + fallback_text(rest)
            except Exception:             # even the fallback failed: still keep the text, one sanitised line per event
                return produced + "".join(clean(str(r.get("text", ""))).replace("\n", " ") + "\n"
                                          for r in rest if isinstance(r, dict) and r.get("text"))

    def prime(self, events):
        """Absorb events from before attachment without printing them, so the status
        row knows at once whether the agent is thinking, resting or running work."""
        with self.lock:
            self.renderer.width, self.renderer.now = self._width(), self.now()
            try:
                self.engine.snapshot(list(events), cutoff=float("inf"))
            except Exception as error:
                self._note(error)
            del self.out[:]
            self.engine.blank = True

    # -- operator-facing text (sanitised, whole lines) ---------------------------
    def _render(self, method, *args):
        with self.lock:
            self.renderer.width, self.renderer.now = self._width(), self.now()
            try:
                return getattr(self.renderer, method)(*args)
            except Exception:
                return guard_output(clean(" ".join(str(a) for a in args if isinstance(a, str))) + "\n", False)

    def user_input(self, text):
        return self._render("user_input", text)

    def reply(self, sender, text):
        return self._render("reply", sender, text, self.now())

    def menu(self, text, labels):
        return self._render("menu", text, labels)

    def line(self, text, style=""):
        """One sanitised notice line (style: '', 'dim', 'warning')."""
        text = one_line(text)
        if self.ascii:
            text = text.translate(ASCII_FOLD)
        if self.renderer.color and style:
            code = {"dim": "2", "warning": "33", "error": "1;31", "ok": "32"}.get(style)
            if code:
                return "\x1b[" + code + "m" + text + "\x1b[0m\n"
        return guard_output(text, False) + "\n"

    # -- the live rows around the editor --------------------------------------------
    def _live(self):
        with self.lock:
            return self.engine.live()

    def _sep(self):
        return " - " if self.ascii else " · "

    def _elapsed(self, entry, now):
        return span(now - entry["since"]) if entry.get("since") else ""

    def _short(self, sid):
        """work-20261104-225522-a6fe -> work-…-a6fe, for a tight status row."""
        parts = sid.split("-")
        return parts[0] + ("-...-" if self.ascii else "-…-") + parts[-1] if len(parts) > 3 else sid

    def _state_text(self, entry, now):
        word = entry["state"]
        elapsed = self._elapsed(entry, now)
        if word == "resting" and entry.get("seconds"):
            return "resting %s of %s" % (elapsed, span(entry["seconds"])) if elapsed else "resting"
        return word + (" " + elapsed if elapsed else "")

    def activity(self, hint=None, notice=None):
        """Formatted fragments for the row above the editor: what is happening right now."""
        try:
            now = self.now()
            live = self._live()
            width = self._width()
            if hint:
                return [("class:activity.hint", truncate(one_line(hint), width))]
            busy = [e for e in live["work"] if e.get("busy")] + ([live["agent"]] if live["agent"] and live["agent"].get("busy") else [])
            if busy:
                entry = busy[0]
                spin = ASCII_SPINNER if self.ascii else SPINNER
                frame = spin[int(now * 8) % len(spin)]
                lead = [("class:activity.spinner", frame + " "), ("class:activity.session", one_line(entry["sid"]) + " ")]
                if entry["state"] == "running":
                    more = " (+%d more)" % entry["more"] if entry.get("more") else ""
                    tail = self._sep() + self._elapsed(entry, now) + more
                    room = width - sum(cell_len(t) for _, t in lead) - cell_len(tail) - cell_len("running ")
                    body = [("class:activity.state", "running "),
                            ("class:activity.command", truncate(one_line(entry.get("command", "")), max(8, room),
                                                                "..." if self.ascii else "…")),
                            ("class:activity.meta", tail)]
                else:
                    body = [("class:activity.state", self._state_text(entry, now))]
                fragments = lead + body
            elif notice:
                fragments = [("class:activity.warning", notice)]
            else:
                fragments = [("class:activity.hint", self._sep().join(
                    ("Enter sends", "Alt-Enter new line", "/help commands", "Ctrl-D detaches")))]
            return self._clip(fragments, width)
        except Exception:
            return [("class:activity.hint", "")]

    def status(self, facts=None, notes=()):
        """Formatted fragments for the labelled status row under the editor:
        agent · model · agent state · work session and its state · queue · last call."""
        try:
            now = self.now()
            live = self._live()
            facts = facts or {}
            width = self._width()
            agent = live["agent"]
            model = facts.get("model") if isinstance(facts.get("model"), dict) else {}
            name = one_line(model.get("name") or (agent or {}).get("model") or "")
            effort = one_line(model.get("reasoningEffort") or "")
            items = [(0, [("class:status.agent", one_line(self.agent))])]
            if name:
                items.append((2, [("class:status.model", name + (" " + effort if effort and effort != "default" else ""))]))
            call = facts.get("call") if isinstance(facts.get("call"), dict) else {}
            phase = call.get("phase")
            if phase == "backoff":
                retry = call.get("retrySeconds")
                items.append((0, [("class:status.warning", "provider backoff" + ((self._sep() + "retry in %ss" % retry) if retry else "")
                                   + (" (%s)" % one_line(call["reason"]) if call.get("reason") else ""))]))
            elif phase == "blocked":
                items.append((0, [("class:status.error", "provider refused" + (" %s" % one_line(call.get("status") or "")
                                   if call.get("status") else ""))]))
            elif phase == "failed":
                items.append((1, [("class:status.warning", "last call failed" + (" (%s)" % one_line(call["reason"])
                                   if call.get("reason") else ""))]))
            if agent is not None:
                busy = agent.get("busy")
                items.append((0, [("class:status.busy" if busy else "class:status.state", self._state_text(agent, now))]))
            else:
                items.append((0, [("class:status.state", "no activity seen yet")]))
            work = live["work"]
            if work:
                entry = work[0]
                items.append((1, [("class:status.work", one_line(entry["sid"])),
                                  ("class:status.state", " " + self._state_text(entry, now))]))
                if len(work) > 1:
                    items.append((4, [("class:status.meta", "+%d more sessions" % (len(work) - 1))]))
            inbox = facts.get("humanInbox")
            if isinstance(inbox, dict) and type(inbox.get("pending")) is int:
                pending = inbox["pending"]
                items.append((2, [("class:status.label", "queue "),
                                  ("class:status.warning" if pending else "class:status.value", str(pending))]))
            if agent is not None and agent.get("last_call") is not None and agent["state"] != "thinking":
                items.append((3, [("class:status.label", "last call "), ("class:status.value", span(agent["last_call"]))]))
            for note in notes:
                if note:
                    items.append((1, [("class:status.warning", one_line(note))]))
            return self._fit(items, width)
        except Exception:
            return [("class:status.agent", one_line(self.agent)), ("class:status.state", " · status unavailable")]

    def _fit(self, items, width):
        """Join the items with ' · ' and drop the least important ones until the row fits;
        a work session id is shortened before anything is dropped."""
        sep = self._sep()

        def joined(chosen):
            out = []
            for n, (_, fragments) in enumerate(chosen):
                if n:
                    out.append(("class:status.sep", sep))
                out += fragments
            return out

        def size(fragments):
            return sum(cell_len(t) for _, t in fragments)

        chosen = list(items)
        if size(joined(chosen)) > width:
            chosen = [(p, [(style, self._short(t) if style == "class:status.work" else t) for style, t in f])
                      for p, f in chosen]
        while size(joined(chosen)) > width and len(chosen) > 1:
            worst = max(range(len(chosen)), key=lambda n: (chosen[n][0], n))
            if chosen[worst][0] == 0:
                break
            del chosen[worst]
        return self._clip(joined(chosen), width)

    def _clip(self, fragments, width):
        out, used = [], 0
        for style, text in fragments:
            if self.ascii:
                text = text.translate(ASCII_FOLD)
            room = width - used
            if room <= 0:
                break
            if cell_len(text) > room:
                text = truncate(text, room, "..." if self.ascii else "…")
            out.append((style, text))
            used += cell_len(text)
        return out

    def footer(self):
        """The status row as plain text (kept for callers that want one string)."""
        return "".join(text for _, text in self.status())
