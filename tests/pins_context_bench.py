"""Timing: native pins context view against pins.context_view(), and the
caller boundary (addition-context-sources into context_sources.bundle_with_views).

    CETTA_BIN=/path/to/cetta PYTHONPATH=tests:src:channels python tests/pins_context_bench.py

State is a temporary directory; no live pin file or channel is touched. Each
figure is the mean over REPEAT calls in one warm process (the cost the agent
pays per turn), the median of three processes. Python is measured in-process
and, for the boundary, through the same CeTTa guest with the engine set to
petta (the SWI/Python path), so both include the same bridge.
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels"), str(ROOT / "tests")]
import helper  # noqa: E402
import pins  # noqa: E402
from isolated_env import isolated_environ  # noqa: E402
from test_pins_context_native import guest_env, q  # noqa: E402

BINARY = Path(os.environ["CETTA_BIN"])
SIZES = {
    "typical 12 pins": b"".join(b"[10-24 09:%02d] pin number %d with some words\n" % (i, i) for i in range(12)),
    "12 long pins (400+ chars)": b"".join(b"[x] " + b"w" * 450 + b"\n" for _ in range(12)),
    "100 pins": b"".join(b"[10-24 09:00] pin number %d\n" % i for i in range(100)),
    "3,000 pins (~120 KB)": b"".join(b"[10-24 09:00] pin number %d\n" % i for i in range(3000)),
    "100,000 pins (~3.4 MB)": b"".join(b"[10-24 09:00] pin number %d\n" % i for i in range(100000)),
    "2 lines, 1 MB each unicode": b"".join(("λ" * 500000 + "\n").encode() for _ in range(2)),
    "missing file": None,
}
REPEAT = {"typical 12 pins": 300, "12 long pins (400+ chars)": 300, "100 pins": 200, "3,000 pins (~120 KB)": 50,
          "100,000 pins (~3.4 MB)": 5, "2 lines, 1 MB each unicode": 5, "missing file": 300}


def python_view(path, repeat):
    os.environ["METTACLAW_PINS_PATH"] = str(path)
    helper._change_marks.clear()
    pins.context_view()
    start = time.perf_counter()
    for _ in range(repeat):
        pins.context_view()
    return (time.perf_counter() - start) / repeat * 1000


def guest(directory, lines, engine="cetta"):
    # Imports resolve upward from the source file: it must live under ROOT.
    with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta", encoding="utf-8") as source:
        source.write("\n".join(lines) + "\n")
        source.flush()
        result = subprocess.run([str(BINARY), "--lang", "petta", "--import-mode", "ancestor-walk", source.name],
                                cwd=ROOT, env=guest_env(directory, engine), capture_output=True, text=True,
                                timeout=1800)
    if result.returncode:
        raise SystemExit(result.stdout[-3000:] + result.stderr[-3000:])


HEADER = ["!(import! &self (library lib_import))", "!(import! &self ./src/helper.py)",
          "!(import! &self (library system))", "!(import! &self ./tests/pins_context_probe.py)"]
LOOP = ["(= (bench:repeat $n) (if (== $n 0) done (let $_ (bench:one) (bench:repeat (- $n 1)))))"]


def timed(directory, label, body, repeat, lines=(), engine="cetta"):
    """Mean ms per call. The last answer is read back: its streak proves that
    every call really ran (the marker advances once per call)."""
    out, last = directory / "ms", directory / "last"
    guest(directory, HEADER + list(lines) + ["(= (bench:one) %s)" % body] + LOOP + [
        "!(let* (($warm (bench:one)) ($t0 (system:wall-time)) ($_ (bench:repeat %d)) ($t1 (system:wall-time)) "
        "($final (bench:one))) "
        "(progn (py-call (pins_context_probe.note %s $final)) "
        "(py-call (pins_context_probe.note %s (* 1000 (/ (- $t1 $t0) %d))))))" % (repeat, q(last), q(out), repeat)],
        engine)
    answer = last.read_text(encoding="utf-8")
    if label != "missing file":
        assert "PINNED newest" in answer, (label, answer[:200])
        if "boundary" in body or "addition-context-sources" in body:
            # The marker advanced once per call at the single owner.
            assert "(unchanged for %d turns)" % (repeat + 1) in answer[:300], (label, answer[:300])
    return float(out.read_text())


def median3(function):
    return statistics.median(function() for _ in range(3))


def main():
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        path = directory / "pins.txt"
        for label, content in SIZES.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
            repeat = REPEAT[label]
            py = median3(lambda: python_view(path, repeat))
            native = median3(lambda: timed(directory, label, "(pinsctx:view)", repeat,
                                           ["!(import! &self ./src/pins_context_cetta.metta)"]))
            boundary = ["!(import! &self ./src/turn_additions)", "(= (tg:client-metta?) False)",
                        "!(py-call (pins_context_probe.restrict_to_pins))"]
            bn = median3(lambda: timed(directory, label, "(addition-context-sources)", repeat, boundary))
            bp = median3(lambda: timed(directory, label, "(addition-context-sources)", repeat, boundary, "petta"))
            rows.append((label, py, native, bp, bn))
            print("%-28s view: python %8.3f ms  native %8.3f ms (x%.1f) | boundary: python %8.3f ms  native %8.3f ms (x%.1f)"
                  % (label, py, native, native / py, bp, bn, bn / bp), flush=True)
    return rows


if __name__ == "__main__":
    main()
