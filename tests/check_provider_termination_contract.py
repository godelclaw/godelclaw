#!/usr/bin/env python3
"""Replay the Lean terminal resource projections through actual MeTTa commits.

This audits the model and compares finite captured-state transitions. It does
not prove the transport observation true, implement an executor, or establish
C/filesystem refinement. Usage merging has separate domain and fault checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("worker", "cetta-root", "lean", "model"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = ["src/provider_settlement_cetta.metta", "src/provider_claims_cetta.metta",
             "src/provider_domain_cetta.metta", "src/knowledge_store_cetta.metta",
             "tests/support/provider_claims_probe.metta", "tests/test_provider_settlement_cetta.py",
             "tests/check_provider_claims_contract.py", "tests/check_provider_termination_contract.py"]
    pins = {name: digest(root / name) for name in names}
    external = {name: digest(getattr(args, name)) for name in ("worker", "lean", "model")}
    with tempfile.TemporaryDirectory(prefix="provider-terminal-model-") as temporary:
        audit = Path(temporary) / "claims.json"
        output = Path(temporary) / "model.txt"
        command = [sys.executable, str(root / "tests/check_provider_claims_contract.py")]
        for name in ("worker", "cetta-root", "lean", "model"):
            command += ["--" + name, str(getattr(args, name.replace("-", "_")).resolve())]
        command += ["--output", str(audit), "--model-output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, LEAN_NUM_THREADS="1"))
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        model_audit = json.loads(audit.read_text())
        model_output = output.read_text()

    def trace(prefix):
        rows = [line[len(prefix):].split() for line in model_output.splitlines() if line.startswith(prefix)]
        if len(rows) != 1:
            raise AssertionError(("missing or ambiguous model trace", prefix))
        return rows[0]

    inputs, expected = trace("NATIVE_TERMINAL_INPUTS "), trace("NATIVE_TERMINAL_STATES ")
    if len(inputs) != len(expected) or not inputs:
        raise AssertionError("incomplete terminal model trace")
    os.environ["CETTA_VALUE_WORKER"] = str(args.worker.resolve())
    os.environ["CETTA_CHANNEL_ROOT"] = str(args.cetta_root.resolve())
    sys.path.insert(0, str(root / "tests"))
    import test_provider_settlement_cetta as fixture
    E, S = fixture.E, fixture.S
    case = fixture.SettlementFixture()
    case.setUp()
    results = []
    reason_names = ["ProviderClaimUnsentV3", "ProviderClaimFailedV3", "ProviderClaimUnknownV3", "ProviderClaimCompletedV3"]
    phase_names = ["ProviderLeaseActiveV2", "ProviderLeaseCompletedV2", "ProviderLeaseFailedV2", "ProviderLeaseUnknownV2"]
    claim_key = E("ProviderClaimKeyV3", "call")
    lease_key = E("ProviderLeaseKeyV2", "call")
    answer_key = E("ProviderAnswerKeyV2", "call")
    intent_key = E("ProviderDispatchKeyV3", "call")
    attempt_key = E("ProviderAttemptKeyV2", "call", "owner")
    index_key = E("ProviderActiveClaimKeyV3", "pool", "call")
    other_key = E("ProviderActiveClaimKeyV3", "pool", "other-call")
    frontier_key = E("ProviderActiveClaimsKeyV3", "pool")
    own = E("ProviderActiveClaimV3", "call", "owner", "process", case.activity("A"))
    other = E("ProviderActiveClaimV3", "other-call", "owner", "process", case.activity("B"))

    def value(observations, key):
        item = observations[key]
        if item == S("KnowledgeAbsentV1"):
            return None
        if item[0] != S("KnowledgePresentV1"):
            raise AssertionError((key, item))
        return item[1]

    try:
        for number, (packet, wanted) in enumerate(zip(inputs, expected)):
            stage, lease, answer, intent, owner, process, operation, indexed = map(int, packet.split(","))
            case.path = case.directory / ("case-" + str(number))
            case.path.mkdir(mode=0o700)
            ready = case.worker.call(E("Create", str(case.path)))
            if ready[0] != S("KnowledgeReadyV1"):
                raise AssertionError(ready)
            state = (S("ProviderClaimReservedV3") if stage == 0 else E("ProviderClaimDispatchV3", "transport")
                     if stage == 1 else E("ProviderClaimEndedV3", S(reason_names[stage - 2])))
            facts = [(E("ProviderCallKeyV2", "call"), E("ProviderCallInfoV2", "p", "m", "work", E("Origin", "fixture"))),
                     (claim_key, E("ProviderClaimV3", "pool", "owner", "process", case.activity("A"), state)),
                     (frontier_key, E("ProviderActiveClaimsV3", (own, other) if stage < 2 else (other,))),
                     (other_key, other)]
            if stage < 2 and indexed:
                facts.append((index_key, own))
            if lease < 5:
                phase = phase_names[[0, 0, 1, 2, 3][lease]]
                facts.append((lease_key, E("ProviderLeaseV2", "successor" if lease == 1 else "owner", S(phase))))
            if answer:
                facts.append((answer_key, E("ProviderAnswerV2", "transport", E("AnswerRef", "existing"))))
            if intent:
                facts += [(intent_key, E("ProviderDispatchIntentV3", "call", "owner",
                                        "other-process" if intent == 3 else "process",
                                        "different" if intent == 2 else "transport")),
                          (attempt_key, E("ProviderSubmittedAttemptV2", "transport"))]
            case.put(facts)
            actor, birth = ("owner" if owner else "stranger"), ("process" if process else "other-process")
            if operation == 0:
                command = E("ProviderAbandonClaimV3", "call", actor, birth, "model")
            else:
                observation = (E("ProviderTransportClosedV3", "transport", S("ProviderFailureKnownV3" if operation == 1 else "ProviderFailureUnknownV3"))
                               if operation < 3 else E("ProviderTransportRunningV3" if operation == 3 else "ProviderTransportUnavailableV3", "transport"))
                command = E("ProviderEndTransportV3", "call", actor, birth, observation, S("ProviderNoUsageV3"), "model")
            reply = case.settlement(command)
            if reply[0] not in (S("KnowledgeCommittedV1"), S("ProviderSettlementRefusedV3")):
                raise AssertionError((packet, "unexpected reply", reply))
            keys = (claim_key, lease_key, answer_key, intent_key, attempt_key, frontier_key, index_key, other_key)
            observed = case.knowledge(E("KnowledgeReadV1", keys, 1048576))
            if observed[0] != S("KnowledgeObservedV1"):
                raise AssertionError(observed)
            points = {point[1]: point[2] for point in observed[1]}
            after = value(points, claim_key)[5]
            code = (0 if after == S("ProviderClaimReservedV3") else 1 if after[0] == S("ProviderClaimDispatchV3")
                    else 2 + [S(name) for name in reason_names].index(after[1]))
            current_lease = value(points, lease_key)
            lease_code = 5 if current_lease is None else (0 if current_lease[1] == "owner" else 1) if current_lease[2] == S(phase_names[0]) else 2 + [S(name) for name in phase_names[1:]].index(current_lease[2])
            frontier = value(points, frontier_key)[1]
            actual = f"{code},{lease_code},{int(any(item[1] == 'call' for item in frontier))},{int(value(points, index_key) is not None)}"
            original = dict(facts)
            for key in (intent_key, attempt_key, answer_key):
                if value(points, key) != original.get(key):
                    raise AssertionError((packet, "lost evidence", key))
            if other not in frontier or value(points, other_key) != other:
                raise AssertionError((packet, "lost unrelated resource"))
            if actual != wanted:
                raise AssertionError((packet, actual, wanted, reply))
            results.append(actual)
    finally:
        if not case.doCleanups():
            raise AssertionError("terminal fixture cleanup failed")
    if pins != {name: digest(root / name) for name in names} or external != {
            name: digest(getattr(args, name)) for name in external}:
        raise AssertionError("program, model, compiler or worker changed during replay")
    receipt = dict(cases=len(inputs), declarations=model_audit["declarations"], inputs=inputs,
                   projected_states=results, program_sha256=pins, external_sha256=external,
                   scope=__doc__)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"cases": len(inputs), "audited_declarations": model_audit["declarations"],
                      "scope": "actual native store transitions; terminal resource projections"}))


if __name__ == "__main__":
    main()
