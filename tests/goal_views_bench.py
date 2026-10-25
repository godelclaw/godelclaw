"""Cold complete-caller before/after, plus SEPARATE warm per-step profiles.

The baseline is the accepted project-03 runtime, not a simulated Python route.
Uses its four-checkout / 24-report workload and real independent Git witnesses.
All child environments go through isolated_environ / guest_env.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
from unittest import mock

from isolated_env import isolated_environ
from small_views_cold_bench import cold_fixtures, make_git_wrapper, engine_info, digest
from test_small_views_native import SmallViewsTest, encoded
from test_goal_views_native import goal
from test_context_bundle_native import ROOT, HEADER, CALLER, guest, guest_env, q, save_evidence

BASELINE = "e9144f9a8e7623777d8e98d08c50ed1a0849fd0e"


def archive(directory):
    target, packed = directory / "before", directory / "before.tar"
    target.mkdir()
    subprocess.run(["git", "archive", "--format=tar", "--output=" + str(packed), BASELINE],
                   cwd=ROOT, env=guest_env(directory), check=True, capture_output=True)
    with tarfile.open(packed) as stream:
        stream.extractall(target, filter="data")
    for name in ("small_views_probe.py", "small_views_cold_probe.py", "goal_views_probe.py", "goal_views_bench_probe.py"):
        shutil.copyfile(ROOT / "tests" / name, target / "tests" / name)
    return target


def measured(case, step, root, native, process, args, wrapper, real_git):
    label = ("after" if native else "before") + "-%d" % process
    trace, log, profiles = (case.directory / (label + suffix) for suffix in (".git-trace", ".samples", ".profiles"))
    outputs = case.directory / (label + ".git-outputs")
    outputs.mkdir()
    lines = HEADER + CALLER + ["!(import! &self (library system))",
        "!(import! &self ./src/goals.py)", "!(import! &self ./src/skills.metta)",
        "!(import! &self ./tests/goal_views_bench_probe.py)",
        "!(py-call (goal_views_bench_probe.configure %s %d))" % (q(step), native)]
    for workload, count in (("prime", 1), ("cold", args.cold_samples), ("warm", args.warm_samples)):
        for sample in range(count):
            lines += ['!(py-call (small_views_cold_probe.begin %s %d %s))' % (q(workload), sample, args.expiry_wait),
                '!(let* (($start (system:monotonic-ns)) ($text (addition-context-sources)) '
                '($end (system:monotonic-ns))) (py-call (small_views_cold_probe.record %s $start $end $text)))' % q(log)]
    # These are WARM microbenchmarks, never included in cold caller summaries.
    lines += ['!(bind! &gvbench-prepared (new-state (py-call (context_source_loaders.prepare ()))))',
              '!(bind! &gvbench-loaders (new-state (car-atom (get-state &gvbench-prepared))))']
    profiles_map = {source: ('(gv:load %s)' % q(source) if native else
                            '(ctx:decode (py-call (context_source_loaders.load (get-state &gvbench-loaders) %s)))' % q(source))
                    for source in ("work-state", "affect-gestalt")}
    profiles_map["goals-view"] = '(goals)'
    if native:
        lines += ['!(bind! &gvbench-text (new-state (gv:read (gv:path))))',
                  '!(bind! &gvbench-lines (new-state (gv:lines (get-state &gvbench-text))))',
                  '!(bind! &gvbench-parsed (new-state (gv:parse (get-state &gvbench-lines))))',
                  '!(bind! &gvbench-body (new-state (let (GvParsed $goals $flags) (get-state &gvbench-parsed) '
                  '(gv:work-body (gv:path) $goals $flags 6000 180))))']
        profiles_map.update({
            "path": "(gv:path)", "read": "(gv:read (gv:path))",
            "splitlines": "(gv:lines (get-state &gvbench-text))",
            "parse": "(gv:parse (get-state &gvbench-lines))",
            "render": "(let (GvParsed $goals $flags) (get-state &gvbench-parsed) (gv:work-body (gv:path) $goals $flags 6000 180))",
            "compost": "(gv:compost (gv:path))", "round": "(gv:r 0.00015)",
            "marker": '(py-call (helper.change_mark "profile-only" (get-state &gvbench-body)))',
            "kernel-read-parse": "(gv:kernel (gv:path))", "mood-render-and-read": "(gv:affect (gv:path))",
            "fair-boundary": "(py-call (goal_views_boundary.fair_suffix))",
            "prepare": "(py-call (context_source_loaders.prepare ()))"})
    for name, expression in profiles_map.items():
        for _ in range(args.profile_samples):
            lines += ['!(let* (($start (system:monotonic-ns)) ($value %s) ($end (system:monotonic-ns))) '
                      '(py-call (goal_views_bench_probe.profile %s %s $start $end (repr $value))))' %
                      (expression, q(profiles), q(name))]
    guest(case.directory, lines, runtime_root=root, GIT_BIN=str(wrapper), SMALL_VIEWS_REAL_GIT=real_git,
          SMALL_VIEWS_GIT_TRACE=str(trace), SMALL_VIEWS_GIT_OUTPUTS=str(outputs),
          PATH=str(wrapper.parent) + os.pathsep + os.environ["PATH"], **case.settings)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    expected = {"pins", "effect-receipts", "recent-proposals", "relevant-files", "conversation"}
    if not native:
        expected |= {"work-state", "affect-gestalt"}
    for row in rows:
        assert row["loader_calls"] == {key: 1 for key in expected}, row["loader_calls"]
        assert row["text"].count("status=known") == 13
        boundaries = {"action-strategy": 1, "pins-engine": 1, "prepare": 1,
                      "retained-load": 5 if native else 7, "engine-command": 1}
        if native:
            boundaries["marker/work-state"] = 1
        assert row["boundaries"] == boundaries, row["boundaries"]
    micro = [json.loads(line) for line in profiles.read_text().splitlines()]
    assert all("Error" not in row["value"] for row in micro), "profile failure is not a timing pass"
    result = {"route": "after" if native else "before", "process": process, "program": lines,
              "samples": rows, "profiles": micro, "git_trace": trace.read_text()}
    save_evidence("goal-bench-" + label, result)
    return result


def run(args, evidence):
    import context_bundle_transcripts  # noqa: F401
    engine = engine_info(os.environ.get("CETTA_BIN", "cetta"),
        "Reviewer-specified project-04 TEST engine; pending pidfd/follow-modified per task, not independently build-certified")
    case = SmallViewsTest()
    case.setUp()
    try:
        step, manifest = cold_fixtures(case)
        value = json.loads(step.read_text())
        for key in ("work-state", "affect-gestalt"):
            value["retained_views"].pop(key)
        goals_path = case.directory / "goals.metta"
        value["env"]["METTACLAW_GOALS_PATH"] = str(goals_path)
        value["files"][str(goals_path)] = encoded(("(goal-budget 3.0)\n" + "\n".join(
            goal("fixture-%02d" % i, sti=str(.00015 if i % 2 else .85),
                 blocked="fixture-review" if i % 3 == 0 else "none", verified="never" if i % 2 else "2026-10-25",
                 note='"Generated 日 🙂 bounded view; evidence remains explicit. ' + "note " * 24 + '"') for i in range(12))
            + "\n(goal-flag fixture-00 stuck)\n").encode())
        value["files"][str(case.directory / "goals_kernel.json")] = encoded({"mood": {
            dim: number for dim, number in zip(("Cn", "C", "Ct", "I", "J", "A", "S", "Co", "Sp"),
                [.5, .00005, .00015, .75, .25, .69, .8, 1e-5, 1.00005])}})
        value["files"][str(case.directory / "goals_compost.log")] = encoded(("\n".join(
            "2026-10-%02dT00:00:00 solved %s" % (i+1, goal("compost-%d" % i)) for i in range(10)) + "\n").encode())
        step.write_text(json.dumps(value))
        manifest["goal_fixture_files_base64"] = {key: val for key, val in value["files"].items() if Path(key).name.startswith("goals")}
        wrapper, real_git = make_git_wrapper(case.directory)
        before = archive(case.directory)
        results = []
        for process in range(args.processes):
            pair = [measured(case, step, root, native, process, args, wrapper, real_git)
                    for root, native in ((before, False), (None, True))]
            assert [row["text"] for row in pair[0]["samples"]] == [row["text"] for row in pair[1]["samples"]], "complete caller byte parity"
            results.extend(pair)
        summaries = []
        for route in ("before", "after"):
            for workload in ("cold", "warm"):
                values = [row["ms"] for result in results if result["route"] == route
                          for row in result["samples"] if row["workload"] == workload]
                summaries.append({"route": route, "workload": workload, "samples_ms": values,
                                  "median_ms": statistics.median(values), "min_ms": min(values), "max_ms": max(values)})
        assert digest(engine["binary"]) == engine["sha256"]
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, env=guest_env(case.directory),
                                  check=True, capture_output=True, text=True).stdout.strip()
        sources = {str(path.relative_to(ROOT)): digest(path) for path in
                   [*sorted((ROOT / "src").glob("goal_views*")), ROOT / "src/context_sources_cetta.metta", ROOT / "src/skills.metta"]}
        report = {"engine": engine, "baseline": BASELINE, "after_head": revision,
                  "after_source_sha256": sources, "python": sys.executable,
                  "rlimit_as": resource.getrlimit(resource.RLIMIT_AS), "parameters": vars(args),
                  "fixture": manifest, "summaries": summaries, "runs": results,
                  "method": "EVERY cold caller sample waits >2s outside timer; 12 actual Git probes/refresh, zero warm; four checkouts and 24 reports. Profile samples are separate WARM calls."}
        (evidence / "goal-measurements.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(summaries, indent=2), flush=True)
    finally:
        case.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processes", type=int, default=3)
    parser.add_argument("--cold-samples", type=int, default=5)
    parser.add_argument("--warm-samples", type=int, default=5)
    parser.add_argument("--profile-samples", type=int, default=10)
    parser.add_argument("--expiry-wait", type=float, default=2.1)
    args = parser.parse_args()
    assert args.expiry_wait > 2 and min(args.processes, args.cold_samples, args.warm_samples, args.profile_samples) > 0
    evidence = Path(os.environ["CONTEXT_BUNDLE_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=True)
    assert not (evidence / "goal-measurements.json").exists(), "use a new evidence directory"
    bound = 25165824 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
    with tempfile.TemporaryDirectory(prefix="goal-views-bench-") as temp:
        env = isolated_environ(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, TMPDIR=temp)
        with mock.patch.dict(os.environ, env, clear=True):
            run(args, evidence)


if __name__ == "__main__":
    main()
