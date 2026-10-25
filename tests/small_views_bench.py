"""Warm loader/caller timings and individual native steps, on one engine.

The before route runs the starting commit's runtime from a temporary git
archive, with only the fixture probe copied in. Both routes run the actual
addition-context-sources caller on the same engine. This is not a full turn.
"""
import json
import os
from pathlib import Path
import statistics
import shutil
import subprocess
import tarfile
import tempfile
import time
from unittest import mock

from test_small_views_native import SmallViewsTest, SOURCES, encoded
from test_context_bundle_native import ROOT, HEADER, CALLER, guest, guest_env, q, save_evidence
import small_views_probe as probe

BASELINE = "ad7c1e1ac6054fd4f3906a7b0fde2aab497400a1"


def fixtures(case):
    directory = case.directory
    fake, mode, count = case.fake()
    query, commitment, task = (directory / name for name in ("queries", "commitment", "task"))
    repo = directory / "checkout"
    repo.mkdir()
    for args in (["init", "-b", "fixture"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "commit", "--allow-empty", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(repo), *args], env=guest_env(directory), capture_output=True, check=True)
    reports = directory / "reports" / "fixture"
    reports.mkdir(parents=True)
    for i in range(6):
        (reports / ("%d.md" % i)).write_text("# Report 日 🙂 %d\nimmutable body\n" % i)
    for i in range(8):
        (case.iter / ("%02d.metta" % i)).write_text('(= (fixture) "日 🙂")\n' * 20)
    (case.iter / "_disabled.metta").write_text("(disabled)\n")
    (directory / "pins.txt").write_text("[x] fixture pin 🙂\n")
    (directory / "history.metta").write_text('("t1" "\\n" ((shell "cat src/a.py")) "\\n")\n')
    step = {
        "command": [str(fake), str(mode), str(count)],
        "env": {"METTACLAW_ACTIVE_QUERIES_PATH": str(query),
                "METTACLAW_COMMITMENT_STATE_PATH": str(commitment),
                "METTACLAW_TASK_PHASE_PATH": str(task),
                "METTACLAW_REPORTS_ROOT": str(reports.parent)},
        "files": {str(mode): encoded({}), str(query): encoded(["日🙂", "query-a", "query-b", "query-a"]),
                  str(commitment): encoded({"phase": "need-trust", "revision": 4, "finished": False}),
                  str(task): encoded({"schema": 1, "task_id": "fixture 日", "revision": 3,
                                     "phases": {"a": {"status": "active"}, "日": {"status": "completed"},
                                                "z": {"status": "pending"}}}),
                  str(case.registry): encoded({"schema": 1, "projects": [{"id": "fixture",
                                             "checkouts": [{"path": str(repo)}]}]})}}
    path = directory / "benchmark-step.json"
    path.write_text(json.dumps(step))
    return path, fake


