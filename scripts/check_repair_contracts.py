#!/usr/bin/env python3
"""Compare real host transitions with kernel-checked repair contracts.

Only private fixtures are mutated. Supply the MeTTapedia model directory, a
compatible Lean toolchain and a CeTTa executable. This is finite conformance
checking, not a universal proof of the host, transport or storage engine.
"""
import argparse
from collections import Counter
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
import time
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]


def boolean(value):
    return "true" if value else "false"


def option(value):
    return "none" if value is None else f"(some {value})"


class Names:
    """An injective map over the exact values observed in this finite run."""
    def __init__(self):
        self.values = {}

    def __call__(self, value):
        if value not in self.values:
            self.values[value] = len(self.values) + 1
        return self.values[value]


class Checks:
    def __init__(self):
        self.items = []

    def add(self, group, expression, expected):
        self.items.append((group, expression, expected))


def codepoints(text):
    return "[" + ",".join(str(ord(char)) for char in text) + "]"


def cache(checks):
    import provider_protocol as host
    stable = ["fixed instructions", "fixed capabilities", "fixed context declarations"]
    for dynamic in ("input A", "input B", "time 1 / receipt 9", "time 2 / receipt 10",
                    "literal _cache_boundary_ remains input", "unicode: čas"):
        prompt = host.CACHE_MARKER.join(stable + [dynamic])
        _, body = host.command_body("chat-completions", "fixture", 10, "medium", prompt)
        actual_system, actual_user = body["messages"][0]["content"], body["messages"][1]["content"]
        checks.add("cache-wire", "cachePacket [" + ",".join(map(codepoints, stable)) + "] " + codepoints(dynamic),
                   f"({codepoints(actual_system)}, {codepoints(actual_user)})")


STATUS = {"waiting-user": "waitingUser"}
CHOICE = {"waiting-user": "waitingUser", "defer": "defer"}


def request_term(row, ids, contents):
    return ("({ id := %d, content := %d, kind := .%s, status := .%s, "
            "inputIdentity := %s, operatorChecker := %s, processed := %s } : Request)" % (
        ids(row["id"]), contents(row["content"]), row["kind"],
        STATUS.get(row["status"], row["status"]), boolean(row["id"].startswith("input:")),
        boolean(row["checker"] == "operator"), boolean(row["processed"])))


def requests(folder, checks):
    from observed_strategy import Ledger
    store = Ledger(folder / "requests.sqlite3")
    ids, contents = Names(), Names()
    kinds = ("message", "notification", "task", "goal", "autonomous")
    statuses = ("active", "processed", "waiting-user", "deferred", "addressed", "achieved", "cancelled")
    for kind, status, processed, operator, input_id in itertools.product(kinds, statuses, (0, 1), (0, 1), (0, 1)):
        key = "input:fixture" if input_id else "fixture-work"
        for operation in ("process", "keep", "waiting-user", "defer", "addressed", "reopen"):
            with store.transaction() as db:
                db.execute("DELETE FROM request_context"); db.execute("DELETE FROM requests")
                db.execute("INSERT INTO requests VALUES (?,?,?,?)", (key, "retained original content", status,
                    "operator" if operator else "sha256:fixture"))
                db.execute("INSERT INTO request_context(request,kind,processed) VALUES (?,?,?)", (key, kind, processed))
            before = request_term(store.request_record(key), ids, contents)
            if operation == "process":
                accepted = store.processed(key); expression = f"process {before}"
            else:
                accepted = store.disposition(key, operation, "fixture reason")
                expression = f"dispose {before} .{CHOICE.get(operation, operation)}"
            after = request_term(store.request_record(key), ids, contents)
            checks.add("request", expression, f"({after}, {boolean(accepted)})")
    with store.transaction() as db:
        db.execute("DELETE FROM request_context"); db.execute("DELETE FROM requests")
    for i in range(40):
        store.accept("input:" + str(i), "source:" + str(i) + "x" * 5000)
    view = store.protected("input:0")
    assert len(view["unfinished"]) == 8 and view["unfinished"][0]["id"] == "input:0"
    assert all(len(row["summary"]) <= 180 for row in view["unfinished"])
    assert len(view["request"]) == 4000 and view["request_truncated"]
    assert len(store.request_record("input:0")["content"]) == 4000
    assert store.request_record("input:0")["next_offset"] == 4000
    assert store.request_record("input:0", 4000)["content"] == ("source:0" + "x" * 5000)[4000:]
    rows = list(reversed([store.request_record("input:" + str(i)) for i in range(40)]))
    focus = rows.pop(-1); rows.insert(0, focus)
    rows_term = "[" + ",".join(request_term(row, ids, contents) for row in rows) + "]"
    checks.add("view", f"activeView {rows_term}", "[" + ",".join(
        request_term(store.request_record(row["id"]), ids, contents) for row in view["unfinished"]) + "]")


