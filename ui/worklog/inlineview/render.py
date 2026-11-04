"""Blocks for the inline viewer. Every block is a string of whole lines with no
borders, gutters or trailing spaces; prose is folded to `width` cells, while a
command or a path is one logical line the terminal may soft-wrap, so a terminal
or tmux selection copies it exactly. SGR colour appears only when `color` is true."""
import json
import re

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

RUNNING, OK, FAILED, UNCERTAIN, PROPOSED = "running", "ok", "failed", "uncertain", "proposed"
GLYPHS = {RUNNING: "◌", OK: "✓", FAILED: "✗", UNCERTAIN: "?", PROPOSED: "·"}
ASCII_GLYPHS = {RUNNING: "~", OK: "+", FAILED: "x", UNCERTAIN: "?", PROPOSED: "-"}
GLYPH_STYLE = {RUNNING: "cyan", OK: "green", FAILED: "bold red", UNCERTAIN: "yellow", PROPOSED: "dim"}

_UNCERTAIN_WORDS = ("interrupted", "unknown", "uncertain", "not run", "pending", "abandon", "cancel",
                    "stopped", "forming", "awaiting", "deferred", "withheld")
_FAILED_WORDS = ("fail", "error", "timeout", "timed out", "timed-out", "killed", "denied", "reject", "crash",
                 "refused", "signal", "exception", "no_result", "not_started", "not started", "http-status")
_OK_WORDS = ("complete", "done", "ok", "success", "succeed", "finished", "passed", "recorded", "applied",
             "returned", "exited")
# Usual outcome words, left out of a block's detail line (the glyph already says it).
_PLAIN_STATUS = ("exited", "returned", "applied", "completed", "done", "finished", "running")
SHELL_TOOLS = {"exec_command": "shell", "write_stdin": "shell input", "exec_poll": "poll", "exec_cancel": "cancel"}
COMMAND_LINES = 12          # rows of a multi-line command shown before eliding the rest
COMMAND_CHARS = 2000        # characters of a one-line command shown before eliding the rest
METTA_ROWS = 4              # rows of a long MeTTa command form
THEME = Theme({
    "markdown.code": "bold cyan", "markdown.code_block": "dim", "markdown.hr": "dim",
    "markdown.item.bullet": "bold", "markdown.item.number": "bold", "markdown.link": "none",
    "markdown.link_url": "dim underline", "markdown.block_quote": "dim",
})


