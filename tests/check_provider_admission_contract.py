#!/usr/bin/env python3
"""Replay audited admission deadline laws through the actual native program.

The contract covers pure decisions and monotone deadline updates. Application
fault/concurrency tests supply separate evidence for commit behavior. This
is not a proof of the interpreter, authenticated broker or filesystem.
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
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--cetta-root", required=True, type=Path)
    parser.add_argument("--lean", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    programs = ["src/knowledge_store_cetta.metta", "src/provider_domain_cetta.metta", "src/usage_evidence_cetta.metta",
                "src/provider_admission_cetta.metta", "tests/support/knowledge_store_probe.metta",
                "tests/support/provider_domain_probe.metta", "tests/support/provider_admission_probe.metta",
                "tests/support/knowledge_store_fault.c", "tests/test_provider_admission_cetta.py",
                "tests/test_provider_admission_failures_cetta.py", "tests/test_provider_admission_reference_cetta.py"]
    pins = {name: digest(root / name) for name in programs}
    pins["checker"] = digest(Path(__file__))
    reference = digest(root / "src/llm_policy.py")
    worker_pin, model_pin, lean_pin = map(digest, (args.worker, args.model, args.lean))
    source = args.model.read_text()
    if re.search(r"\b(sorry|axiom|unsafe|native_decide)\b|#eval!", source):
        raise AssertionError("model contains an unaudited proof escape")
    names = re.findall(r"^theorem (\w+)", source, re.M)
    if not names:
        raise AssertionError("no declarations to audit")
    audits = "\n".join("#print axioms Mettapedia.Machines.NativeProviderAdmission." + name for name in names)
    with tempfile.TemporaryDirectory(prefix="native-admission-model-") as directory:
        path = Path(directory) / "AdmissionContract.lean"
        path.write_text(source + "\n" + audits + "\n")
        run = subprocess.run([str(args.lean.resolve()), "-j1", str(path)], capture_output=True, text=True,
                             timeout=120, env=dict(os.environ, LEAN_NUM_THREADS="1"))
    if run.returncode or "warning:" in run.stdout or "error:" in run.stdout:
        raise RuntimeError(run.stdout + run.stderr)
    axioms = re.findall(r"depends on axioms: \[([^\]]*)\]", run.stdout)
    free = re.findall(r"does not depend on any axioms", run.stdout)
    if len(axioms) + len(free) != len(names):
        raise AssertionError("missing declaration audit")
    for entry in axioms:
        if not set(filter(None, map(str.strip, entry.split(",")))).issubset({"propext", "Classical.choice", "Quot.sound"}):
            raise AssertionError(("unexpected axiom", entry))

    def unique(prefix):
        values = [line[len(prefix):].split() for line in run.stdout.splitlines() if line.startswith(prefix)]
        if len(values) != 1:
            raise AssertionError(("missing/ambiguous model trace", prefix))
        return values[0]

    expected = unique("NATIVE_ADMISSION_DEADLINES ")
    inputs = unique("NATIVE_ADMISSION_INPUTS ")
    extensions = unique("NATIVE_ADMISSION_EXTENSIONS ")
    if len(inputs) != len(expected):
        raise AssertionError("input/result count mismatch")
    os.environ["CETTA_VALUE_WORKER"] = str(args.worker.resolve())
    os.environ["CETTA_CHANNEL_ROOT"] = str(args.cetta_root.resolve())
    sys.path.insert(0, str(root / "tests"))
    import test_provider_admission_cetta as fixture
    E, S = fixture.E, fixture.S
    case = fixture.ProviderAdmissionTests()
    case.setUp()
    native = []
    extension_rows = []
    try:
        for text in inputs:
            profile, now, deadline, maximum = text.split(",")
            cooldown = S("ProviderNoCooldownV2") if deadline == "none" else E(
                "ProviderCooldownV2", case.integer(int(deadline)), S("Source"))
            result = case.worker.call(E("Deadline", S("ProviderManagedV2" if profile == "managed" else "ProviderUpstreamV2"),
                                        case.integer(int(now)), cooldown, case.integer(int(maximum))))
            if result == S("ProviderAdmissionEligibleV2"):
                label = "eligible"
            elif result[0] == S("ProviderAdmissionDeferredV2"):
                label = "deferred:" + str(result[1])
            elif result == E("ProviderAdmissionRefusedV2", S("invalid-maximum")):
                label = "invalid-maximum"
            else:
                raise AssertionError(("unexpected native decision", result))
            native.append(label)
        if expected != native:
            raise AssertionError(dict(lean=expected, native=native))
        for text in extensions:
            proposed, boundary = map(int, text.split(":"))
            answer = case.answer(case.defer(proposed), "deadline-" + str(len(extension_rows)))
            stored = case.value(E("ProviderCooldownKeyV2", "provider"))
            expected_boundary = case.integer(boundary)
            case.same(answer, E("ProviderCooldownRecordedV2", expected_boundary))
            if stored[0] != S("KnowledgePresentV1") or stored[1][0] != S("ProviderCooldownV2"):
                raise AssertionError(stored)
            case.same(stored[1][1], expected_boundary)
            extension_rows.append(text)
    finally:
        if not case.doCleanups():
            raise AssertionError("native fixture cleanup failed")
    after = {name: digest(root / name) for name in programs}
    after["checker"] = digest(Path(__file__))
    if pins != after or reference != digest(root / "src/llm_policy.py"):
        raise AssertionError("application/reference changed")
    if (worker_pin, model_pin, lean_pin) != tuple(map(digest, (args.worker, args.model, args.lean))):
        raise AssertionError("native/model/compiler changed")
    receipt = dict(decisions=native, inputs=inputs, extension_states=extension_rows,
                   lean_declarations=len(names), program_sha256=pins, reference_sha256=reference,
                   worker_sha256=worker_pin, model_sha256=model_pin, lean_sha256=lean_pin,
                   lean_version=subprocess.check_output([str(args.lean.resolve()), "--version"], text=True).strip(),
                   scope="pure admission deadlines and actual native cooldown states; complete application/concurrency/fault gates are separate; no interpreter/C/filesystem proof")
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