def native_term(row, ids, epochs, results):
    result = None if row["result"] is None else results(row["result"])
    return ("({ id := %d, epoch := %d, phase := .%s, transport := %s, soft := %s, "
            "bytes := %d, quiet := %d, result := %s, reservation := %d } : NativeCall)" % (
        ids(row["id"]), epochs(row["epoch"]), row["state"], option(row["transport"]),
        boolean(row["soft_elapsed"]), row["bytes"], row["quiet_ms"], option(result), row["reserved"]))


def native(folder, checks):
    import llm_policy
    import native_lifetime as host
    ids, epochs, results = Names(), Names(), Names()
    correlation, epoch = "fixture-call", "fixture-epoch"
    ids(correlation); epochs(epoch)
    def seed(phase):
        with host.transaction() as db:
            db.execute("DELETE FROM native_calls"); db.execute("DELETE FROM requests")
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)",
                (correlation, 1000, 37, "pending", None, "synthetic", "fixture", None))
            db.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,transport,soft_elapsed,bytes,quiet_ms,result) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (correlation, epoch, os.getpid(), "start", 1000, 1000,
                       phase, None if phase == "claimed" else 7, 0, 11, 2,
                       None if phase in ("claimed", "submitted") else "original evidence"))
    def snapshot():
        with host.transaction() as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT n.*,r.reserved FROM native_calls n JOIN requests r ON r.id=n.id").fetchone())
    for phase, matching_id, matching_epoch in itertools.product(
            ("claimed", "submitted", "completed", "late", "failed", "uncertain"), (True, False), (True, False)):
        plan = SimpleNamespace(correlation=correlation if matching_id else "foreign-call",
                               epoch=epoch if matching_epoch else "foreign-epoch", cap=1024)
        for operation in ("submit", "progress", "soft", "completed", "late", "failed", "recover-alive", "recover-dead"):
            seed(phase); before = native_term(snapshot(), ids, epochs, results)
            call_id, call_epoch = ids(plan.correlation), epochs(plan.epoch)
            if operation == "submit":
                host.submitted(plan, 9); expression = f"submitted {before} {call_id} {call_epoch} 9"
            elif operation in ("progress", "soft"):
                soft = operation == "soft"
                host.progress(plan, 123, 45, soft)
                expression = f"progress {before} {call_id} {call_epoch} 123 45 {boolean(soft)}"
            elif operation.startswith("recover"):
                alive = operation == "recover-alive"
                with mock.patch.object(host, "process_start", return_value="start" if alive else None):
                    host.recover()
                expression = f"recover {before} {boolean(alive)}"
                if not alive and phase in ("claimed", "submitted"):
                    with llm_policy.transaction() as db:
                        outcome = db.execute("SELECT status,output,finish,reserved FROM requests").fetchone()
                    assert tuple(outcome) == ("failed", None, "interrupted-unknown", 37)
            else:
                accepted = host.settle(plan, operation, "new evidence")
                expression = f"settle {before} {call_id} {call_epoch} .{operation} (some {results('new evidence')})"
            after = native_term(snapshot(), ids, epochs, results)
            if operation in ("completed", "late", "failed"):
                after = f"({after}, {boolean(accepted)})"
            checks.add("native", expression, after)
        seed(phase); before = native_term(snapshot(), ids, epochs, results)
        plan = SimpleNamespace(correlation="new-call", epoch=epoch, cap=1024)
        with mock.patch.object(host, "process_start", return_value="start"):
            accepted = host.claim(plan)
        checks.add("capacity", f"canClaim [{before}]", boolean(accepted))


def notice_term(row, keys):
    return ("({ update := %d, sendKey := %d, ownedDeleteKey := %d, deleteKey := %s, "
            "message := %s, processed := %s, state := .%s } : Notice)" % (
        int(row["update_id"]), keys(row["send_key"]), keys(delete_identity(row["send_key"])),
        option(None if row["delete_key"] is None else keys(row["delete_key"])),
        option(row["message_id"]), boolean(row["processed"]), row["state"]))


