"""py-call results reach PeTTa as Symbols where Python returned a str, so a
comparison with a String literal never matches and silently takes the other
branch. This scan keeps that bug class out of the runtime MeTTa files: a
py-call result compared with a String literal, directly or through a variable
bound to it, and a case over a py-call result whose String pattern lacks the
same text as a Symbol. Convert with (format-args "{}" (...)) or match both."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
LITERAL = r'"(?:[^"\\]|\\.)*"'
CALL = r"\(py-call\b"


def forms(text):
    """Top-level forms, skipping strings and comments."""
    out, depth, start, i = [], 0, None, 0
    while i < len(text):
        c = text[i]
        if c == ";":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if c == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")" and depth:
            depth -= 1
            if depth == 0:
                out.append((start, text[start:i + 1]))
        i += 1
    return out


def balanced(text, start):
    """The parenthesized expression beginning at text[start]."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


def problems(text):
    found = []
    for offset, form in forms(text):
        line = lambda at: text.count("\n", 0, offset + at) + 1
        for match in re.finditer(r"\(==\s*", form):
            rest = form[match.end():]
            if rest.startswith("(py-call"):
                right = rest[len(balanced(rest, 0)):].lstrip()
                if re.match(LITERAL, right):
                    found.append((line(match.start()), "py-call result compared with a String literal"))
            elif re.match(LITERAL + r"\s*" + CALL, rest):
                found.append((line(match.start()), "py-call result compared with a String literal"))
        for match in re.finditer(r"\(\s*(\$[\w\-]+)\s+" + CALL + r"|\(let\s+(\$[\w\-]+)\s+" + CALL, form):
            var = re.escape(match.group(1) or match.group(2))
            compared = re.search(r"\(==\s*" + var + r"\s*" + LITERAL + r"\s*\)|\(==\s*" + LITERAL + r"\s*" + var + r"\s*\)", form)
            if compared:
                found.append((line(compared.start()), "variable bound to a py-call result compared with a String literal"))
        for match in re.finditer(r"\(case\s+(?=" + CALL + r")", form):
            subject = balanced(form, match.end())
            branches = balanced(form, form.index("(", match.end() + len(subject)))
            for pattern in re.finditer(r"\(\s*(" + LITERAL + r")\s", branches):
                text_value = json.loads(pattern.group(1))
                if not re.search(r"\(\s*" + re.escape(text_value) + r"\s", branches):
                    found.append((line(match.start()), "case over a py-call result matches \"%s\" only as a String" % text_value))
    return found


class HostBoundaryTests(unittest.TestCase):
    def test_runtime_metta_has_no_string_comparison_of_py_call_results(self):
        files = json.loads((ROOT / "config/runtime-files.json").read_text())
        files = files if isinstance(files, list) else files.get("files", [])
        report = []
        for name in files:
            if not name.endswith(".metta") or "/upstream/" in name:
                continue
            path = ROOT / name
            if path.is_file():
                report += ["%s:%d: %s" % (name, line, why) for line, why in problems(path.read_text(encoding="utf-8"))]
        self.assertEqual(report, [])

    def test_the_scan_finds_each_shape(self):
        self.assertEqual(len(problems('(= (a) (if (== (py-call (m.f)) "chat") 1 0))')), 1)
        self.assertEqual(len(problems('(= (a) (if (== "chat" (py-call (m.f 1))) 1 0))')), 1)
        self.assertEqual(len(problems('(= (a) (let $mode (py-call (m.f)) (if (== $mode "chat") 1 0)))')), 1)
        self.assertEqual(len(problems('(= (a) (case (py-call (m.f)) (("chat" 1) ($other 0))))')), 1)
        self.assertEqual(problems('(= (a) (case (py-call (m.f)) (("chat" 1) (chat 1) ($other 0))))'), [])
        self.assertEqual(problems('(= (a) (== (format-args "{}" ((py-call (m.f)))) "chat"))'), [])
        self.assertEqual(problems('; (== (py-call (m.f)) "chat")\n(= (a) "(== (py-call (m.f)) \\"x\\")")'), [])


if __name__ == "__main__":
    unittest.main()
