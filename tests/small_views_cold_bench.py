"""Complete caller with independently witnessed cold evidence on EVERY sample.

Use run_small_views_checks.py bench, with a NEW evidence directory. Engine
selection is qualification environment only; no installed setting is changed.
The old small_views_bench.py remains a separate WARM/profiling workload.
"""
import argparse
import hashlib
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
from small_views_bench import BASELINE, fixtures
from test_context_bundle_native import ROOT, HEADER, CALLER, guest, guest_env, q, save_evidence
from test_small_views_native import SmallViewsTest, SOURCES, encoded

SUBMITTED = "696e4016cf0c1ab0fe17fd69c0ab43a7e3e19c83"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], env=guest_env(directory),
                          capture_output=True, text=True, check=True)


def cold_fixtures(case):
    step, _ = fixtures(case)
    value = json.loads(step.read_text())
    projects, argv, inputs = [], [], []
    reports_root = Path(value["env"]["METTACLAW_REPORTS_ROOT"])
    for index in range(4):
        checkout = case.directory / ("checkout" if index == 0 else "checkout-%d" % index)
        if index:
            checkout.mkdir()
            git(checkout, "init", "-b", "fixture-%d" % index)
        tracked = checkout / "README.md"
        tracked.write_text("# Disposable checkout %d\nUnicode 日 🙂\n" % index)
        git(checkout, "add", "README.md")
        git(checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "tracked fixture")
        if index == 2:
            git(checkout, "checkout", "--detach", "HEAD")
        if index == 3:
            tracked.write_text(tracked.read_text() + "tracked dirty fixture\n")
        namespace = "fixture-%d" % index
        reports = reports_root / namespace
        reports.mkdir()
        for number in range(6):
            report = reports / ("2026-10-%02d-review.md" % (number + 1))
            report.write_text("#  Report 日 🙂 %d/%d\t witnessed metadata\n\n" % (index, number)
                              + "Immutable disposable report body.\n" * 20)
            inputs.append(report)
        projects.append({"id": namespace, "report_namespace": namespace,
                         "checkouts": [{"path": str(checkout), "role": "disposable"}]})
        argv.extend([["-C", str(checkout), *args] for args in
                     (("rev-parse", "HEAD"), ("branch", "--show-current"),
                      ("status", "--porcelain=v1", "--untracked-files=no"))])
        inputs.append(tracked)
    value["files"][str(case.registry)] = encoded({"schema": 1, "projects": projects})
    value["git_probe_argv"] = argv
    value["retained_views"] = {
        "work-state": "ACTIVE WORK: verify isolated context projections and retain current evidence.\n" * 8,
        "affect-gestalt": "Stable focus; uncertainty remains explicit; no live effects are authorized.\n" * 4,
        "effect-receipts": "DISPOSABLE RECEIPTS: fixture-only checks passed; deployment is withheld.\n" * 8,
        "conversation": "Operator fixture: compare fresh repository/report evidence, preserve Unicode 日 🙂.\n" * 12,
    }
    step.write_text(json.dumps(value))
    inputs.extend(sorted(case.iter.iterdir()))
    manifest = {str(path.relative_to(case.directory)): {"bytes": path.stat().st_size,
                "sha256": digest(path)} for path in inputs}
    manifest["encoded_policy_inputs"] = value["files"]
    manifest["registry"] = projects
    return step, manifest


def make_git_wrapper(directory):
    real = shutil.which("git")
    assert real
    wrapper = directory / "probe-bin" / "git"
    wrapper.parent.mkdir()
    # Real Git outputs/exit are preserved and privately captured. The fixture
    # Git commands emit text without NUL. Builtin forwarding avoids launching
    # extra cat processes; shell/file logging cost remains on BOTH routes.
    wrapper.write_text('''#!/bin/sh
{ printf 'start\\t%s' "$$"; for arg do printf '\\t%s' "$arg"; done; printf '\\n'; } >> "$SMALL_VIEWS_GIT_TRACE"
"$SMALL_VIEWS_REAL_GIT" "$@" > "$SMALL_VIEWS_GIT_OUTPUTS/$$.stdout" 2> "$SMALL_VIEWS_GIT_OUTPUTS/$$.stderr"
code=$?
forward() {
    while :; do
        IFS= read -r line
        read_code=$?
        printf '%s' "$line"
        [ "$read_code" -eq 0 ] || break
        printf '\\n'
    done < "$1"
}
forward "$SMALL_VIEWS_GIT_OUTPUTS/$$.stdout"
forward "$SMALL_VIEWS_GIT_OUTPUTS/$$.stderr" >&2
printf 'end\\t%s\\t%s\\n' "$$" "$code" >> "$SMALL_VIEWS_GIT_TRACE"
exit "$code"
''')
    wrapper.chmod(0o700)
    return wrapper, real