def delete_identity(send_key):
    number = int.from_bytes(hashlib.sha256(("notice-delete:" + send_key).encode()).digest()[:8], "big")
    return send_key.rsplit(".", 1)[0] + ".%020d" % number


def notices(folder, checks):
    import input_ack as host
    import telegram
    import mode_channel
    import durable_telegram as protocol
    keys = Names()
    send_key = "42.0.00000000000000000001"; delete_key = delete_identity(send_key)
    keys(send_key); keys(delete_key)
    store = host._store()
    def seed(state, processed, message, delete):
        with store.transaction() as db:
            db.execute("DELETE FROM input_notices"); db.execute("DELETE FROM processed_inputs")
            db.execute("INSERT INTO input_notices(task,update_id,lane,send_key,created,message_id,processed,delete_key,state) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", ("fixture-notice", "1", "42.0", send_key, 999,
                       message, processed, delete_key if delete else None, state))
    def snapshot():
        with store.transaction() as db:
            return dict(db.execute("SELECT * FROM input_notices").fetchone())
    class Channel:
        def __init__(self): self.intents = []
        def pending(self, limit): return []
        def rpc(self, code, key, body):
            self.intents.append((key, json.loads(body)))
            return protocol.STORED, "", ""
    for state, processed, message, delete in itertools.product(
            ("registered", "submitted", "deleted", "expired"), (0, 1), (None, 501), (False, True)):
        for expired in (False, True):
            seed(state, processed, message, delete)
            if expired:
                with store.transaction() as db: db.execute("UPDATE input_notices SET created=?", (1000 - 172801,))
            before = notice_term(snapshot(), keys)
            channel = Channel(); host.pump(channel)
            target = None if not channel.intents else channel.intents[0][1][0][1]
            if channel.intents: assert channel.intents[0][0] == delete_key
            checks.add("notice-pump", f"pump {before} {boolean(expired)}",
                f"({notice_term(snapshot(), keys)}, {option(target)})")
        for kind in ("send", "send-conflict", "send-negative", "delete", "delete-zero", "foreign", "failed", "malformed", "noninteger"):
            seed(state, processed, message, delete); before = notice_term(snapshot(), keys)
            key = delete_key if kind in ("delete", "delete-zero") else "foreign-key" if kind == "foreign" else send_key
            number = 0 if kind == "delete-zero" else 777 if kind == "send-conflict" else -1 if kind == "send-negative" else 501
            delivered = kind not in ("failed", "noninteger")
            outcome = ["uncertain", "no authority"] if kind == "failed" else ["delivered", True] if kind == "noninteger" else ["delivered", number]
            envelope = kind != "malformed"
            accepted = host.receipt(["delivery", key, 0, 1 if envelope else 0, outcome])
            observation = ("({ key := %d, envelopeValid := %s, deliveredInteger := %s, message := %s } : Receipt)" %
                (keys(key), boolean(envelope), boolean(delivered), f"({number})"))
            checks.add("notice-receipt", f"receipt {before} {observation}",
                f"({notice_term(snapshot(), keys)}, {boolean(accepted)})")
        for durable, exact in itertools.product((False, True), (False, True)):
            seed(state, processed, message, delete); before = notice_term(snapshot(), keys)
            update = "1" if exact else "2"
            with mock.patch.object(mode_channel, "acknowledge_handover", return_value=durable):
                assert telegram.handover_ack(update) == int(durable)
            checks.add("notice-handover", f"acknowledgeNotice {before} [{update}] {boolean(durable)}",
                       notice_term(snapshot(), keys))
    samples = ["", "0", "1,1", "7,11", "['7', '11']",
               "1,,2", "1, 2", "01", "١", "-1", str(2**63 - 1), str(2**63), ",".join(["1"] * 257)]
    for text in samples:
        try: actual = telegram.handover_ids(text)
        except ValueError: actual = None
        expected = "none" if actual is None else "(some [" + ",".join(actual) + "])"
        checks.add("id-validation", "csvIds " + codepoints(text), expected)


