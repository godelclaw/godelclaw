#!/usr/bin/env python3
"""Check executable Lean admission contracts against host decisions and SQL replays.

This is finite implementation correspondence, not verification of Python,
SQLite, transport code, or the declared observation providers themselves.
"""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import admission_frontier as frontier
from observed_strategy import Ledger


def boolean(value):
    return "true" if value else "false"


def optional(value):
    return "none" if value is None else "some " + str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    models = args.model_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    model = models / "AdmissionFrontier.lean"
    toolchain = (models.parent / "mettapedia/lean-toolchain").read_text().strip()
    env = dict(os.environ, LEAN_PATH=str(output), LEAN_NUM_THREADS="1")
    command = ["nice", "-n", "19", "lean", "+" + toolchain, "-j", "1"]
    built = subprocess.run(command + ["-o", str(output / "AdmissionFrontier.olean"), str(model)],
                           cwd=models, env=env, capture_output=True, text=True)
    (output / "model.log").write_text(built.stdout + built.stderr)
    if built.returncode:
        print(built.stdout + built.stderr)
        return built.returncode

    cases, answers = [], []
    for available, paused, pending, allowance, cooldown in itertools.product(
            (False, True), (False, True), (None, 0), (None, 0, 1), (0, 2)):
        cases.append("{ available := %s, paused := %s, pending := %s, allowance := %s, cooldown := %s, now := 1 }" %
                     (boolean(available), boolean(paused), optional(pending), optional(allowance), cooldown))
        code = frontier.thought_denial(available=available, paused=paused, pending=pending,
                                      allowance=allowance, cooldown=cooldown, now=1)
        answers.append("none" if code is None else "some ." + {
            "request-not-selectable": "unavailable", "cognition-paused": "paused",
            "provider-in-flight": "pending", "allowance-exhausted": "allowance",
            "admission-cooldown": "cooldown"}[code])
    body = ["import AdmissionFrontier", "open AdmissionFrontier", "set_option maxRecDepth 4096", "",
        "def controlCases : List Controls := [" + ",\n".join(cases) + "]",
        "theorem host_thought_decisions_match : controlCases.map thoughtDenial = [" +
        ",".join(answers) + "] := by decide"]

    decision_cases, decisions = [], []
    for read, status, receipt, observed, kind, exclude in itertools.product(
            (False, True), ("issued", "uncertain", "returned"), (False, True),
            (False, True), (False, True), (None, 0, 1)):
        row = dict(ticket=0, command=["help"] if read else ["shell", "effect"],
                   status=status, receipt="receipt" if receipt else None, observed=observed)
        observation = ["help"] if kind else ["shell", "fresh"]
        decision_cases.append("([{ ticket := 0, kind := .%s, status := .%s, receipt := %s, observed := %s }], .%s, %s)" %
            ("observation" if read else "effect", status, boolean(receipt), boolean(observed),
             "observation" if kind else "effect", optional(exclude)))
        decisions.append(boolean(frontier.may_issue([row], observation, exclude)))
    body += ["def effectCases : List (List Attempt × Kind × Option Nat) := [" + ",\n".join(decision_cases) + "]",
        "theorem host_effect_decisions_match : effectCases.map (fun c => mayIssue c.1 c.2.1 c.2.2) = [" +
        ",".join(decisions) + "] := by decide"]

    prefix_cases, prefix_answers = [], []
    for size in range(5):
        for reads in itertools.product((False, True), repeat=size):
            for limit in range(6):
                commands = [["help"] if read else ["shell", "effect"] for read in reads]
                prefix_cases.append("([" + ",".join(".observation" if r else ".effect" for r in reads) +
                                    "], " + str(limit) + ")")
                prefix_answers.append(".observation" if frontier.observational(
                    dict(commands=commands, limit=limit)) else ".effect")
    body += ["def prefixCases : List (List Kind × Nat) := [" + ",\n".join(prefix_cases) + "]",
        "theorem host_prefix_decisions_match : prefixCases.map (fun c => prefixKind c.1 c.2) = [" +
        ",".join(prefix_answers) + "] := by decide"]

    router = {key: ["help", "shell", "recent-messages"] for key in ("advertised", "executable", "permitted")}
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        base = Path(temporary)
        def new_store(name):
            store = Ledger(base / (name + ".sqlite3"))
            store.accept("task", "work")
            return store

        def capture(store):
            return store.capture("task", "context", router, 0)

        def issue(store, command):
            snap = capture(store)
            if snap is None:
                raise AssertionError("provider admission was incorrectly denied")
            return store.decide(snap["serial"], dict(kind="command", command=command,
                owner=snap["owner"], request="task", tool=frontier.admitted_prefix(command)[0][0]), 0)

        def projection(store):
            value = store.projection()
            rows, meta = value["actions"], value["meta"]
            probe = Ledger(base / "probe.sqlite3")
            with sqlite3.connect(store.path) as source, sqlite3.connect(probe.path) as dest:
                source.backup(dest)
            snap = capture(probe)
            can_capture = snap is not None
            can_effect = False
            if snap:
                outcome = probe.decide(snap["serial"], dict(kind="command", command=["shell", "fresh"],
                    owner=snap["owner"], request="task", tool="shell"), 0)
                can_effect = outcome not in ("blocked-effects", "denied", "stale", "ignored")
            return [meta["next_sample"], int(meta["pending"] is not None), len(rows),
                sum(r["status"] == "uncertain" for r in rows), sum(r["receipt"] is not None for r in rows),
                sum(bool(r["observed"]) for r in rows), int(can_capture), int(can_effect)]

        read = new_store("read")
        issue(read, dict(commands=[["recent-messages", "room", 6], ["shell", "deferred"]], limit=1))
        read.restore()
        replays = [projection(read)]
        issue(read, ["shell", "fresh"])
        replays.append(projection(read))
        effect = new_store("effect")
        ticket = issue(effect, ["shell", "effect"])
        effect.restore()
        replays.append(projection(effect))
        assert issue(effect, ["shell", "fresh"]) == "blocked-effects"
        replays.append(projection(effect))
        issue(effect, ["help"])
        replays.append(projection(effect))
        effect2 = new_store("effect2")
        ticket2 = issue(effect2, ["shell", "effect"])
        effect2.restore()
        effect2.report(ticket2, "late receipt")
        replays.append(projection(effect2))
        effect2.observe([ticket2], {ticket2: [{"ticket": ticket2, "payload_digest": "forged"}]})
        replays.append(projection(effect2))
        result = effect2.protected("task")["results"][0]
        effect2.observe([ticket2], {ticket2: [result]})
        replays.append(projection(effect2))
        issue(effect2, ["shell", "fresh"])
        replays.append(projection(effect2))
    body.append("theorem durable_host_replays_match : replayProjections = " + json.dumps(replays) + " := by decide")
    theorem_names = re.findall(r"^theorem (\w+)", model.read_text(), re.M)
    body += ["#print axioms AdmissionFrontier." + name for name in theorem_names]
    body += ["#print axioms " + name for name in ("host_thought_decisions_match",
        "host_effect_decisions_match", "host_prefix_decisions_match", "durable_host_replays_match")]
    source = output / "AdmissionCorrespondence.lean"
    source.write_text("\n\n".join(body) + "\n")
    checked = subprocess.run(command + [str(source)], cwd=models, env=env, capture_output=True, text=True)
    log = checked.stdout + checked.stderr
    (output / "correspondence.log").write_text(log)
    axioms = set()
    for group in re.findall(r"depends on axioms: \[([^\]]*)\]", log):
        axioms.update(w.strip() for w in group.split(",") if w.strip())
    audited = re.findall(r"'([^']+)' (?:depends on axioms|does not depend)", log)
    success = not checked.returncode and len(audited) == len(theorem_names) + 4 and not (
        axioms - {"propext", "Classical.choice", "Quot.sound"})
    receipt = dict(passed=success, thought_cases=len(cases), effect_cases=len(decision_cases),
        prefix_cases=len(prefix_cases), durable_replays=len(replays), model_theorems=len(theorem_names),
        audited=len(audited), axioms=sorted(axioms),
        source_sha256={str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name:
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (model, ROOT / "src/admission_frontier.py", ROOT / "src/observed_strategy.py", Path(__file__))})
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    if not success:
        print(log)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
