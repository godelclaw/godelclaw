"""Blocks for the inline viewer. Every block is a plain string of whole lines,
each at most `width` cells, with no right-hand borders or gutters beyond leading
indentation, so a terminal selection copies clean text. SGR colour appears only
when `color` is true."""
import json

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import CodeBlock, Heading, ListItem, Markdown
from rich.padding import Padding
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from rich.theme import Theme
from rich.color import ColorSystem

from .textsafe import clean, clock, command_text, guard_output, one_line, span, truncate

RUNNING, OK, FAILED, UNCERTAIN = "running", "ok", "failed", "uncertain"
GLYPHS = {RUNNING: "◌", OK: "✓", FAILED: "✗", UNCERTAIN: "?"}
ASCII_GLYPHS = {RUNNING: "~", OK: "+", FAILED: "x", UNCERTAIN: "?"}
GLYPH_STYLE = {RUNNING: "cyan", OK: "green", FAILED: "bold red", UNCERTAIN: "yellow"}

_UNCERTAIN_WORDS = ("interrupted", "unknown", "uncertain", "not run", "proposed", "pending",
                    "abandon", "cancel", "stopped", "forming", "awaiting")
_FAILED_WORDS = ("fail", "error", "timeout", "timed out", "killed", "denied", "reject", "crash", "refused")
_OK_WORDS = ("complete", "done", "ok", "success", "succeed", "finished", "passed", "recorded", "applied")
THEME = Theme({
    "markdown.code": "bold cyan", "markdown.code_block": "dim", "markdown.hr": "dim",
    "markdown.item.bullet": "bold", "markdown.item.number": "bold", "markdown.link": "none",
    "markdown.link_url": "dim underline", "markdown.block_quote": "dim",
})


def classify(status, terminal, exit_code=None, text_item=False):
    """Map an item's reported state to a glyph state; unknown outcomes stay uncertain."""
    if not terminal:
        return RUNNING
    word = str(status or "").lower()
    if any(w in word for w in _UNCERTAIN_WORDS) and word != "forming":
        return UNCERTAIN
    if exit_code not in (None, 0):
        return FAILED
    if any(w in word for w in _FAILED_WORDS):
        return FAILED
    if exit_code == 0 or any(w in word for w in _OK_WORDS):
        return OK
    return OK if (text_item or word in ("", "forming")) else UNCERTAIN


class FlatHeading(Heading):
    def __rich_console__(self, console, options):
        text = Text("#" * int(self.tag[1:]) + " ")
        text.append_text(self.text)
        text.justify = "left"
        yield text


class FlatCode(CodeBlock):
    """No syntax colouring, no background box: indented text, folded at the width."""
    def __rich_console__(self, console, options):
        code = str(self.text).rstrip("\n").expandtabs(4)
        yield Padding(Text(code, style="markdown.code_block", no_wrap=False, overflow="fold"), (0, 0, 0, 2))


ASCII_FOLD = str.maketrans({"·": "-", "…": ".", "•": "*", "▌": "|", "─": "-", "━": "-", "○": " ", "◆": "*",
                            "↳": "-", "›": ">", "✓": "+", "✗": "x", "◌": "~"})


class DottedItem(ListItem):
    """Ordered items keep their 'N.' period."""
    bullet = " • "

    def render_bullet(self, console, options):
        lines = console.render_lines(self.elements, options.update(width=options.max_width - 3), style=self.style)
        style = console.get_style("markdown.item.bullet", default="none")
        for i, line in enumerate(lines):
            yield Segment(self.bullet if i == 0 else "   ", style)
            yield from line
            yield Segment("\n")

    def render_number(self, console, options, number, last_number):
        number_width = len(str(last_number)) + 3
        lines = console.render_lines(self.elements, options.update(width=options.max_width - number_width), style=self.style)
        style = console.get_style("markdown.item.number", default="none")
        pad = Segment(" " * number_width, style)
        for i, line in enumerate(lines):
            yield Segment((str(number) + ".").rjust(number_width - 1) + " ", style) if i == 0 else pad
            yield from line
            yield Segment("\n")


class AsciiItem(DottedItem):
    bullet = " * "


class FlatMarkdown(Markdown):
    elements = {**Markdown.elements, "heading_open": FlatHeading, "fence": FlatCode,
                "code_block": FlatCode, "list_item_open": DottedItem}


class AsciiMarkdown(FlatMarkdown):
    elements = {**FlatMarkdown.elements, "list_item_open": AsciiItem}