def routed_notices(folder, checks):
    """Compose the real input, intent and receipt boundaries across routes.

    The observed MeTTa turn guard decides whether the host handover is called.
    Only the external channel transport is replaced with a failed/successful
    fixture; ledger, ownership, handover and notice policy are actual code.
    """
    import input_ack as host
    import telegram
    import mode_channel
    import durable_telegram as protocol
    store, keys = host._store(), Names()
    guards = {expression: expected == "true" for group, expression, expected in checks.items
              if group == "metta-turn-guard"}
    for lane in ("17.0", "-42.0", "-42.9"):
        chat, thread = lane.split(".")
        route = f"(⟨({chat}), {thread}⟩ : NoticeProtocol.Route)"
        send_key = lane + ".00000000000000000001"
        delete_key = delete_identity(send_key)
        for succeeded, history, handover in itertools.product((False, True), repeat=3):
            boundary = "⟨" + ",".join(map(boolean, (succeeded, history, handover))) + "⟩"
            guard = guards[f"turnGate {boundary} true"]
            with store.transaction() as db:
                db.execute("DELETE FROM input_notices"); db.execute("DELETE FROM processed_inputs")
                db.execute("INSERT INTO input_notices(task,update_id,lane,send_key,created,message_id,state) "
                           "VALUES (?,?,?,?,?,?,?)", ("fixture-notice", "7", lane, send_key, 999, 501, "submitted"))
            def snapshot():
                with store.transaction() as db:
                    return dict(db.execute("SELECT * FROM input_notices").fetchone())
            def term():
                return f"({{route := {route}, notice := {notice_term(snapshot(), keys)}}} : NoticeProtocol.OwnedNotice)"
            class Channel:
                def __init__(self): self.intents = []; self.unavailable = True
                def pending(self, limit): return []
                def rpc(self, code, key, body):
                    assert code == protocol.SUBMIT
                    self.intents.append((key, json.loads(body)))
                    if self.unavailable: raise OSError("fixture transport unavailable")
                    return protocol.STORED, "", ""
            channel = Channel()
            before = term()
            # A visible reply can precede durable settlement. It grants no
            # authority to process this input or delete its queue notice.
            host.pump(channel)
            assert not channel.intents
            checks.add("routed-visible-reply", f"NoticeProtocol.visibleReply {before}", term())
            if guard:
                handover_path = mode_channel.state() / "channel-handover" / "7.json"
                handover_path.parent.mkdir(parents=True, exist_ok=True)
                handover_path.write_text("{}\n")
                assert telegram.handover_ack("7") == 1
                assert not handover_path.exists() and handover_path.with_suffix(".done").exists()
            checks.add("routed-input-commit", f"NoticeProtocol.settleInput {before} [7] {boundary}", term())
            before_retries = term()
            for _ in range(3): host.pump(channel)
            checks.add("routed-failed-retries", f"NoticeProtocol.failedAttempts {before_retries} 3", term())
            assert all(key == delete_key and body == [["delete", 501]] for key, body in channel.intents)
            before_confirmation = term()
            channel.unavailable = False
            count = host.pump(channel)
            if guard:
                assert count == 1 and channel.intents[-1] == (delete_key, [["delete", 501]])
                assert host.receipt(["delivery", delete_key, 0, 1, ["delivered", 0]]) == 1
            else:
                assert count == 0 and not channel.intents
            checks.add("routed-confirmation", f"NoticeProtocol.attempt {before_confirmation} true", term())
            # Later selection of another reply chat is deliberately irrelevant
            # to the captured cleanup route and the already confirmed receipt.
            with mock.patch.object(telegram, "_reply_chat_id", "99"):
                before_later = term(); host.pump(channel)
            checks.add("routed-terminal-retry", f"NoticeProtocol.attempt {before_later} false", term())


def sexpr_at(text, start):
    depth, quoted, escape = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escape: escape = False
            elif char == "\\": escape = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char == "(": depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0: return text[start:index + 1]
    raise ValueError("incomplete source expression")


