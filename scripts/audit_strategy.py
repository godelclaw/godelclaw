#!/usr/bin/env python3
"""Check an atomic, read-only ledger snapshot with a Lean trace monitor.

No request text, command arguments or result payloads enter the certificate.
This checks recorded transitions, not physical effects or task-checker truth.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def snapshot(path):
    import sqlite3
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result = {name: [dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
                  for name in ("meta", "requests", "samples", "actions", "events")}
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone()
        result["attempts"] = ([dict(row) for row in db.execute(
            "SELECT * FROM command_attempts ORDER BY ticket,position")] if exists else [])
        return result


def boolean(value):
    if not (type(value) is bool or type(value) is int and value in (0, 1)):
        raise ValueError("invalid boolean")
    return "true" if value else "false"


def export_trace(view):
    """Normalize identities injectively and cross-check events against rows."""
    meta = {r["key"]: json.loads(r["value"]) for r in view["meta"]}
    samples = {r["serial"]: r for r in view["samples"]}
    actions = {r["ticket"]: r for r in view["actions"]}
    by_serial = {r["serial"]: r for r in view["actions"]}
    if len(by_serial) != len(actions):
        raise ValueError("multiple actions for one provider sample")
    requests, tools, modes = {}, {}, {"godelclaw": 0}

    def identity(table, value):
        if not isinstance(value, str):
            raise ValueError("non-string identity")
        if value not in table:
            table[value] = len(table)
        return table[value]

    def natural(value):
        if type(value) is not int or value < 0:
            raise ValueError("invalid natural number")
        return value

    def ticket(value):
        row = actions.get(value)
        if row is None or value != f"{meta['owner']}:{natural(row['serial'])}":
            raise ValueError("uncorrelated action ticket")
        return row["serial"] + 1

    def capture(serial):
        row = samples.get(natural(serial))
        if row is None:
            raise ValueError("capture has no sample")
        snap = json.loads(row["snapshot"])
        if snap["serial"] != serial or snap["owner"] != meta["owner"]:
            raise ValueError("sample owner or serial mismatch")
        request = identity(requests, snap["focus"])
        mode = identity(modes, snap["mode"])
        router = snap["router"]
        executable = [identity(tools, t) for t in router["executable"]]
        permitted = [identity(tools, t) for t in router["permitted"]]
        term = f"⟨{serial}, {request}, {natural(snap['revision'])}, {mode}, {executable}, {permitted}⟩"
        return snap, term

    terms, sequences = [], []
    dispositions, reports, observations, deliveries = {}, set(), set(), set()
    reserved, results, result_order, action_status = set(), set(), {}, {}
    pending = None
    for row in view["events"]:
        event = json.loads(row["event"])
        kind = event["kind"]
        sequences.append(natural(row["sequence"]))
        if kind == "deliver":
            key = event["request"]
            if key in deliveries:
                raise ValueError("duplicate request delivery")
            deliveries.add(key)
            term = f".deliver {identity(requests, key)}"
        elif kind == "control":
            term = f".control {identity(modes, event['mode'])} {boolean(event['paused'])}"
        elif kind == "restore":
            term = ".restore"
            if pending is not None:
                dispositions[pending] = "interrupted"
            pending = None
            action_status = {t: ("uncertain" if s == "issued" else s) for t, s in action_status.items()}
        elif kind == "capture":
            snap, definition = capture(event["serial"])
            if (snap["focus"] != event["focus"] or snap["router"] != event["router"]
                    or snap["boundary_digest"] != event["boundary_digest"]):
                raise ValueError("capture event differs from saved snapshot")
            term = f".capture {definition}"
            pending = event["serial"]
            dispositions[pending] = "pending"
        elif kind == "reply":
            serial, disposition = natural(event["serial"]), event["disposition"]
            dispositions[serial] = disposition
            pending = None
            if disposition == "command":
                action = by_serial.get(serial)
                if action is None:
                    raise ValueError("command reply has no action")
                snap, _ = capture(serial)
                if action["request"] != snap["focus"]:
                    raise ValueError("action belongs to another request")
                proposal = json.loads(action["command"])
                commands = proposal.get("commands", []) if isinstance(proposal, dict) else [proposal]
                if not commands or any(not isinstance(c, list) or not c for c in commands):
                    raise ValueError("invalid saved command batch")
                if str(commands[0][0]) != action["tool"]:
                    raise ValueError("action tool differs from saved command")
                for command in commands:
                    if command[0] not in snap["router"]["executable"] or command[0] not in snap["router"]["permitted"]:
                        raise ValueError("command lacks captured executable permission")
                term = f".command {serial} {ticket(action['ticket'])} {identity(requests, action['request'])} {identity(tools, action['tool'])}"
                action_status[action["ticket"]] = "issued"
            elif disposition in {"stale", "denied", "failure", "text"}:
                if serial in by_serial:
                    raise ValueError("non-command reply issued an action")
                term = f".otherReply {serial}"
            else:
                raise ValueError("unknown reply disposition")
        elif kind in {"command-reserved", "command-result"}:
            key = event["ticket"], natural(event["position"])
            seen = reserved if kind == "command-reserved" else results
            if key in seen:
                raise ValueError("duplicate " + kind)
            seen.add(key)
            if kind == "command-result":
                result_order.setdefault(key[0], []).append(key[1])
            constructor = "reserve" if kind == "command-reserved" else "commandResult"
            term = f".{constructor} {ticket(key[0])} {key[1]}"
        elif kind == "report":
            t = event["ticket"]
            if t in reports:
                raise ValueError("duplicate broker receipt")
            reports.add(t)
            success = event["disposition"] == "returned" and event["domain"] == "success"
            term = f".report {ticket(t)} {boolean(success)}"
            action_status[t] = "returned"
        elif kind == "observe":
            observations.add(event["ticket"])
            term = f".observe {ticket(event['ticket'])}"
        elif kind in {"attest", "finish"}:
            term = f".{kind} {identity(requests, event['request'])} {ticket(event['ticket'])}"
        elif kind == "cancel":
            term = f".cancel {identity(requests, event['request'])}"
        elif kind in {"wake", "compact"}:
            term = ".neutral"
        else:
            raise ValueError("unmodeled event")
        terms.append(term)

    if not terms or len(sequences) != len(set(sequences)) or sequences != sorted(sequences):
        raise ValueError("empty or unordered event history")
    if set(samples) != set(dispositions) or any(samples[s]["status"] != d for s, d in dispositions.items()):
        raise ValueError("sample history differs from saved dispositions")
    if meta["pending"] != pending:
        raise ValueError("saved pending sample differs from events")
    if {r["id"] for r in view["requests"]} != deliveries:
        raise ValueError("request history differs from saved requests")
    attempts = {(r["ticket"], r["position"]): r for r in view["attempts"]}
    if set(attempts) != reserved or {k for k, r in attempts.items() if r["result"] is not None} != results:
        raise ValueError("command attempt history differs from saved attempts")
    for (t, position), attempt in attempts.items():
        proposal = json.loads(actions[t]["command"])
        commands = proposal["commands"] if isinstance(proposal, dict) else [proposal]
        limit = natural(proposal["limit"]) if isinstance(proposal, dict) else len(commands)
        if position >= min(len(commands), limit) or json.loads(attempt["command"]) != commands[position]:
            raise ValueError("reserved command differs from admitted batch")
    action_terms = []
    for action in view["actions"]:
        t = action["ticket"]
        receipt = json.loads(action["receipt"]) if action["receipt"] is not None else None
        if (action_status.get(t) != action["status"] or (t in reports) != (receipt is not None)
                or (t in observations) != bool(action["observed"])):
            raise ValueError("saved action differs from its event history")
        if receipt is not None and receipt["ticket"] != t:
            raise ValueError("receipt ticket differs from its action")
        success = bool(receipt and receipt["disposition"] == "returned" and receipt["domain"] == "success")
        positions = sorted(p for key, p in reserved if key == t)
        action_terms.append(f"⟨{ticket(t)}, {identity(requests, action['request'])}, {boolean(receipt is not None)}, "
                            f"{boolean(action['observed'])}, {boolean(success)}, {positions}, {result_order.get(t, [])}⟩")
    accepted = [identity(requests, r["id"]) for r in view["requests"]]
    active = [identity(requests, r["id"]) for r in view["requests"] if r["status"] == "active"]
    if any(r["status"] not in {"active", "achieved", "cancelled"} for r in view["requests"]):
        raise ValueError("unknown request status")
    pending_term = "none" if pending is None else "some " + capture(pending)[1]
    expected = (f"{{ revision := {natural(meta['revision'])}, mode := {identity(modes, meta['mode'])}, "
                f"paused := {boolean(meta['paused'])}, nextSerial := {natural(meta['next_sample'])}, "
                f"pending := {pending_term}, accepted := {accepted}, active := {active}, "
                "actions := [" + ", ".join(action_terms) + "] }")
    summary = {"events": len(terms), "requests": len(accepted), "active_requests": len(active),
               "samples": len(samples), "actions": len(actions), "observed_results": len(observations),
               "uncertain_effects": sum(a["status"] == "uncertain" for a in view["actions"]),
               "in_flight_effects": sum(a["status"] == "issued" for a in view["actions"]),
               "pending_provider": pending is not None,
               "snapshot_sha256": hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()}
    return terms, expected, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, action="append", required=True)
    parser.add_argument("--model", type=Path, required=True, help="StrategyTraceAudit.lean")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--toolchain", required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = args.model.resolve()
    model_bytes = source.read_bytes()
    frozen = output / "StrategyTraceAudit.lean"
    frozen.write_bytes(model_bytes)
    base = ["nice", "-n", "19", "lean", "+" + args.toolchain.removeprefix("leanprover/lean4:"), "-j", "1"]
    env = dict(os.environ, LEAN_NUM_THREADS="1", LEAN_PATH=str(output))
    command = base + ["-o", str(output / "StrategyTraceAudit.olean"), str(frozen)]
    model = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True)
    (output / "model.log").write_text(model.stdout + model.stderr)
    if model.returncode:
        print(model.stdout + model.stderr)
        return model.returncode
    summaries = []
    failed = False
    for index, path in enumerate(args.ledger):
        try:
            view = snapshot(path)
            terms, expected, summary = export_trace(view)
            certificate = output / f"Trace{index}.lean"
            certificate.write_text("import StrategyTraceAudit\nopen StrategyTraceAudit\n"
                "set_option maxRecDepth 8192\nset_option maxHeartbeats 2000000\n"
                "def events : List Event := [\n" + ",\n".join(terms) + "]\n"
                + "def expected : State := " + expected + "\n"
                "#eval firstFailure {} events 0\n"
                "theorem live_recorded_trace_matches : replay {} events = some expected := by decide\n"
                "theorem live_recorded_trace_admissible : Valid {} events expected :=\n"
                "  (replay_iff_valid {} events expected).mp live_recorded_trace_matches\n"
                "#print axioms live_recorded_trace_admissible\n")
            checked = subprocess.run(base + [str(certificate)], cwd=output, env=env,
                                     capture_output=True, text=True)
            (output / f"trace-{index}.log").write_text(checked.stdout + checked.stderr)
            summary["recorded_safety"] = "pass" if checked.returncode == 0 else "fail"
            first = re.search(r"^\(?some (\d+)\)?\s*$", checked.stdout, re.MULTILINE)
            summary["first_violating_sequence"] = view["events"][int(first[1])]["sequence"] if first else None
            summary["recovery"] = "reconciliation-required" if summary["uncertain_effects"] else "clear"
            summary["model_sha256"] = hashlib.sha256(model_bytes).hexdigest()
            summaries.append(summary)
            failed |= checked.returncode != 0
        except (OSError, ValueError, KeyError) as error:
            summaries.append({"recorded_safety": "unavailable", "reason": str(error)})
            failed = True
    receipt = {"schema": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "snapshots": summaries,
               "scope": "recorded safety; not liveness, checker truth or physical effect verification"}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
