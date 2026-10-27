#!/usr/bin/env python3
"""Replay the typed provider contract through actual MeTTa and audited Lean.

This establishes a pinned finite correspondence, not full interpreter, C or
filesystem verification. Independent native fault tests cover publication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--cetta-root", required=True, type=Path)
    parser.add_argument("--lean", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    programs = ["src/knowledge_store_cetta.metta", "src/provider_domain_cetta.metta", "src/usage_evidence_cetta.metta",
                "tests/support/knowledge_store_probe.metta", "tests/support/provider_domain_probe.metta",
                "tests/support/knowledge_store_fault.c", "tests/test_knowledge_store_cetta.py",
                "tests/test_provider_domain_cetta.py", "tests/test_provider_domain_failures_cetta.py",
                "tests/test_provider_domain_reference_cetta.py"]
    before = {name: digest(root / name) for name in programs}
    references = {name:digest(root / name) for name in ("src/llm_policy.py", "src/native_lifetime.py")}
    checker_before = digest(Path(__file__))
    worker_before, model_before = digest(args.worker), digest(args.model)
    lean_before = digest(args.lean)
    lean_version = subprocess.check_output([str(args.lean.resolve()), "--version"], text=True).strip()
    source = args.model.read_text()
    if re.search(r"\b(sorry|axiom|unsafe|native_decide)\b|#eval!", source):
        raise AssertionError("model contains an unaudited proof escape")
    names = re.findall(r"^theorem (\w+)", source, re.M)
    if not names:
        raise AssertionError("no declarations to audit")
    audit = "\n".join("#print axioms Mettapedia.Machines.NativeProviderDomain." + name for name in names)
    with tempfile.TemporaryDirectory(prefix="native-provider-model-") as directory:
        path = Path(directory) / "ProviderContract.lean"
        path.write_text(source + "\n" + audit + "\n")
        run = subprocess.run([str(args.lean.resolve()), "-j1", str(path)], capture_output=True,
                             text=True, timeout=120, env=dict(os.environ, LEAN_NUM_THREADS="1"))
    if run.returncode or "warning:" in run.stdout or "error:" in run.stdout:
        raise RuntimeError(run.stdout + run.stderr)
    axioms = re.findall(r"depends on axioms: \[([^\]]*)\]", run.stdout)
    free = re.findall(r"does not depend on any axioms", run.stdout)
    if len(axioms) + len(free) != len(names):
        raise AssertionError(("missing declaration audit", len(names), run.stdout))
    for entry in axioms:
        if not set(filter(None, map(str.strip, entry.split(",")))).issubset({"propext", "Classical.choice", "Quot.sound"}):
            raise AssertionError(("unexpected axiom", entry))
    expected = [line for line in run.stdout.splitlines() if line.startswith("NATIVE_PROVIDER_TRACE ")]
    expected_states = [line for line in run.stdout.splitlines() if line.startswith("NATIVE_PROVIDER_STATES ")]
    if len(expected) != 1 or len(expected_states) != 1:
        raise AssertionError(("missing model trace", run.stdout))

    os.environ["CETTA_VALUE_WORKER"] = str(args.worker.resolve())
    os.environ["CETTA_CHANNEL_ROOT"] = str(args.cetta_root.resolve())
    sys.path.insert(0, str(root / "tests"))
    import test_provider_domain_cetta as fixture
    E, S, encode = fixture.E, fixture.S, fixture.encode
    cases = []

    def fresh():
        case = fixture.ProviderDomainTests()
        case.setUp()
        cases.append(case)
        return case

    def label(result):
        outcome = result[1]
        mapping = {"ProviderCompletedV2": "completed", "ProviderAlreadyCompletedV2": "already-completed", "ProviderUsageRecordedV2": "recorded",
                   "ProviderNotOwnedV2": "not-owned", "ProviderAnswerConflictV2": "answer-conflict"}
        if outcome[0].name not in mapping:
            raise AssertionError(("unexpected native outcome", result))
        status = {"ProviderUsageAcceptedV2": "accepted", "ProviderUsageConflictV2": "conflict"}[outcome[1].name]
        prefix = "replayed:" if result[0] == S("KnowledgeReplayedV1") else ""
        if result[0] not in (S("KnowledgeReplayedV1"), S("KnowledgeCommittedV1")):
            raise AssertionError(result)
        return prefix + mapping[outcome[0].name] + ":" + status

    def state(case, attempt="attempt"):
        submitted = case.value(E("ProviderAttemptKeyV2", "call", attempt))
        if submitted[0] != S("KnowledgePresentV1") or submitted[1][0] != S("ProviderSubmittedAttemptV2"):
            raise AssertionError(("missing submitted-attempt projection", submitted))
        usage_key = case.usage_key(attempt=attempt, transport=submitted[1][1])
        points = case.values([E("ProviderLeaseKeyV2", "call"), usage_key, E("ProviderAnswerKeyV2", "call")])
        def present(value):
            if value == S("KnowledgeAbsentV1"):
                return None
            if value[0] != S("KnowledgePresentV1"):
                raise AssertionError(value)
            return value[1]
        lease, usage, answer = map(present, points)
        phases = {"ProviderLeaseActiveV2": "active", "ProviderLeaseCompletedV2": "completed",
                  "ProviderLeaseUnknownV2": "unknown", "ProviderLeaseFailedV2": "failed"}
        lease_label = "absent" if lease is None else lease[1] + ":" + phases[lease[2].name]
        def counter(value):
            if value == S("ProviderCountUnknownV2"):
                return "?"
            if value[0] != S("ProviderCountV2") or type(value[1]) is not int:
                raise AssertionError(value)
            return str(value[1])
        usage_label = "absent" if usage is None else ",".join(map(counter, usage[1:]))
        if answer is None:
            answer_label = "absent"
        else:
            if answer != E("ProviderAnswerV2", case.submission(), E("AnswerRef", "answer")):
                raise AssertionError(("unexpected answer identity/reference", answer))
            answer_label = "transport:answer"
        return lease_label + "|" + usage_label + "|" + answer_label

    try:
        case = fresh()
        command = case.finish(case.usage(100, 7))
        first = case.execute(command, 1)
        first_state = state(case)
        second = case.execute(case.record(case.usage(100, 7)), 2)
        second_state = state(case)
        third = case.execute(case.record(case.usage(101, 7)), 3)
        third_state = state(case)
        already_completed = case.execute(command, 8)
        already_state = state(case)
        other_answer = case.execute(case.finish(case.usage(100, 7), reference=E("AnswerRef", "different")), 4)
        other_state = state(case)
        case.put([(E("ProviderLeaseKeyV2", "call"), E("ProviderLeaseV2", "other", S("ProviderLeaseActiveV2")))])
        before_replay = encode(case.audit())
        replay = case.execute(command, 1)
        replay_state = state(case)
        if encode(case.audit()) != before_replay:
            raise AssertionError("receipt replay changed later state")
        case.register(attempt="second-owner", transport=case.submission())
        transfer_completion = case.execute(case.finish(case.usage(100, 7), attempt="second-owner"), 9)
        transfer_state = state(case, "second-owner")
        stale_case = fresh()
        stale_case.put([(E("ProviderLeaseKeyV2", "call"), E("ProviderLeaseV2", "other", S("ProviderLeaseActiveV2")))])
        stale = stale_case.execute(command, 1)
        stale_state = state(stale_case)
        stale_case.same(stale_case.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))
        conflict_case = fresh()
        conflict_case.execute(conflict_case.record(conflict_case.usage(101, 7)), 2)
        conflict_completion = conflict_case.execute(command, 1)
        conflict_state = state(conflict_case)
        conflict_case.same(conflict_case.value(case.usage_key()),
                           E("KnowledgePresentV1", conflict_case.usage(101, 7)))
        native = "NATIVE_PROVIDER_TRACE " + " ".join(map(label, [first, second, third, stale,
                         conflict_completion, other_answer, replay, already_completed, transfer_completion]))
        if expected != [native]:
            raise AssertionError(dict(lean=expected, native=native))
        native_states = "NATIVE_PROVIDER_STATES " + " ".join([first_state, second_state, third_state, stale_state,
                         conflict_state, other_state, replay_state, already_state, transfer_state])
        if expected_states != [native_states]:
            raise AssertionError(dict(lean=expected_states, native=native_states))
    finally:
        for case in cases:
            if not case.doCleanups():
                raise AssertionError("native fixture cleanup failed")
    if before != {name: digest(root / name) for name in programs} or worker_before != digest(args.worker) or model_before != digest(args.model):
        raise AssertionError("qualification source changed")
    if references != {name:digest(root / name) for name in references} or checker_before != digest(Path(__file__)) or lean_before != digest(args.lean):
        raise AssertionError("qualification reference/checker/compiler changed")
    receipt = dict(trace=native, states=native_states, lean_declarations=len(names), program_sha256=before,
                   reference_sha256=references, checker_sha256=checker_before,
                   lean_binary_sha256=lean_before, lean_version=lean_version,
                   worker_sha256=worker_before, model_sha256=model_before,
                   scope="typed focused application laws and actual native fact/commit trace; full MeTTa/C/I/O refinement and live integration remain open")
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