def limit_lines(lines, rows, head_ratio=3):
    """(head, hidden_count, tail). rows<=0 means unlimited."""
    if rows <= 0 or len(lines) <= rows:
        return lines, 0, []
    head = max(1, (rows - 1) // head_ratio)
    tail = rows - 1 - head
    return lines[:head], len(lines) - head - tail, lines[len(lines) - tail:] if tail else []


class Renderer:
    def __init__(self, width=100, color=False, ascii_only=False, full=False, now=None):
        self.width, self.color, self.ascii, self.full, self.now = width, color, ascii_only, full, now
        self.text_rows = 60
        self.output_rows = 8
        self.sep = "-" if ascii_only else "·"
        self.ell = "..." if ascii_only else "…"

    def _join(self, bits):
        return (" %s " % self.sep).join(bits)

    # -- plumbing ---------------------------------------------------------
    def glyph(self, state):
        return (ASCII_GLYPHS if self.ascii else GLYPHS)[state]

    def _console(self, width):
        return Console(width=width, color_system="standard", force_terminal=True, theme=THEME,
                       highlight=False, emoji=False, markup=False, legacy_windows=False, _environ={})

    def lines(self, renderable, width=None):
        """Rich renderable -> list of segment lines, trimmed to the width."""
        width = max(10, width or self.width)
        console = self._console(width)
        raw = console.render_lines(renderable, console.options.update(width=width), pad=False)
        out = []
        for line in raw:
            line = [s for s in line if not s.control]
            while line and not line[-1].text.strip():
                line.pop()
            if line:
                last = line[-1]
                line[-1] = Segment(last.text.rstrip(), last.style)
            line = list(Segment.adjust_line_length(line, width, pad=False))
            out.append(line)
        return out

    def to_str(self, seg_lines):
        parts = []
        for line in seg_lines:
            for seg in line:
                if self.color and seg.style:
                    parts.append(seg.style.render(seg.text, color_system=ColorSystem.STANDARD))
                else:
                    parts.append(seg.text)
            parts.append("\n")
        text = "".join(parts)
        return guard_output(text.translate(ASCII_FOLD) if self.ascii else text, self.color)

    def block(self, *renderables, trailing_blank=True):
        seg = []
        for r in renderables:
            seg += self.lines(r)
        if trailing_blank:
            seg.append([])
        return self.to_str(seg)

    def _stamp(self, seconds):
        return clock(seconds, self.now) if seconds else ""

    def _lead(self, seconds, state=None):
        """Dim time and optional status glyph, as a Text."""
        t = Text()
        stamp = self._stamp(seconds)
        if stamp:
            t.append(stamp + "  ", style="dim")
        if state:
            t.append(self.glyph(state) + " ", style=GLYPH_STYLE[state])
        return t

    # -- session level ----------------------------------------------------
    def session_header(self, sid, session):
        head = Text()
        head.append("◆ " if not self.ascii else "* ", style="bold")
        head.append(one_line(session.get("title") or sid), style="bold")
        head.append("  " + one_line(session.get("status", "")), style="bold green")
        bits = []
        if session.get("model"):
            bits.append(one_line(session["model"]))
        if session.get("started"):
            bits.append("started " + self._stamp(session["started"]))
        if session.get("parent"):
            bits.append("parent " + one_line(session["parent"]))
        bits += self.call_bits(session)
        bits = [b for b in bits if b]
        room = max(10, self.width - 3)
        while len(bits) > 1 and cell_len(self._join(bits)) > room:
            bits.pop()                      # drop trailing facts whole rather than cut one mid-word
        sub = Text("  " + truncate(self._join(bits), room, self.ell), style="dim", no_wrap=True)
        head.no_wrap, head.overflow = True, "ellipsis"
        return self.block(head, sub)

    def call_times(self, session):
        return self._join(self.call_bits(session))

    def call_bits(self, session):
        bits = []
        if session.get("status") == "thinking" and session.get("call_started"):
            bits.append("since " + self._stamp(session["call_started"]))
        elif session.get("call_took") is not None:
            bits.append("last call " + span(session["call_took"]))
        if session.get("updated"):
            bits.append("updated " + self._stamp(session["updated"]))
        return bits

    def status_change(self, sid, session):
        t = self._lead(session.get("updated"))
        t.append(("↳ " if not self.ascii else "-> ") + one_line(sid) + " " + one_line(session.get("status", "")), style="dim")
        ct = self.call_times(session).split(self._join(["", "updated"]))[0]
        if ct:
            t.append(self._join(["", ct]), style="dim")
        return self.block(t, trailing_blank=False)

    def plan(self, session, seconds=None):
        text = session.get("plan", "")
        t = self._lead(seconds)
        t.append("Plan", style="bold")
        rows = [t]
        try:
            for p in json.loads(text).get("plan", []):
                state = {"completed": OK, "in_progress": RUNNING}.get(str(p.get("status")), None)
                row = Text("  ")
                if state:
                    row.append(self.glyph(state) + " ", style=GLYPH_STYLE[state])
                else:
                    row.append(("○ " if not self.ascii else "  "), style="dim")
                row.append(one_line(p.get("step", "")))
                row.no_wrap = False
                rows.append(row)
        except (ValueError, TypeError, AttributeError):
            rows.append(Text("  " + one_line(text)))
        return self.block(*rows)

    def gap(self, value):
        t = Text(("! " if True else "") + (("display missed %d updates; " % value) if isinstance(value, int) else (one_line(value) + "; "))
                 + "final records remain authoritative", style="yellow")
        return self.block(t)

    def note(self, text, seconds=None, dim=True):
        t = self._lead(seconds)
        t.append(one_line(text), style="dim" if dim else "")
        return self.block(t, trailing_blank=False)

    def report(self, session):
        head = self._lead(session.get("updated"))
        head.append("Session " + one_line(session.get("status", "")), style="bold")
        body = clean(session.get("report", "")).strip()
        parts = [head]
        if body:
            parts.append(self.markdown(body, rows=self.text_rows))
        return self.block(*parts)

    # -- items --------------------------------------------------------------
    def markdown(self, text, rows=0, style=""):
        text = clean(text).expandtabs(4)
        seg = self.lines(Padding((AsciiMarkdown if self.ascii else FlatMarkdown)(text, hyperlinks=False, style=style), (0, 0, 0, 2)), self.width)
        while seg and not seg[-1]:
            seg.pop()
        if not self.full:
            head, hidden, tail = limit_lines(seg, rows)
            if hidden:
                marker = [Segment("  %s %d more lines" % (self.ell, hidden), Style(dim=True))]
                seg = head + [marker] + tail
        return _SegLines(seg)

    def text_chunk(self, title, channel, started, text, state=None, header=True, close=False, cont=False, tail_note=None):
        """One append-only slice of a commentary / reasoning item."""
        parts = []
        if header:
            t = self._lead(started, state if state in (RUNNING, UNCERTAIN, FAILED) and close else None)
            t.append(one_line(title), style="bold cyan" if channel == "commentary" else "bold magenta" if channel == "reasoning-summary" else "bold")
            if cont:
                t.append(" (cont.)", style="dim")
            parts.append(t)
        if tail_note:
            parts.append(Text("  " + tail_note, style="dim"))
        if text.strip():
            parts.append(self.markdown(text, rows=self.text_rows, style="dim italic" if channel == "reasoning-summary" else ""))
        if close and state == UNCERTAIN:
            parts.append(Text("  " + self.glyph(UNCERTAIN) + " interrupted", style="yellow"))
        return self.block(*parts)

    def command_title(self, command):
        return "$ " + one_line(command_text(command))

    def tool_block(self, title_text, is_command, started, state, status, took, exit_code, text, artifact=None, truncated=False):
        width = self.width
        lead = self._lead(started, state)
        avail = width - lead.cell_len - 1
        title = one_line(title_text)
        head = lead
        if self.full:
            head.append(title, style="bold")
            head.no_wrap = False
            head.overflow = "fold"
        else:
            head.append(truncate(title, max(4, avail), self.ell), style="bold")
        detail = [one_line(status or ("running" if state == RUNNING else ""))]
        if took:
            detail.append(took)
        if exit_code is not None:
            detail.append("exit %s" % exit_code)
        parts = [head, Text("    " + self._join([d for d in detail if d]), style="dim")]
        body = clean(text).expandtabs(4)
        lines = body.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if truncated and lines:
            parts.append(Text("    " + self.ell + " earlier output not retained", style="dim"))
        if lines:
            if self.full:
                for ln in lines:
                    parts.append(Text("    " + ln, no_wrap=False, overflow="fold"))
            else:
                head_l, hidden, tail_l = limit_lines(lines, self.output_rows)
                cap = max(4, width - 5)
                for ln in head_l:
                    parts.append(Text("    " + truncate(ln, cap, self.ell), no_wrap=True))
                if hidden:
                    parts.append(Text("    %s %d more line%s" % (self.ell, hidden, "" if hidden == 1 else "s"), style="dim"))
                for ln in tail_l:
                    parts.append(Text("    " + truncate(ln, cap, self.ell), no_wrap=True))
        if artifact:
            parts.append(Text("    output: " + one_line(artifact), style="dim", no_wrap=not self.full, overflow="fold" if self.full else "ellipsis"))
        return self.block(*parts)

    def user_note(self, seconds, text, marker):
        t = self._lead(seconds)
        t.append("you ", style="bold")
        t.append(("› " if not self.ascii else "> "))
        t.append_text(Text(clean(text).replace("\n", " ").replace("\t", " "), no_wrap=False, overflow="fold"))
        m = Text("    " + marker, style="dim")
        return self.block(t, m, trailing_blank=False)


class _SegLines:
    """Pre-rendered segment lines usable as a renderable."""
    def __init__(self, lines):
        self.seg = lines

    def __rich_console__(self, console, options):
        for line in self.seg:
            yield from line
            yield Segment("\n")
