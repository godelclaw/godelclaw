"""Printed Python text, read the same way on both py-call bridges.

Since CeTTa b8d3580b, py-call hands Python text back as a MeTTa String, which
println! prints quoted; earlier bridges handed back a symbol, printed bare.
Probes that print a value Python returned, such as a model answer, use bare()
so that one assertion reads the text either way.
"""
import re

_QUOTED = re.compile(r'\((?P<tag>[A-Za-z_][\w:-]*) "(?P<text>(?:[^"\\]|\\.)*)"\)')
_ESCAPE = re.compile(r'\\(.)', re.S)


def _unescape(text):
    # println! escapes only the quote and the backslash; line breaks print raw.
    return _ESCAPE.sub(lambda m: m.group(1) if m.group(1) in '"\\' else m.group(0), text)


def bare(output, tags):
    """Print (TAG "text") as (TAG text) for the given tags; nothing else changes."""
    wanted = set(tags)

    def unquote(match):
        if match.group("tag") not in wanted:
            return match.group(0)
        return "(" + match.group("tag") + " " + _unescape(match.group("text")) + ")"

    return _QUOTED.sub(unquote, output)