def measured(case, step, binary, route, sample, baseline_root):
    log = case.directory / ("bench-%s-%d" % (route, sample))
    native = route == "after"
    lines = HEADER + CALLER + [
        "!(import! &self (library system))",
        "!(import! &self ./tests/small_views_probe.py)",
        "!(py-call (small_views_probe.apply %s))" % q(step),
        "!(py-call (small_views_probe.setup %d 1))" % native,
        '!(bind! &bench-prepared (new-state (py-call (context_source_loaders.prepare ()))))',
        '!(bind! &bench-loaders (new-state (car-atom (get-state &bench-prepared))))']
    cases = ['("caller" (addition-context-sources))']
    for source in SOURCES:
        cases += ['(%s %s)' % (q(source), '(sv:load %s)' % q(source) if native else
                              '(ctx:decode (py-call (context_source_loaders.load (get-state &bench-loaders) %s)))' % q(source))]
    profiles = {
        "profile/noop": '"ok"',
        "profile/env": '(sv:env "METTACLAW_ACTIVE_QUERIES_PATH")',
        "profile/read": '(sv:read (sv:env "METTACLAW_ACTIVE_QUERIES_PATH"))',
        "profile/parse": '(sv:json (sv:env "METTACLAW_ACTIVE_QUERIES_PATH"))',
        "profile/query-render": '(sv:queries)',
        "profile/task-render": '(sv:task)',
        "profile/iter-entries": '(fs:entries (system:getenv-or-default "METTACLAW_ITER_PROCESS_DIR" ""))',
        "profile/iter-inventory": '(sv:iter)',
        "profile/iter-render": '(sv:development)',
        "profile/hash-source": '(str:sha256 (sv:bytes %s))' % q(case.iter / "00.metta"),
        "profile/engine-command": '(py-call (engine_modes.engine_command "cetta"))',
        "profile/artifact-key": '(sv:artifact-key (%s))' % q(binary),
        "profile/capability-probe": '(sv:cap-refresh (Cold) (sv:command (py-call (engine_modes.engine_command "cetta"))))',
        "profile/evidence-render": '(sv:evidence-render)',
        "profile/evidence-cache-hit": '(sv:evidence)',
        "profile/prepare": '(py-call (context_source_loaders.prepare ()))',
        "profile/retained-dispatch": '(ctx:load (get-state &bench-loaders) "work-state")',
        "profile/native-dispatch": '(ctx:load (get-state &bench-loaders) "active-query-declaration")',
        "profile/project": '(ctx:project (get-state &bench-loaders) (index-atom (get-state &bench-prepared) 1))',
        "profile/layout": '(ctx:layout (get-state &bench-observations))',
        "profile/caller-views": '(addition-context-views)',
        "profile/observe": '(ctx:observe "work-state" 1000 False ("value" "fixture work-state"))',
    }
    if native:
        cases += ['(%s %s)' % (q(label), expression) for label, expression in profiles.items()]
    lines += ['(= (bench:eval $label) (case $label (%s)))' % " ".join(cases),
              '(= (bench:repeat $n $label) (if (== $n 0) done (let $_ (bench:eval $label) (bench:repeat (- $n 1) $label))))']
    labels = ["caller", *SOURCES] + (list(profiles) if native else [])
    for label in labels:
        if label == "profile/noop":
            lines += ['!(bind! &bench-observations (new-state (ctx:project (get-state &bench-loaders) '
                      '(index-atom (get-state &bench-prepared) 1))))']
        repeat = 10 if label in {"profile/capability-probe", "profile/evidence-render"} else 50
        lines += ['!(let* (($warm (bench:eval %s)) ($t0 (system:monotonic-ns)) '
                  '($_ (bench:repeat %d %s)) ($t1 (system:monotonic-ns)) '
                  '($last (bench:eval %s))) '
                  '(py-call (small_views_probe.record %s (json:stringify (JsonObject '
                  '((JsonPair "label" %s) (JsonPair "repeat" %d) '
                  '(JsonPair "ms" (/ (- $t1 $t0) %d.0)) '
                  '(JsonPair "last" %s)))))))' %
                  (q(label), repeat, q(label), q(label), q(log), q(label), repeat,
                   repeat * 1000000, '$last' if label == "caller" else '(repr $last)')]
    guest(case.directory, lines, runtime_root=None if native else baseline_root,
          METTACLAW_OBSERVED_STRATEGY="0", **case.settings)
    rows = [dict(json.loads(entry["text"]), witness={key: value for key, value in entry.items() if key != "text"})
            for entry in map(json.loads, log.read_text().splitlines())]
    assert all(row["witness"]["forbidden"] == 0 for row in rows), rows
    assert all("Error" not in row["last"] and "unavailable" not in row["last"] for row in rows), rows
    # Counts at the end of the timed real caller: warm + 50 turns + final.
    retained = {spec.source_id for spec in probe.context_sources.SOURCE_SPECS} - (set(SOURCES) if native else set())
    assert rows[0]["witness"]["calls"] == {key: 52 for key in retained}, rows[0]
    assert rows[0]["witness"]["engine_calls"] == 52, rows[0]
    save_evidence("small-views-benchmark", {"route": route, "sample": sample, "rows": rows})
    return rows


def main():
    case = SmallViewsTest()
    case.setUp()
    try:
        step, binary = fixtures(case)
        archive = case.directory / "baseline.tar"
        baseline_root = case.directory / "baseline"
        baseline_root.mkdir()
        subprocess.run(["git", "archive", "--format=tar", "--output=" + str(archive), BASELINE],
                       cwd=ROOT, env=guest_env(case.directory), capture_output=True, check=True)
        with tarfile.open(archive) as bundle:
            bundle.extractall(baseline_root, filter="data")
        shutil.copyfile(ROOT / "tests/small_views_probe.py", baseline_root / "tests/small_views_probe.py")
        direct = {}
        with mock.patch.dict(os.environ, guest_env(case.directory, **case.settings), clear=True), mock.patch.object(
                probe.engine_modes, "engine_command", probe.engine_command):
            probe.apply(step)
            for source in SOURCES:
                probe.MODULES[source].view()
                samples = []
                for _ in range(3):
                    start = time.perf_counter_ns()
                    for _ in range(200):
                        probe.MODULES[source].view()
                    samples.append((time.perf_counter_ns() - start) / 200000000)
                direct[source] = samples
        routes = {route: [] for route in ("before", "after")}
        for sample in range(3):
            for route in routes:
                routes[route].append(measured(case, step, binary, route, sample, baseline_root))
            assert routes["before"][-1][0]["last"] == routes["after"][-1][0]["last"], "caller bytes differ"
        summaries = []
        for label in ["caller", *SOURCES]:
            row = {"label": label}
            for route, samples in routes.items():
                values = [next(row["ms"] for row in sample if row["label"] == label) for sample in samples]
                row[route + "_ms"] = values
                row[route + "_median_ms"] = statistics.median(values)
            if label in direct:
                row["python_direct_ms"] = direct[label]
                row["python_direct_median_ms"] = statistics.median(direct[label])
            row["ratio"] = row["after_median_ms"] / row["before_median_ms"]
            summaries.append(row)
            print(json.dumps(row), flush=True)
        profiles = {}
        for label in (row["label"] for row in routes["after"][0] if row["label"].startswith("profile/")):
            values = [next(row["ms"] for row in sample if row["label"] == label) for sample in routes["after"]]
            profiles[label] = {"ms": values, "median_ms": statistics.median(values)}
            print(json.dumps({"label": label, **profiles[label]}), flush=True)
        evidence = os.environ.get("CONTEXT_BUNDLE_EVIDENCE")
        if evidence:
            (Path(evidence) / "small-views-measurements.json").write_text(json.dumps({
                "summaries": summaries, "profiles": profiles, "raw": routes,
                "baseline": BASELINE + " runtime from temporary git archive; fixture probe only added to tests",
                "memory_limit_kib": 25165824}, indent=2) + "\n")
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