def archive_runtime(directory, revision, name):
    target = directory / name
    target.mkdir()
    archive = directory / (name + ".tar")
    subprocess.run(["git", "archive", "--format=tar", "--output=" + str(archive), revision],
                   cwd=ROOT, env=guest_env(directory), capture_output=True, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(target, filter="data")
    for probe in ("small_views_probe.py", "small_views_cold_probe.py"):
        shutil.copyfile(ROOT / "tests" / probe, target / "tests" / probe)
    return target


def engine_info(binary, provenance):
    resolved = shutil.which(binary)
    assert resolved, binary
    binary = str(Path(resolved).resolve())
    result = subprocess.run([binary, "--version"], env=isolated_environ(),
                            capture_output=True, text=True, check=True)
    return {"binary": binary, "sha256": digest(binary), "version_command": result.args,
            "version_returncode": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr, "provenance": provenance}


def measured(case, step, root, engine, engine_label, runtime, revision, process, args,
             wrapper, real_git, compatibility=False):
    import test_context_bundle_native as harness
    name = "%s-%s-%d" % (engine_label, runtime, process)
    trace, log = (case.directory / (name + suffix) for suffix in (".git-trace", ".samples"))
    outputs = case.directory / (name + ".git-outputs")
    outputs.mkdir()
    lines = HEADER + CALLER + ["!(import! &self (library system))",
        "!(import! &self ./tests/small_views_cold_probe.py)",
        "!(py-call (small_views_cold_probe.configure %s %d))" % (q(step), runtime != "historical")]
    work = [("prime", 0)]
    if not compatibility:
        work += [("cold", n) for n in range(args.cold_samples)]
        work += [("warm", n) for n in range(args.warm_samples)]
    for workload, sample in work:
        lines += ['!(py-call (small_views_cold_probe.begin %s %d %s))' %
                  (q(workload), sample, args.expiry_wait),
                  '!(let* (($start (system:monotonic-ns)) ($text (addition-context-sources)) '
                  '($end (system:monotonic-ns))) '
                  '(py-call (small_views_cold_probe.record %s $start $end $text)))' % q(log)]
    with mock.patch.object(harness, "ENGINE", engine["binary"]):
        guest(case.directory, lines, runtime_root=root, METTACLAW_OBSERVED_STRATEGY="0",
              GIT_BIN=str(wrapper), SMALL_VIEWS_REAL_GIT=real_git, SMALL_VIEWS_GIT_TRACE=str(trace),
              SMALL_VIEWS_GIT_OUTPUTS=str(outputs),
              PATH=str(wrapper.parent) + os.pathsep + os.environ["PATH"], **case.settings)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    retained = {spec.source_id for spec in harness.context_sources.SOURCE_SPECS}
    if runtime != "historical":
        retained -= set(SOURCES)
    for row in rows:
        assert row["loader_calls"] == {key: 1 for key in retained}, row["loader_calls"]
        if compatibility:
            assert "SOURCE[project-capabilities] status=unavailable" in row["text"]
            assert row["text"].count("status=unavailable") == 1
        else:
            assert row["text"].count("status=known") == 13, row["text"]
        row.update(engine=engine_label, engine_sha256=engine["sha256"], runtime=runtime,
                   runtime_revision=revision, process=process, compatibility_only=compatibility)
    result = {"engine": engine, "runtime": runtime, "revision": revision,
              "program": lines, "samples": rows, "git_trace": trace.read_text()}
    save_evidence("cold-caller-" + name, result)
    print(json.dumps({"engine": engine_label, "runtime": runtime, "process": process,
                      "compatibility_only": compatibility,
                      "samples": [{key: row[key] for key in ("workload", "sample", "ms",
                                  "wait_elapsed_ns", "git_probe_count", "evidence_refresh_count")}
                                  for row in rows]}), flush=True)
    return result


def run(args, evidence):
    # Import the recorder only after choosing a NEW output directory. It also
    # records Git fixture creation, archive commands and engine version exits.
    import context_bundle_transcripts  # noqa: F401
    engines = {"production": engine_info(os.environ.get("CETTA_BIN", "cetta"),
               "Existing installed production qualification binary; not rebuilt or switched")}
    if args.test_engine:
        engines["test"] = engine_info(args.test_engine,
            "Requester checkpoint f8e82ff15df8428b9e797732bad6e25a; reviewer-supplied TEST binary. "
            "Production features, modified metadata and pidfd wakeup are reviewer claims, not a build certification.")
    case = SmallViewsTest()
    case.setUp()
    try:
        step, manifest = cold_fixtures(case)
        wrapper, real_git = make_git_wrapper(case.directory)
        versions = {"historical": BASELINE, "submitted": SUBMITTED}
        if args.revised_revision:
            versions["revised"] = args.revised_revision
        roots = {name: archive_runtime(case.directory, revision, name) for name, revision in versions.items()}
        results = []
        for process in range(args.processes):
            for engine_label, engine in engines.items():
                for runtime, revision in versions.items():
                    if runtime == "revised" and engine_label == "production":
                        continue  # Missing-source output is NEVER a qualified speed sample.
                    results.append(measured(case, step, roots[runtime], engine, engine_label, runtime,
                                            revision, process, args, wrapper, real_git))
        for process in range(args.processes):
            group = [result for result in results if result["samples"][0]["process"] == process]
            reference = [row["text"] for row in group[0]["samples"]]
            for result in group[1:]:
                assert [row["text"] for row in result["samples"]] == reference, "complete caller parity"
        compatibility = []
        if args.revised_revision:
            compatibility.append(measured(case, step, roots["revised"], engines["production"],
                "production", "revised", args.revised_revision, 0, args, wrapper, real_git, True))
        summaries = []
        for engine_label in engines:
            for runtime in versions:
                for workload in ("cold", "warm"):
                    samples = [row for result in results for row in result["samples"]
                               if row["engine"] == engine_label and row["runtime"] == runtime
                               and row["workload"] == workload]
                    if not samples:
                        continue
                    values = [row["ms"] for row in samples]
                    summary = {"engine": engine_label, "runtime": runtime, "workload": workload,
                               "samples_ms": values, "median_ms": statistics.median(values),
                               "min_ms": min(values), "max_ms": max(values),
                               "git_probe_counts": [row["git_probe_count"] for row in samples]}
                    summaries.append(summary)
                    print(json.dumps(summary), flush=True)
        for engine in engines.values():
            assert digest(engine["binary"]) == engine["sha256"], "engine changed during qualification"
        result = {"engines": engines, "runtime_revisions": versions, "fixture": manifest,
                  "parameters": vars(args), "python": sys.executable, "python_version": sys.version,
                  "rlimit_as": resource.getrlimit(resource.RLIMIT_AS),
                  "git_wrapper": wrapper.read_text(), "git_real": real_git,
                  "method": "2+ seconds real wait OUTSIDE every cold complete-caller timing; "
                            "12 witnessed real Git probes per cold sample; zero per warm sample. "
                            "Four disposable checkouts, four report namespaces; wrapper overhead included on every route.",
                  "summaries": summaries, "runs": results, "compatibility_only": compatibility,
                  "parity": "all qualified complete-caller samples byte-equal across runtimes AND engines"}
        (evidence / "cold-caller-measurements.json").write_text(json.dumps(result, indent=2) + "\n")
    finally:
        case.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processes", type=int, default=3)
    parser.add_argument("--cold-samples", type=int, default=5)
    parser.add_argument("--warm-samples", type=int, default=5)
    parser.add_argument("--expiry-wait", type=float, default=2.1)
    parser.add_argument("--test-engine", default=os.environ.get("SMALL_VIEWS_TEST_CETTA_BIN"))
    parser.add_argument("--revised-revision", default=os.environ.get("SMALL_VIEWS_REVISED_REVISION"))
    args = parser.parse_args()
    assert args.expiry_wait > 2 and min(args.processes, args.cold_samples, args.warm_samples) > 0
    evidence = Path(os.environ["CONTEXT_BUNDLE_EVIDENCE"]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    assert not (evidence / "cold-caller-measurements.json").exists(), "use a NEW evidence directory"
    bound = 25165824 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
    with tempfile.TemporaryDirectory(prefix="small-views-cold-") as temp:
        directory = Path(temp)
        deny = directory / "deny-bin"
        deny.mkdir()
        (deny / "tmux").write_text("#!/bin/sh\necho 'BLOCKED: no tmux qualification' >&2\nexit 126\n")
        (deny / "tmux").chmod(0o700)
        env = isolated_environ()
        for name in list(env):
            if name.startswith("GIT_") or any(part in name.upper() for part in
                   ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")):
                env.pop(name)
        env.update(CONTEXT_BUNDLE_EVIDENCE=str(evidence), PYTHONDONTWRITEBYTECODE="1",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   PATH=str(deny) + os.pathsep + env.get("PATH", ""), TMPDIR=temp,
                   XDG_STATE_HOME=str(directory / "state"), XDG_CONFIG_HOME=str(directory / "config"),
                   XDG_CACHE_HOME=str(directory / "cache"))
        with mock.patch.dict(os.environ, env, clear=True):
            run(args, evidence)


if __name__ == "__main__":
    main()