def classify(status, terminal, exit_code=None, text_item=False, result=""):
    """Map an item's reported state to a glyph state; unknown outcomes stay uncertain.
    A MeTTa command that returned an (Error ...) value counts as failed; a tool call
    the model proposed is PROPOSED until the executor reports what happened to it."""
    if not terminal:
        return RUNNING
    word = str(status or "").lower()
    if word == "proposed":
        return PROPOSED
    if any(w in word for w in _UNCERTAIN_WORDS) and word != "forming":
        return UNCERTAIN
    if exit_code not in (None, 0):
        return FAILED
    if any(w in word for w in _FAILED_WORDS):
        return FAILED
    if word == "returned" and str(result or "").lstrip().startswith("(Error"):
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
                            "↳": "-", "›": ">", "✓": "+", "✗": "x", "◌": "~", "◇": "-", "→": ">"})


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
        self.names = {}         # session identifier -> configured display name
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

    def logical(self, text):
        """One Text as one unfolded line: the terminal soft-wraps it, so a selection
        of a command or path copies exactly what was printed."""
        text = text.copy()
        text.no_wrap, text.overflow, text.end = True, "ignore", ""
        width = max(self.width, text.cell_len + 1)
        return _SegLines(self.lines(text, width)[:1])

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
            seg += r.seg if isinstance(r, _SegLines) else self.lines(r)
        if trailing_blank:
            seg.append([])
        return self.to_str(seg)

    def _stamp(self, seconds):
        return clock(seconds, self.now) if seconds else ""

    def _lead(self, seconds, state=None):
        """Optional status glyph (time goes at the end of a line, see _meta), as a Text."""
        t = Text()
        if state:
            t.append(self.glyph(state) + " ", style=GLYPH_STYLE[state])
        return t

    def _meta(self, line, bits, seconds=None):
        """Append ' · a · b · HH:MM:SS' in dim to a header line."""
        bits = [b for b in bits if b]
        stamp = self._stamp(seconds)
        if stamp:
            bits.append(stamp)
        if bits:
            line.rstrip()
            line.append((" %s " % self.sep if line.plain else "") + self._join(bits), style="dim")
        return line

    def _fit(self, line):
        """A header line kept to one row (its facts are short; commands are printed separately)."""
        line.no_wrap, line.overflow = True, "ellipsis"
        return line

    # -- session level ----------------------------------------------------
    def session_line(self, sid, session, first=True, seconds=None):
        """Marks whose output follows. The first mention of a session names its model,
        parent and task; a later switch back to it is one short line."""
        head = Text()
        head.append(("◆ " if not self.ascii else "* "), style="bold magenta")
        head.append(one_line(self.names.get(sid, sid)), style="bold")
        bits = []
        if first:
            if session.get("model"):
                bits.append(one_line(session["model"]))
            if session.get("parent"):
                bits.append("started by " + one_line(self.names.get(session["parent"], session["parent"])))
        parts = [self._fit(self._meta(head, bits, seconds or session.get("updated")))]
        title = one_line(session.get("title") or "")
        if first and title and title != sid:
            parts.append(Text("  " + truncate(title, max(10, self.width - 2), self.ell), style="dim", no_wrap=True))
        return self.block(*parts, trailing_blank=False)

    def session_header(self, sid, session):
        """Kept for snapshot callers: the first-mention session line plus a blank line."""
        return self.session_line(sid, session, first=True) + "\n"

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

    def interrupted(self, sid, session, reason="", took=None, seconds=None):
        """A model call that ended without its result; the only state change kept in scrollback
        (thinking/ready transitions live in the status line)."""
        t = Text("! " + one_line(sid) + " model call interrupted", style="yellow")
        bits = [("reason " + one_line(reason)) if reason else "", span(took) if took is not None else ""]
        return self.block(self._fit(self._meta(t, bits, seconds)), trailing_blank=False)

    def status_change(self, sid, session):
        return self.interrupted(sid, session, took=session.get("call_took"), seconds=session.get("updated"))

    def plan(self, session, seconds=None):
        text = session.get("plan", "")
        t = Text("Plan", style="bold")
        rows = [self._meta(t, [], seconds)]
        try:
            value = json.loads(text)
            if value.get("explanation"):
                rows.append(Padding(Text(one_line(value["explanation"]), style="dim"), (0, 0, 0, 2)))
            for p in value.get("plan", []):
                state = {"completed": OK, "in_progress": RUNNING}.get(str(p.get("status")), None)
                row = Text()
                if state:
                    row.append(self.glyph(state) + " ", style=GLYPH_STYLE[state])
                else:
                    row.append(("○ " if not self.ascii else "  "), style="dim")
                row.append(one_line(p.get("step", "")))
                rows.append(Padding(row, (0, 0, 0, 2)))
        except (ValueError, TypeError, AttributeError):
            rows.append(Text("  " + one_line(text)))
        return self.block(*rows)

    def gap(self, value):
        t = Text("! " + (("display missed %d updates; " % value) if isinstance(value, int) else (one_line(value) + "; "))
                 + "final records remain authoritative", style="yellow")
        return self.block(t)

    def note(self, text, seconds=None, dim=True):
        t = Text(one_line(text), style="dim" if dim else "")
        return self.block(self._meta(t, [], seconds), trailing_blank=False)

    def report(self, session, sid=None, took=None):
        status = one_line(session.get("status", ""))
        state = classify(status, True)
        head = self._lead(None, state)
        head.append((one_line(sid) + " " if sid else "Session ") + status, style="bold")
        parts = [self._fit(self._meta(head, [span(took) if took else ""], session.get("updated")))]
        body = clean(session.get("report", "")).strip()
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

    def _bullet(self, lines, glyph, style):
        """Put a glyph in the two-cell indent of the first line of rendered markdown."""
        seg = lines.seg
        if seg and seg[0] and seg[0][0].text.startswith("  "):
            first = seg[0][0]
            seg[0] = [Segment(glyph + " ", Style.parse(style)), Segment(first.text[2:], first.style)] + seg[0][1:]
        return lines

    def text_chunk(self, title, channel, started, text, state=None, header=True, close=False, cont=False, tail_note=None):
        """One append-only slice of a commentary / reasoning item.

        Commentary (what the agent says) is a bulleted paragraph; reasoning is dim italic
        under a 'thinking' line. Neither repeats the session name or time: the session line
        above a run of output says whose it is."""
        parts = []
        reasoning = channel == "reasoning-summary"
        if header and (reasoning or channel not in ("commentary",)):
            t = self._lead(None, state if state in (RUNNING, UNCERTAIN, FAILED) and close else None)
            label = "thinking" if reasoning else one_line(title)
            t.append(("◇ " if reasoning else "") + label, style="dim" if reasoning else "bold")
            if cont:
                t.append(" (continued)", style="dim")
            parts.append(t)
        if tail_note:
            parts.append(Text("  " + tail_note, style="dim"))
        if text.strip():
            body = self.markdown(text, rows=self.text_rows, style="dim italic" if reasoning else "")
            if channel == "commentary" and header:
                body = self._bullet(body, "•", "bold")
            parts.append(body)
        if close and state == UNCERTAIN:
            parts.append(Text("  " + self.glyph(UNCERTAIN) + " interrupted", style="yellow"))
        return self.block(*parts)

    def command_title(self, command):
        return one_line(command_text(command))

    def _command_lines(self, command):
        """A shell command as printed lines: verbatim at column 0, never folded, so the
        copied lines join back to the command; an indent would, for one, stop a
        copied here-document's terminator from ending it (ConsoleView.copy_exact)."""
        text = clean(command).expandtabs(4).rstrip("\n")
        rows = text.split("\n")
        out = []
        shown = rows if self.full else rows[:COMMAND_LINES]
        for n, row in enumerate(shown):
            if not self.full and len(row) > COMMAND_CHARS:
                row = row[:COMMAND_CHARS] + self.ell + " (%d more characters)" % (len(row) - COMMAND_CHARS)
            out.append(self.logical(Text(row.rstrip(), style="bold")))
        if len(rows) > len(shown):
            out.append(Text("  %s %d more command lines" % (self.ell, len(rows) - len(shown)), style="dim"))
        return out

    def _output_lines(self, text, truncated):
        parts = []
        body = clean(text).expandtabs(4)
        lines = body.split("\n")
        if truncated and len(lines) > 1:
            lines = lines[1:]           # the retained tail starts mid-line
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if truncated and lines:
            parts.append(Text("    " + self.ell + " earlier output is in the log", style="dim"))
        if not lines:
            return parts
        if self.full:
            return parts + [Text("    " + ln.rstrip(), no_wrap=False, overflow="fold") for ln in lines]
        head_l, hidden, tail_l = limit_lines(lines, self.output_rows)
        cap = max(4, self.width - 5)
        for ln in head_l:
            parts.append(Text("    " + truncate(ln.rstrip(), cap, self.ell), style="dim", no_wrap=True))
        if hidden:
            parts.append(Text("    %s %d more line%s" % (self.ell, hidden, "" if hidden == 1 else "s"), style="dim"))
        for ln in tail_l:
            parts.append(Text("    " + truncate(ln.rstrip(), cap, self.ell), style="dim", no_wrap=True))
        return parts

    def tool(self, kind, name, command, started, state, status, took, exit_code, text, artifact=None,
             truncated=False, seconds=None):
        """One finished (or flushed) tool-like item.

        A shell command is printed on its own lines below the outcome line, verbatim and
        unfolded; a short MeTTa command is the outcome line's title. Output is a head/tail
        preview and the complete log path is one unfolded line."""
        status = one_line(status or ("running" if state == RUNNING else ""))
        word = status.lower()
        detail = [] if word in _PLAIN_STATUS else [status]
        if exit_code is not None and (name in SHELL_TOOLS or exit_code != 0):
            detail.append("exit %s" % exit_code)
        if took:
            detail.append(took)
        head = self._lead(None, state)
        shell = bool(command) and (name in SHELL_TOOLS or not str(command).startswith("["))
        body = []
        if shell:
            head.append(SHELL_TOOLS.get(name, name or "shell"), style="bold")
            body = self._command_lines(command)
        elif command:
            title = self.command_title(command)
            room = self.width - head.cell_len - cell_len(self._join(detail + ["00:00:00"])) - 4
            if self.full or cell_len(title) <= room:
                head.append(title, style="bold")
            else:
                # A long MeTTa form (often a task or message text) is read, not pasted: fold it.
                head.append("metta", style="bold")
                folded = self.lines(Padding(Text(title, style="bold", overflow="fold"), (0, 0, 0, 2)))
                if len(folded) > METTA_ROWS:
                    folded = folded[:METTA_ROWS - 1] + [[Segment("  %s %d more characters" % (
                        self.ell, max(0, cell_len(title) - (METTA_ROWS - 1) * (self.width - 2))), Style(dim=True))]]
                body = [_SegLines(folded)]
        elif kind == "maintenance":
            title = one_line(text) or "maintenance"
            head.append(title[:1].lower() + title[1:], style="bold")
            text = ""
        else:
            label = one_line(name or kind.replace(".", " ")).replace("apply_patch", "patch")
            head.append(label, style="bold")
            if kind in ("patch", "steering") and word in _PLAIN_STATUS:
                detail.insert(0, status)
        head = self._fit(self._meta(head, detail, seconds or started))
        parts = [head] + body + self._output_lines(text, truncated)
        if artifact:
            line = Text("    output: ", style="dim")
            line.append(one_line(artifact))
            parts.append(self.logical(line))
        return self.block(*parts)

    def tool_block(self, title_text, is_command, started, state, status, took, exit_code, text, artifact=None, truncated=False):
        """Older entry point: a titled block (title is the command when `is_command`)."""
        return self.tool("tool", "exec_command" if is_command else title_text, title_text if is_command else "",
                         started, state, status, took, exit_code, text, artifact, truncated)

    def user_note(self, seconds, text, marker):
        t = Text()
        t.append("› " if not self.ascii else "> ", style="bold cyan")
        t.append_text(Text(clean(text).replace("\n", " ").replace("\t", " "), no_wrap=False, overflow="fold"))
        m = Text("  " + marker, style="dim")
        return self.block(t, m, trailing_blank=False)

    def user_input(self, text):
        """The operator's submitted text, verbatim: '› ' before its first line, two spaces before the rest."""
        rows = clean(text).expandtabs(4).rstrip("\n").split("\n")
        parts = []
        for n, row in enumerate(rows):
            t = Text(("› " if not self.ascii else "> ") if n == 0 else "  ", style="bold cyan")
            t.append(row.rstrip(), style="bold")
            parts.append(self.logical(t))
        return self.block(*parts, trailing_blank=False)

    def menu(self, text, labels):
        """A control reply's text, then its choices numbered for typing."""
        parts = [self.logical(Text(row.rstrip())) for row in clean(text).expandtabs(4).rstrip("\n").split("\n")]
        digits = len(str(len(labels)))
        for n, label in enumerate(labels, 1):
            row = Text("  ")
            row.append(str(n).rjust(digits), style="bold cyan")
            row.append("  " + one_line(label))
            parts.append(row)
        if labels:
            parts.append(Text("  type a number to choose, or /close-menu", style="dim"))
        return self.block(*parts)

    def reply(self, sender, text, seconds=None):
        """A message the agent sent to this console: a header, then the text verbatim at column 0."""
        head = Text(one_line(sender), style="bold green")
        head.append(" → you" if not self.ascii else " -> you", style="green")
        parts = [self._fit(self._meta(head, [], seconds))]
        body = clean(text).expandtabs(4).rstrip("\n")
        for row in body.split("\n"):
            parts.append(self.logical(Text(row.rstrip())))
        return self.block(*parts)


class _SegLines:
    """Pre-rendered segment lines usable as a renderable."""
    def __init__(self, lines):
        self.seg = lines

    def __rich_console__(self, console, options):
        for line in self.seg:
            yield from line
            yield Segment("\n")
