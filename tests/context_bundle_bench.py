"""Whole addition-context-sources, native vs Python on the same CeTTa engine.

Median of three fresh processes, warm per-turn costs, no imports/startup in
the timed interval. Ten retained source texts are controlled fixtures; real
pins/action loaders and views run, as do every-source projection and layout.
"""
import json
import os
from pathlib import Path
import statistics
import tempfile

from test_context_bundle_native import HEADER, CALLER, context_sources, guest, q, save_evidence


def timed(directory, fixture, repeat, engine):
    output = directory / "time"
    final = directory / "final"
    final.unlink(missing_ok=True)
    lines = HEADER + CALLER + [
        "!(import! &self (library system))",
        "!(py-call (context_bundle_probe.configure %s %d))" % (q(fixture), engine == "cetta"),
        "(= (bench:repeat $n) (if (== $n 0) done (let $_ (addition-context-sources) (bench:repeat (- $n 1)))))",
        "!(let* (($warm (addition-context-sources)) ($t0 (system:wall-time)) "
        "($_ (bench:repeat %d)) ($t1 (system:wall-time)) ($final (addition-context-sources))) "
        "(progn (py-call (context_bundle_probe.record %s $final)) "
        "(py-call (context_bundle_probe.note %s (* 1000 (/ (- $t1 $t0) %d))))))" % (repeat, q(final), q(output), repeat)]
    guest(directory, lines, engine=engine, METTACLAW_OBSERVED_STRATEGY="0")
    value = json.loads(final.read_text())
    save_evidence("benchmark-caller", {"engine_route": engine, "repeat": repeat, "final": value})
    assert value["forbidden"] == 0, value
    assert value["calls"] == {spec.source_id: repeat + 2 for spec in context_sources.SOURCE_SPECS}, value["calls"]
    assert "(unchanged for %d turns)" % (repeat + 1) in value["text"]
    return float(output.read_text()), value["text"]


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="context-bundle-bench-") as temp:
        directory = Path(temp)
        for label, scale, repeat in (("typical", 0.25, 100), ("large Unicode/clipped", 1.5, 30)):
            fixture = {}
            for spec in context_sources.SOURCE_SPECS:
                if spec.source_id not in {"pins", "recent-proposals", "relevant-files"}:
                    count = int(spec.max_chars * scale)
                    fixture[spec.source_id] = ("漢字🙂é " * (count // 6 + 1))[:count]
            path = directory / "fixture.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            pin_count = 12 if scale < 1 else 100
            (directory / "pins.txt").write_text("".join("[x] pin %d %s\n" % (i, "words 🙂 " * 20) for i in range(pin_count)))
            turns = 24 if scale < 1 else 300
            (directory / "history.metta").write_text("".join('("t%d" "\n" ((shell "cat src/file%d.py")) "\n")\n' % (i, i) for i in range(turns)))
            samples = {"native_ms": [], "python_ms": []}
            for _ in range(3):
                native, native_text = timed(directory, path, repeat, "cetta")
                python, python_text = timed(directory, path, repeat, "petta")
                assert native_text == python_text, "benchmark caller bytes differ"
                samples["native_ms"].append(native)
                samples["python_ms"].append(python)
            native = statistics.median(samples["native_ms"])
            python = statistics.median(samples["python_ms"])
            row = {"label": label, "repeat": repeat, "processes_per_route": 3,
                   "caller_codepoints": len(native_text), "caller_utf8_bytes": len(native_text.encode()),
                   **samples, "native_median_ms": native, "python_median_ms": python,
                   "slowdown": native / python}
            rows.append(row)
            print(json.dumps(row), flush=True)
    evidence = os.environ.get("CONTEXT_BUNDLE_EVIDENCE")
    if evidence:
        (Path(evidence) / "measurements.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