def metta_guard(cetta, folder, checks):
    source = (ROOT / "src/loop.metta").read_text()
    marker = source.index("($_ (if (and (== $providerSucceeded 1)")
    gate = sexpr_at(source, source.index("(if", marker))
    assert gate.count("(py-call (strategy_host.input_processed))") == 1
    gate = gate.replace("(py-call (strategy_host.input_processed))", "(probe:mark $case)")
    gate = gate.replace("(py-call (cognitive_health.turn_settled))", "1")
    forms = ["!(import! &self str)",
        "(= (probe:mark $case) (let $_ (add-atom &self (Processed $case)) 1))",
        "(= (probe:turn $case $has-activity $providerSucceeded $historyReceipt $activityReceipt) " + gate + ")"]
    flags = list(itertools.product((False, True), repeat=4))
    for index, (activity, succeeded, history, handover) in enumerate(flags):
        forms.append(f"!(probe:turn {index} {'True' if activity else 'False'} {int(succeeded)} {int(history)} {int(handover)})")
    forms.append("!(collapse (match &self (Processed $id) $id))")
    fixture = folder / "TurnGuard.metta"; fixture.write_text("\n".join(forms) + "\n")
    result = subprocess.run([str(cetta), "--lang", "petta", str(fixture)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    (folder / "metta-guard.log").write_text(result.stdout + result.stderr)
    if result.returncode or "Error" in result.stdout or "error" in result.stderr.lower():
        raise RuntimeError("MeTTa turn guard did not execute cleanly")
    actual = {int(value) for value in re.findall(r"\d+", result.stdout.strip().splitlines()[-1])}
    assert actual.issubset(set(range(len(flags))))
    for index, (activity, succeeded, history, handover) in enumerate(flags):
        checks.add("metta-turn-guard", "turnGate ⟨" + ",".join(map(boolean, (succeeded, history, handover))) +
                   "⟩ " + boolean(activity), boolean(index in actual))


def run_lean(base, env, output, path):
    result = subprocess.run(base + [str(path)], cwd=output, env=env, capture_output=True, text=True)
    path.with_suffix(".log").write_text(result.stdout + result.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True, help="MeTTapedia lean/pettaclaw directory")
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--cetta", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / "receipt.json"
    receipt_path.write_text(json.dumps({"status": "incomplete", "scope": "finite host conformance"}) + "\n")
    sources = ("src/observed_strategy.py", "src/native_lifetime.py", "src/input_ack.py", "src/loop.metta",
               "src/agent_checkpoint.py", "src/mode_channel.py", "src/llm_policy.py", "src/provider_protocol.py",
               "src/helper.py", "src/memory.metta", "src/durable_telegram.py",
               "channels/telegram.py", "channels/telegram.metta", "channels/telegram_control.metta",
               "tests/test_telegram_metta_client.py", "scripts/check_repair_contracts.py")
    host_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    base = ["nice", "-n", "19", "lean", "+" + args.toolchain.removeprefix("leanprover/lean4:"), "-j", "1", "-DwarningAsError=true"]
    env = dict(os.environ, LEAN_PATH=str(output), LEAN_NUM_THREADS="1")
    models = ("AsyncWait", "RequestLifecycle", "RepairArchitecture", "NoticeProtocol")
    hashes = {}
    for name in models:
        source = output / (name + ".lean"); source.write_bytes((args.models / source.name).read_bytes())
        hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        result = subprocess.run(base + ["-o", str(source.with_suffix(".olean")), str(source)],
                                cwd=output, env=env, capture_output=True, text=True)
        source.with_suffix(".log").write_text(result.stdout + result.stderr)
        if result.returncode: raise RuntimeError("model failed: " + name + "\n" + result.stdout + result.stderr)
    checks = Checks()
    with tempfile.TemporaryDirectory(dir=output, prefix="fixtures-") as temporary:
        folder = Path(temporary)
        environment = {"METTACLAW_STRATEGY_PATH": str(folder / "notices.sqlite3"),
                       "METTACLAW_LLM_LEDGER_PATH": str(folder / "native.sqlite3"),
                       "METTACLAW_ENGINE_STATE_PATH": str(folder / "engine"),
                       "METTACLAW_TELEGRAM_SERVICE_SOCKET": "",
                       "METTACLAW_LLM_POLICY": "upstream", "METTACLAW_LLM_POLICY_PATH": ""}
        with mock.patch.dict(os.environ, environment), mock.patch.object(time, "time", return_value=1000):
            for name, build in (("requests", requests), ("native lifetimes", native), ("owned notices", notices)):
                build(folder, checks)
                print("Captured actual " + name + ": " + str(len(checks.items)) + " cumulative checks", flush=True)
            metta_guard(args.cetta.resolve(), output, checks)
            routed_notices(folder, checks)
        cache(checks)
    header = "import NoticeProtocol\nopen RepairArchitecture\nset_option maxRecDepth 8192\nset_option maxHeartbeats 2000000\n"
    for start in range(0, len(checks.items), 128):
        part = output / f"Conformance{start // 128}.lean"
        part.write_text(header + "\n".join(f"theorem observed_{i} : {expression} = {expected} := by decide\n#print axioms observed_{i}"
            for i, (_, expression, expected) in enumerate(checks.items[start:start + 128], start)) + "\n")
        result = run_lean(base, env, output, part)
        if result.returncode: raise RuntimeError("host differs from Lean: " + part.name + "\n" + result.stdout + result.stderr)
    theorem_names = []
    for name in models:
        theorem_names += [name + "." + item for item in re.findall(r"^theorem ([A-Za-z0-9_]+)", (output / (name + ".lean")).read_text(), re.M)]
    audit = output / "AxiomAudit.lean"
    audit.write_text(header + "\n".join("#print axioms " + name for name in theorem_names) + "\n")
    result = run_lean(base, env, output, audit)
    axioms = {axiom.strip() for names in re.findall(r"depends on axioms:\s*\[([^]]*)\]", result.stdout)
              for axiom in names.split(",") if axiom.strip()}
    audit_lines = re.findall(r"^'[^']+' (?:does not depend on any axioms|depends on axioms:)", result.stdout, re.M)
    if (result.returncode or len(audit_lines) != len(theorem_names) or
            not axioms.issubset({"propext", "Classical.choice", "Quot.sound"})):
        raise RuntimeError("axiom audit failed")
    request = "({id := 1, content := 2, kind := .message} : Request)"
    call = "({id := 1, epoch := 1, phase := .submitted, reservation := 37} : NativeCall)"
    notice = "({update := 1, sendKey := 1, ownedDeleteKey := 2, message := some 501} : Notice)"
    corruptions = {
        "false-achievement": (f"(process {request}).1.status", "RequestLifecycle.Status.achieved"),
        "soft-cancels-call": (f"(progress {call} 1 1 10 1 true).phase", "Phase.failed"),
        "duplicate-main-call": (f"canClaim [progress {call} 1 1 10 1 true]", "true"),
        "foreign-epoch-settlement": (f"(settle {call} 1 2 .completed (some 1)).2", "true"),
        "delete-unprocessed": (f"(pump {notice} false).2", "(some 501)"),
        "salvage-malformed-ids": ('csvIds [49,44,44,50]', "(some [1,2])"),
        "erase-unknown-reservation": (f"(recover {call} false).reservation", "0"),
        "failed-handover-processes": (f"(acknowledgeNotice {notice} [1] false).processed", "true"),
        "goal-workflow-bypass": ("(dispose ({id := 1, content := 2, kind := .goal} : Request) .addressed).2", "true"),
        "terminal-result-overwrite": ("(settle ({id := 1, epoch := 1, phase := .completed, reservation := 37} : NativeCall) 1 1 .late (some 2)).2", "true"),
        "unbounded-backlog-view": (f"(activeView (List.replicate 32 {request})).length", "32"),
        "changing-context-in-prefix": ("cachedPrefix [67,65,66] [68]", "[65,66]"),
        "visible-reply-processes-input": (f"(NoticeProtocol.visibleReply (⟨⟨-42,0⟩, {notice}⟩ : NoticeProtocol.OwnedNotice)).notice.processed", "true"),
        "group-cleanup-changes-route": (f"(NoticeProtocol.issue (⟨⟨-42,9⟩, {notice}⟩ : NoticeProtocol.OwnedNotice) false).1.route.chat", "17"),
    }
    for index, (name, (expression, bad)) in enumerate(corruptions.items()):
        control = output / f"MustFail{index}.lean"
        control.write_text(header + f"example : {expression} = {bad} := by decide\n")
        rejected = run_lean(base, env, output, control)
        if rejected.returncode == 0 or "decide" not in rejected.stdout or "error:" not in rejected.stdout:
            raise RuntimeError("corrupted observation was not rejected: " + name)
    if host_hashes != {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}:
        raise RuntimeError("source changed during conformance checking; rerun on stable sources")
    receipt = {"schema": 1, "status": "passed", "scope": "finite conformance; not whole-host verification",
               "checked_transitions": len(checks.items), "groups": dict(Counter(item[0] for item in checks.items)),
               "named_model_theorems": len(theorem_names), "rejected_corruptions": list(corruptions),
               "model_sha256": hashes, "host_sha256": host_hashes,
               "cetta_sha256": hashlib.sha256(args.cetta.read_bytes()).hexdigest(),
               "toolchain": args.toolchain, "python_version": sys.version, "uses_native_decide": False,
               "model_axioms": sorted(axioms),
               "assumptions": ["faithful injective fixture encoding", "Lean kernel correctness",
                               "host transactions and transport operate as observed in these fixtures"],
               "does_not_prove": ["arbitrary task completion", "provider eventual reply", "global prompt/token bound",
                                  "all crash windows", "C/Python/SQLite refinement", "physical exactly-once effects"]}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("status", "checked_transitions", "groups", "named_model_theorems", "rejected_corruptions")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
