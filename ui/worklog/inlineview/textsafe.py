"""Never trust text: strip terminal controls, bidi controls and hyperlinks;
measure and cut by terminal cells, not code points."""
import ast
import json
import re
import time
import unicodedata

from rich.cells import cell_len

# Control strings that carry a payload up to ST (ESC \) or BEL: OSC (incl. OSC 8
# hyperlinks), DCS, SOS, PM, APC. An unterminated one swallows the rest.
_STRING = re.compile(r"(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)")
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?")
_ESC_OTHER = re.compile(r"\x1b[ -/]*[0-~]?")
BIDI = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean(text):
    """Printable text only; keeps newline and tab. Bidi and format controls are
    removed, other controls become a space. A carriage return keeps only what
    follows it on that line (progress bars), so no cursor motion survives."""
    text = _STRING.sub("", str(text))
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.replace("\r\n", "\n").split("\n"))
    out = []
    for c in text:
        if c in "\n\t":
            out.append(c)
            continue
        cat = unicodedata.category(c)
        if c in BIDI or cat == "Cf":
            continue
        out.append(" " if cat[0] == "C" else c)
    return "".join(out)


def one_line(text):
    return " ".join(clean(text).split())


def cells(text):
    return cell_len(text)


def char_cells(c):
    return cell_len(c)


def truncate(text, width, ellipsis="…"):
    """Cut to at most `width` terminal cells, marking the cut."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    room = max(0, width - cell_len(ellipsis))
    out, used = [], 0
    for c in text:
        w = cell_len(c)
        if used + w > room:
            break
        out.append(c)
        used += w
    return "".join(out) + (ellipsis if cell_len(ellipsis) <= width else "")


def headline(text, width=120):
    return truncate(one_line(text), width)


def clock(seconds, now=None):
    """Local wall time: HH:MM:SS today, MM-DD HH:MM before."""
    if not seconds:
        return ""
    moment = time.localtime(float(seconds))
    today = time.localtime(time.time() if now is None else now)
    return time.strftime("%H:%M:%S" if moment[:3] == today[:3] else "%m-%d %H:%M", moment)


def span(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return "%.1fs" % seconds
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % divmod(int(seconds), 60)
    return "%dh%02dm" % divmod(int(seconds) // 60, 60)


def command_text(command):
    """Agent commands arrive as Python list text; show the MeTTa form issued."""
    text = str(command)
    if not (text.startswith("[") and text.endswith("]")):
        return text
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return text
    if not (isinstance(value, list) and value and isinstance(value[0], str)):
        return text

    def atom(v):
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, (list, tuple)):
            return "(" + " ".join(map(atom, v)) + ")"
        return str(v)
    return "(" + " ".join([value[0]] + [atom(v) for v in value[1:]]) + ")"


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def guard_output(text, color):
    """Last line of defence on bytes about to be written: only SGR colour
    sequences (and only when colour is on) may contain ESC."""
    if "\x1b" not in text and not any("\x80" <= c <= "\x9f" for c in text):
        return text
    if color:
        parts, pos = [], 0
        for m in _SGR.finditer(text):
            parts.append(_strip_esc(text[pos:m.start()]))
            parts.append(m.group(0))
            pos = m.end()
        parts.append(_strip_esc(text[pos:]))
        return "".join(parts)
    return _strip_esc(text)


def _strip_esc(text):
    return "".join(c for c in text if c != "\x1b" and not ("\x80" <= c <= "\x9f"))
