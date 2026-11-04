"""An offline stand-in for an attached agent: control service, mail client and display feed.

Nothing here reaches a live agent, mail service or provider. `build()` writes an
instance profile whose control process and mail client are small local scripts,
and `SCRIPT` is a recorded-shape work stream (synthetic content) that `feed()`
appends to the display directory the console follows.

Run directly to try a console against the fixture in the current terminal:

    python3 tests/console_fixture.py [--console PATH_TO_agent_console.py] [--speed 1.0]
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]

CONTROL = r'''import json, sys
status = {"schema": 1, "call": {"phase": "idle"}, "turnPending": False,
          "humanInbox": {"pending": 2, "sampleAgeSeconds": 1},
          "model": {"name": "fixture-model-large", "reasoningEffort": "high"}}
menu = {"kind": "menu", "toast": "", "text": "Model: fixture-model-large · tap to switch",
        "rows": [[["fixture-model-large [selected]", "model:fixture-model-large"]],
                 [["fixture-model-small", "model:fixture-model-small"]], [["cancel", "cancel"]]]}
print("CLAW_CONTROL_READY", flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["kind"] == "status":
        body = status
    elif request["kind"] == "choice":
        body = {"kind": "text", "text": "Selected " + request["value"]}
    elif request.get("name") == "/models":
        body = menu
    elif request.get("name") == "/activity":
        body = {"kind": "text", "text": "Activity: one work session running; 2 inputs queued."}
    else:
        body = {"kind": "text", "text": "fixture: " + request.get("name", "")}
    print("CLAW_CONTROL " + json.dumps({"id": request["id"], "body": body}), flush=True)
'''

MAIL = r'''import json, sys, time
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
if args[1] == "send":
    identity, sender, recipient, text = args[3:7]
    (root / "outbox").mkdir(exist_ok=True)
    (root / "outbox" / (identity + ".json")).write_text(json.dumps({"id": identity, "text": text}))
    (root / "reply.json").write_text(json.dumps({"status": "delivery", "id": "reply-" + identity,
        "from": recipient, "text": "Noted: " + text.splitlines()[0][:60]}))
    print(json.dumps({"status": "accepted", "id": identity}))
elif args[1] == "wait":
    reply = root / "reply.json"
    if reply.exists():
        print(reply.read_text()); reply.unlink(); sys.exit(0)
    time.sleep(1); print(json.dumps({"status": "idle"})); sys.exit(3)
else:
    print(json.dumps({"status": "settled", "id": args[-1]}))
'''


def build(base):
    """Write a self-contained instance profile under `base`; return its paths."""
    base = Path(base)
    instance, state, display, client = base / "instance", base / "state", base / "display", base / "client"
    for path in (instance, state, display, client):
        path.mkdir(parents=True, exist_ok=True)
    (instance / "control.py").write_text(CONTROL)
    runner = instance / "run.sh"
    runner.write_text("#!/bin/sh\nexec " + json.dumps(sys.executable) + " -I " + json.dumps(str(instance / "control.py")) + "\n")
    runner.chmod(0o700)
    mail = base / "mail.py"
    mail.write_text(MAIL)
    profile = base / "instances.json"
    profile.write_text(json.dumps({"instances": [{"name": "godel", "root": str(instance), "state_dir": str(state)}]}))
    return dict(base=base, instances=profile, display=display, client=client, mail=mail)


def console_args(paths, console=None):
    return [sys.executable, str(console or ROOT / "scripts/agent_console.py"), "--agent", "godel",
            "--instances", str(paths["instances"]), "--dir", str(paths["display"]),
            "--client-state", str(paths["client"]), "--mail-script", str(paths["mail"])]


AGENT, WORK = "godel", "work-20261104-225522-a6fe"
TASK = ("TASK 1 of 4: port the processed-inputs part of the input ledger to a native store. "
        "Workdir /srv/work/ledger. Do not commit; leave changes in the worktree.")
GREP = ('cd /srv/work/ledger && rg -n "db:transaction |db:snapshot" docs src | head -20; '
        'ls src | head -50')
GREP_OUT = "".join("docs/native_store/PROTOCOL.txt:%d:db:transaction opens a write scope %d\n" % (90 + i, i)
                   for i in range(10))
HEREDOC = ("cat > /srv/work/ledger/scratch/probe.metta <<'EOF'\n!(import! &self (library db))\n"
           "!(let* (($db (db:open \"/srv/work/ledger/scratch/db\"))) (db:close $db))\nEOF\n"
           "cetta /srv/work/ledger/scratch/probe.metta")


def _text(session, attempt, channel, item, text, part=None):
    return dict(kind="text.delta", session=session, attempt=attempt, channel=channel, item=item,
                part=part, text=text, source="provider")


def _attempt(session, attempt, reasoning, commentary, tools, purpose="coding"):
    """One model attempt: streamed reasoning and commentary, then the tool calls it proposed."""
    events = [(0.3, dict(kind="attempt.open", session=session, attempt=attempt, item=attempt, purpose=purpose,
                         model="", status="awaiting")),
              (0.05, dict(kind="attempt.open", session=session, attempt=attempt, item=attempt,
                          model="fixture-model-large", status="awaiting"))]
    for n, word in enumerate(reasoning.split(" ")):
        events.append((0.02, _text(session, attempt, "reasoning-summary", "0", (" " if n else "") + word, part=0)))
    events.append((0.05, _text(session, attempt, "reasoning-summary", "0", "\n\n", part=0)))
    for n, word in enumerate(commentary.split(" ") if commentary else []):
        events.append((0.02, _text(session, attempt, "commentary", "1", (" " if n else "") + word, part=1)))
    for n, name in enumerate(tools):
        events.append((0.05, dict(kind="tool.forming", session=session, attempt=attempt, item=str(2 + n),
                                  name=name, source="provider")))
    texts = [{"item": "answer", "channel": "commentary", "text": commentary},
             {"item": "0", "part": 0, "channel": "reasoning-summary", "text": reasoning + "\n\n"}]
    events.append((0.4, dict(kind="text.reconcile", session=session, attempt=attempt, item="answer",
                             channel="commentary", text=commentary, source="terminal")))
    events.append((0.0, dict(kind="text.snapshot", session=session, attempt=attempt, item="0", part=0,
                             channel="reasoning-summary", text=reasoning + "\n\n", source="terminal")))
    events.append((0.0, dict(kind="attempt.finished", session=session, attempt=attempt, status="completed",
                             reason="tool_calls", texts=texts, source="terminal")))
    return events


def _run(session, item, command, output, code=0, took=0.1, artifact=True, name="exec_command"):
    events = [(0.05, dict(kind="tool.dispatching", session=session, item=item, name=name, status="dispatching",
                          command=command, source="executor", text="")),
              (0.02, dict(kind="tool.started", session=session, item=item, name=name, status="running",
                          command=command, source="executor", text=""))]
    if output:
        events.append((took, dict(kind="tool.output", session=session, item=item, text=output, source="executor")))
    finished = dict(kind="tool.finished", session=session, item=item, status="exited" if name else "returned",
                    exit_code=code, source="executor", text=output, command=command)
    if artifact:
        finished["artifact"] = "/srv/agent/memory/coding/%s/executions/%s.log" % (session, item)
    if not name:
        finished.pop("exit_code")
    events.append((0.02 if output else took, finished))
    return events


SCRIPT = [
    *_attempt(AGENT, "a1", "This is a coding task for the ledger lane, so starting a work session fits.",
              "I'm starting a coding session in the ledger worktree for task 1.", ["metta"], purpose="turn"),
    *_run(AGENT, "g1", "['work', %r]" % TASK, "(STARTED %s)" % WORK, artifact=False, name="", took=0.2),
    (0.2, dict(kind="session.open", session=WORK, parent_session=AGENT, item="session", text=TASK, status="running",
               source="executor")),
    *_attempt(WORK, "w1", "I'm going to begin by checking what's in the workspace.",
              "I'll start by reading the oracle, its tests and the db fixture.", ["exec_command"]),
    *_run(WORK, "x1", GREP, GREP_OUT),
    *_attempt(WORK, "w2", "I'm fixing a heredoc error, since let* needs a proper bindings list. The record shape "
              "with an index on position 1 looks right; I still need to check whether a match inside the "
              "transaction sees the adds made earlier in the same batch.", "", ["exec_command"]),
    *_run(WORK, "x2", HEREDOC, "(Error (let* ...) \"bindings must be a list\")\n", code=1, took=0.4),
    *_attempt(WORK, "w3", "The probe failed as expected; writing the module now.", "Writing the ledger module.",
              ["apply_patch"]),
    (0.1, dict(kind="patch", session=WORK, item="p1", name="apply_patch", status="applied", source="executor",
               text="Success. Updated the following files:\nA src/input_ledger.metta\n")),
    *_attempt(AGENT, "a2", "The session has started and there's nothing new to act on, so I'm waiting for the "
              "worker to finish.", "", ["metta"], purpose="turn"),
    *_run(AGENT, "g2", "['rest']", "(RESTING budget-banked)", artifact=False, name="", took=0.05),
    *_attempt(WORK, "w4", "Running the oracle comparison tests against the new module.", "", ["exec_command"]),
    (0.05, dict(kind="tool.dispatching", session=WORK, item="x3", name="exec_command", status="dispatching",
                command="cd /srv/work/ledger && python3 -m unittest tests/test_input_ledger.py -v",
                source="executor", text="")),
    (0.02, dict(kind="tool.started", session=WORK, item="x3", name="exec_command", status="running",
                command="cd /srv/work/ledger && python3 -m unittest tests/test_input_ledger.py -v",
                source="executor", text="")),
]


def feed(display, script=SCRIPT, speed=1.0, producer="f1x7"):
    """Append the scripted events to one display file, pausing between them."""
    path = Path(display) / ("fixture.%s.jsonl" % producer)
    with path.open("a") as stream:
        for sequence, (delay, event) in enumerate(script, 1):
            if delay and speed:
                time.sleep(delay / speed)
            record = dict(event, producer=producer, sequence=sequence, time=time.time(), epoch="initial",
                          instance=AGENT, version=1)
            record.setdefault("parent_session", "" if record["session"] == AGENT else AGENT)
            stream.write(json.dumps(record) + "\n")
            stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--console", type=Path, help="agent_console.py (or a release's console_entry.py) to run")
    parser.add_argument("--dir", type=Path, help="fixture directory (a new temporary one by default)")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds before the feed starts")
    args = parser.parse_args(argv)
    base = args.dir or Path(tempfile.mkdtemp(prefix="console-fixture-"))
    paths = build(base)
    command = console_args(paths, args.console)
    if args.console and args.console.name == "console_entry.py":
        command[1:2] = ["-I", "-S", "-B", str(args.console)]
    thread = threading.Thread(target=lambda: (time.sleep(args.delay), feed(paths["display"], speed=args.speed)),
                              daemon=True)
    thread.start()
    environment = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin")
    return subprocess.call(command, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
