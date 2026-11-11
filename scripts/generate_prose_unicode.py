#!/usr/bin/env python3
"""Generate Python-compatible Unicode profiles as native-basis differences.

The oracle is the Python 3.12 runtime embedded by CeTTa.  The basis comes from
the selected CeTTa executable itself, never from the host's regex library.
Both providers and the scalar domain are qualified before any file is written.
Run with the bundle's Python and CETTA_BIN selecting its verified bundle:

    python3 scripts/generate_prose_unicode.py          # rewrite
    python3 scripts/generate_prose_unicode.py --check  # exit 1 if stale
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "undelivered_prose_unicode.metta"
DOMAIN = frozenset(range(0xD800)) | frozenset(range(0xE000, 0x110000))
ORACLE_PYTHON = (3, 12)
ORACLE_UNICODE = "15.0.0"
NATIVE_UNICODE = "16.0.0"
sys.path.insert(0, str(ROOT / "src"))


class QualificationError(RuntimeError):
    """The selected providers cannot safely publish generated profiles."""


def require(condition, message):
    if not condition:
        raise QualificationError(message)


def ranges(codepoints):
    out = []
    for c in codepoints:
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def escaped(c):
    return "\\x{%X}" % c


def char_class(codepoints, negated=False):
    parts = [escaped(lo) if lo == hi else escaped(lo) + "-" + escaped(hi)
             for lo, hi in ranges(codepoints)]
    return "[" + ("^" if negated else "") + "".join(parts) + "]"


def metta_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_oracle():
    require(sys.version_info[:2] == ORACLE_PYTHON,
            "unqualified Python oracle; use the bundle's Python 3.12")
    require(unicodedata.unidata_version == ORACLE_UNICODE,
            "unqualified Python Unicode oracle: " + unicodedata.unidata_version)


def validate_native(native):
    require(native.get("version") == NATIVE_UNICODE,
            "unqualified native Unicode provider: " + str(native.get("version")))


def selected_binary():
    configured = os.environ.get("CETTA_BIN")
    if configured:
        return Path(configured).expanduser().resolve()
    local = ROOT / ".runtime" / "cetta" / "cetta"
    if local.is_file():
        return local
    return Path.home() / "repos" / "CeTTa-runtime" / "cetta"


def selected_source(revision):
    configured = os.environ.get("CETTA_SOURCE")
    candidates = ([Path(configured).expanduser()] if configured else
                  [ROOT.parent / "CeTTa-ours", ROOT / ".build" / "CeTTa"])
    for source in candidates:
        result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip() == revision:
            clean = subprocess.run(["git", "-C", str(source), "diff", "--quiet", revision],
                                   capture_output=True, text=True, check=False)
            require(clean.returncode == 0,
                    "selected CeTTa source has uncommitted changes or cannot be verified")
            return source.resolve()
    raise QualificationError(
        "no CeTTa source checkout matches CETTA_REF; set CETTA_SOURCE")


def native_basis():
    """Read exact native sets and prove selected source/bundle correspondence."""
    validate_oracle()
    binary = selected_binary()
    require(binary.is_file() and os.access(binary, os.X_OK),
            "selected CeTTa executable is unavailable; set CETTA_BIN")
    try:
        manifest = json.loads((binary.parent / "build-manifest.json").read_text(
            encoding="utf-8"))
        expected = manifest["files"][binary.name]
        revision = manifest["revision"]
        python_version = manifest["python_version"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise QualificationError(
            "selected CeTTa bundle lacks verifiable provider evidence") from error
    selected = (ROOT / "CETTA_REF").read_text(encoding="utf-8").strip()
    require(revision == selected,
            "CeTTa bundle/source revision mismatch; regenerate intentionally")
    require(python_version == "%d.%d" % ORACLE_PYTHON,
            "CeTTa bundle Python does not match the qualified oracle")
    require(hashlib.sha256(binary.read_bytes()).hexdigest() == expected,
            "CeTTa executable does not match its manifest")
    source = selected_source(revision)
    with tempfile.TemporaryDirectory(prefix="godelclaw-unicode-") as directory:
        reader = Path(directory) / "reader"
        sources = [ROOT / "scripts" / "dump_unicode_basis.c",
                   source / "native/unicode_class_v1.c",
                   source / "native/unicode_class_equality_v1.c",
                   source / "langdef/unicode/generated/unicode_ucd16_v1.generated.c"]
        result = subprocess.run(["cc", "-std=c11", "-O2", "-I" + str(source),
                                 "-I" + str(source / "src"),
                                 *map(str, sources), "-o", str(reader)],
                                capture_output=True, text=True, check=False)
        require(result.returncode == 0,
                "cannot inspect selected native Unicode provider: " + result.stderr)
        result = subprocess.run([str(reader)], capture_output=True, text=True,
                                check=False, timeout=30)
        require(result.returncode == 0 and not result.stderr,
                "native Unicode provider inspection failed")
        native = json.loads(result.stdout)
    validate_native(native)
    classes = {name: frozenset(
        c for lo, hi in native["classes"][key] for c in range(lo, hi + 1)) & DOMAIN
        for name, key in (("alnum", "L"), ("numbers", "N"),
                          ("controls", "C"), ("separators", "Z"))}
    classes["alnum"] |= classes.pop("numbers")
    classes["nonprintable"] = classes.pop("controls") | classes.pop("separators")
    require(all(values <= DOMAIN for values in classes.values()),
            "native Unicode basis escaped the scalar domain")
    return {"version": native["version"], "revision": revision, "classes": classes}


def exact_pattern(basis, target, expression):
    removed, added = basis - target, target - basis
    body = expression
    if removed:
        body = "[" + body + "--[" + "".join(
            escaped(lo) if lo == hi else escaped(lo) + "-" + escaped(hi)
            for lo, hi in ranges(sorted(removed))) + "]]"
    if added:
        body = "[" + body + "[" + "".join(
            escaped(lo) if lo == hi else escaped(lo) + "-" + escaped(hi)
            for lo, hi in ranges(sorted(added))) + "]]"
    require(((basis - removed) | added) == target,
            "Unicode correction algebra does not reconstruct the oracle")
    return body


def profiles(native=None):
    native = native or native_basis()
    validate_native(native)
    classes = native["classes"]
    alnum = frozenset(c for c in DOMAIN if chr(c).isalnum())
    nonprintable = frozenset(c for c in DOMAIN if not chr(c).isprintable())
    return {
        "alnum": exact_pattern(classes["alnum"], alnum, r"[\p{L}\p{N}]"),
        "nonprintable": exact_pattern(
            classes["nonprintable"], nonprintable, r"[\p{C}\p{Z}]"),
    }


def tables(native=None):
    import helper
    generated = profiles(native)
    space = [c for c in sorted(DOMAIN) if chr(c).isspace()]
    breaks = [c for c in space if len(("a" + chr(c) + "b").splitlines()) == 2]
    require(set(breaks) <= set(space), "line breaks must remain whitespace")
    require("a\r\nb".splitlines() == ["a", "b"], "CRLF oracle changed")
    blanks = [c for c in space if c not in breaks]
    pattern = helper._AFFECT_TRACE.pattern
    require(len(pattern) == 8 and pattern[2:5] == "[^" + pattern[7]
            and pattern[5:7] == "]*", "affect-trace oracle shape changed")
    affect = (escaped(ord(pattern[0])) + escaped(ord(pattern[1]))
              + char_class([ord(pattern[7])], negated=True) + "*"
              + escaped(ord(pattern[7])))
    return {
        "line-break": escaped(13) + escaped(10) + "|" + char_class(breaks),
        "blank-run": char_class(blanks) + "+",
        "space": char_class(space),
        "word": char_class(space, negated=True) + "+",
        "alnum": generated["alnum"],
        "affect-trace": affect,
    }


def render(native=None):
    native = native or native_basis()
    validate_native(native)
    lines = [
        "; Generated by scripts/generate_prose_unicode.py; do not edit.",
        "; Python 3.12 Unicode 15.0.0 predicates over CeTTa Unicode %s."
        % native["version"],
        "; Scalar-domain corrections; selected CeTTa source %s." % native["revision"],
        '(= (prose:unicode-version) "%s")' % unicodedata.unidata_version,
    ]
    for name, pattern in tables(native).items():
        lines.append("(= (prose:pattern %s) %s)" % (name, metta_string(pattern)))
    return "\n".join(lines) + "\n"


def publish(target, text, check):
    if check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print("%s is stale; regenerate with the qualified CeTTa bundle"
                  % target.relative_to(ROOT))
            return 1
        return 0
    target.write_text(text, encoding="utf-8")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        return publish(TARGET, render(), "--check" in argv)
    except QualificationError as error:
        print("REFUSE: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
