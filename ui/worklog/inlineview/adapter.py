"""Event blocks for a console that already owns its prompt.

The console keeps its single prompt_toolkit editor; this module only turns
display events into text made of WHOLE lines (every block ends with a newline,
nothing is ever emitted mid-line), which is what patch_stdout can place above a
draft without touching it, plus a one-line status string for a bottom toolbar.
No terminal control sequences are produced (the console runs patch_stdout with
raw=False, which would turn them into '?').

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

from .engine import Engine
from .render import Renderer
from .textsafe import clean, command_text, guard_output, one_line, truncate

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
    def __init__(self, agent, columns, now=time.time, ascii_only=False):
        self.agent, self.columns, self.now, self.ascii = agent, columns, now, ascii_only
        self.lock = threading.Lock()
        self.out = []
        self.consumed = 0       # events of the current batch already handled (rendered or fallen back)
        self.fault = ""         # last renderer error, bounded and sanitised ("" when the last batch was clean)
        self.renderer = Renderer(width=80, color=False, ascii_only=ascii_only, now=now())
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

    def footer(self):
        """One short line for prompt_toolkit's bottom_toolbar (never raises)."""
        try:
            with self.lock:
                if not self.engine.proj.sessions:
                    return "attached to %s · no activity yet" % self.agent
                return self.engine.footer(self._width(), False, self.ascii)
        except Exception:
            return "attached to %s · status unavailable" % self.agent
