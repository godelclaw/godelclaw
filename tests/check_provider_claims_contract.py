#!/usr/bin/env python3
"""Audit claim laws and replay their cases through actual MeTTa definitions.

This checks scheduling and recovery decisions, not a transport executor or
an interpreter/filesystem refinement proof. Application fault tests separately
exercise atomic publication and released capacity.
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
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("worker", "cetta-root", "lean", "model"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = ["src/knowledge_store_cetta.metta", "src/provider_domain_cetta.metta",
             "src/usage_evidence_cetta.metta", "src/provider_admission_cetta.metta",
             "src/provider_claims_cetta.metta", "src/provider_settlement_cetta.metta",
             "tests/support/provider_claims_probe.metta", "tests/test_provider_claims_cetta.py",
             "tests/test_provider_settlement_cetta.py", "tests/check_provider_claims_contract.py"]
    pins = {name: digest(root / name) for name in names}
    external = {name: digest(getattr(args, name)) for name in ("worker", "lean", "model")}
    source = args.model.read_text()
    if re.search(r"\b(sorry|axiom|unsafe|native_decide)\b|#eval!", source):
        raise AssertionError("unaudited proof escape")
    declarations = re.findall(r"^theorem (\w+)", source, re.M)
    if not declarations:
        raise AssertionError("no declarations")
    audit = "\n".join("#print axioms Mettapedia.Machines.NativeProviderClaims." + name for name in declarations)
    with tempfile.TemporaryDirectory(prefix="native-claim-contract-") as directory:
        path = Path(directory) / "ClaimContract.lean"
        path.write_text(source + "\n" + audit + "\n")
        result = subprocess.run([str(args.lean.resolve()), "-j1", str(path)], capture_output=True, text=True,
                                timeout=120, env=dict(os.environ, LEAN_NUM_THREADS="1"))
    if result.returncode or "warning:" in result.stdout or "error:" in result.stdout:
        raise AssertionError(result.stdout + result.stderr)
    axioms = re.findall(r"depends on axioms: \[([^\]]*)\]", result.stdout)
    free = re.findall(r"does not depend on any axioms", result.stdout)
    if len(axioms) + len(free) != len(declarations):
        raise AssertionError("incomplete declaration audit")
    for entry in axioms:
        if not set(filter(None, map(str.strip, entry.split(",")))).issubset({"propext", "Classical.choice", "Quot.sound"}):
            raise AssertionError(("unexpected axiom", entry))

    def trace(prefix):
        lines = [line[len(prefix):].split() for line in result.stdout.splitlines() if line.startswith(prefix)]
        if len(lines) != 1:
            raise AssertionError(("missing or ambiguous model trace", prefix))
        return lines[0]

    os.environ["CETTA_VALUE_WORKER"] = str(args.worker.resolve())
    os.environ["CETTA_CHANNEL_ROOT"] = str(args.cetta_root.resolve())
    sys.path.insert(0, str(root / "tests"))
    import test_provider_claims_cetta as fixture
    E, S = fixture.E, fixture.S
    case = fixture.ProviderClaimsTests()
    case.setUp()

    def claim(text):
        identity, activity = text.split(",")
        value = S("ProviderExclusiveActivityV3") if activity == "x" else E("ProviderNamedActivityV3", activity[2:],
                   S("ProviderInteractiveV3" if activity.startswith("i:") else "ProviderBackgroundV3"))
        return E("ProviderActiveClaimV3", identity, "owner", "process", value)

    inputs, decisions, recovery = trace("NATIVE_CLAIM_INPUTS "), [], []
    recovery_inputs = trace("NATIVE_CLAIM_RECOVERY_INPUTS ")
    try:
        for item in inputs:
            policy, active, incoming = item.split("|")
            capacity, reserve = map(int, policy.split(","))
            answer = case.worker.call(E("Eligible", E("ProviderSchedulerPolicyV3", capacity, reserve),
                                        tuple(map(claim, active.split(";"))) if active else (), claim(incoming)))
            if answer not in (S("ProviderClaimEligibleV3"), S("ProviderClaimBusyV3")):
                raise AssertionError(answer)
            decisions.append("eligible" if answer == S("ProviderClaimEligibleV3") else "busy")
        for item in recovery_inputs:
            stage, observation = item.split(",")
            stage_value = {"reserved": S("ProviderClaimReservedV3"), "intent": E("ProviderClaimDispatchV3", "transport"),
                           "ended": E("ProviderClaimEndedV3", S("ProviderClaimCompletedV3"))}[stage]
            observation_value = {"live": "ProviderProcessLiveV3", "exited": "ProviderProcessExitedV3",
                                 "unavailable": "ProviderProcessUnavailableV3"}[observation]
            answer = case.worker.call(E("RecoverDecision", stage_value, S(observation_value)))
            recovery.append({S("ProviderKeepClaimV3"): "keep", S("ProviderReleaseUnsentV3"): "unsent",
                             S("ProviderPreserveUnknownV3"): "unknown"}[answer])
    finally:
        if not case.doCleanups():
            raise AssertionError("fixture cleanup failed")
    if decisions != trace("NATIVE_CLAIM_SCHEDULING ") or recovery != trace("NATIVE_CLAIM_RECOVERY "):
        raise AssertionError(dict(scheduling=decisions, recovery=recovery))
    if pins != {name: digest(root / name) for name in names} or external != {
            name: digest(getattr(args, name)) for name in external}:
        raise AssertionError("program/compiler/worker changed")
    receipt = dict(declarations=len(declarations), scheduling=decisions, inputs=inputs,
                   recovery=recovery, recovery_inputs=recovery_inputs, program_sha256=pins,
                   external_sha256=external, scope=__doc__)
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
