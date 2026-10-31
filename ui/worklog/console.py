"""Attach an input composer to an existing agent without starting cognition."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from terminal_client import Controls, MailSession
from ui.worklog.viewer import Reader, clean


def profile(args):
    config = json.loads(args.instances.read_text()) if args.instances.is_file() else {}
    selected = next((i for i in config.get("instances", []) if i["name"] == args.agent), {})
    root = args.root or (Path(selected["root"]) if selected else None)
    state = args.state or (Path(selected["state_dir"]) if selected else None)
    if root is None or state is None:
        raise ValueError("choose an existing instance with --root and --state, or an instances file")
    root, state = root.resolve(), state.resolve()
    env = dict(os.environ)
    for key in ("PYTHONHOME", "LD_LIBRARY_PATH"):
        if key in selected.get("environment", {}): env[key] = selected["environment"][key]
    env.update(selected.get("required_runtime", {}))
    env.update(METTACLAW_SKIP_INITIALIZE="1", METTACLAW_ENGINE="cetta", METTACLAW_ACTIVE_ENGINE="cetta",
        METTACLAW_INSTANCE=state.name, METTACLAW_ENGINE_STATE_PATH=str(state / "engine"),
        METTACLAW_MODEL_STATE_PATH=str(root / "memory/persistent.metta"),
        METTACLAW_COGNITIVE_HEALTH_PATH=str(state / "cognitive-health.json"),
        METTACLAW_STRATEGY_PATH=str(state / "observed-strategy.sqlite3"),
        METTACLAW_RECYCLE_REQUEST_PATH=str(state / "recycle.requested"),
        METTACLAW_WAKE_REQUEST_PATH=str(state / "wake.requested"))
    return root, state, env


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="godel")
    parser.add_argument("--instances", type=Path, default=Path.home() / ".config/godelclaw/instances.json")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--client-state", type=Path)
    parser.add_argument("--mail-script", type=Path)
    parser.add_argument("--dir", type=Path, default=Path.home() / ".local/state/godelclaw/display")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--submit", help="text, or - to read all stdin")
    operation.add_argument("--submit-file", type=Path)
    operation.add_argument("--command")
    operation.add_argument("--status", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.json and args.submit is None and args.submit_file is None and not args.command and not args.status:
        parser.error("--json requires --submit, --submit-file, --command or --status")
    root, state, environment = profile(args)
    client_state = args.client_state or state / "terminal-client"
    client_state.mkdir(parents=True, exist_ok=True)
    command = shutil.which("claw-mail")
    installed = Path(command).resolve() if command else None
    mail_script = args.mail_script or (installed if installed and installed.suffix == ".py" else
                  installed.parent / "cli/shared_mail.py" if installed else None)
    control_only = bool(args.command or args.status)
    if not control_only and (not mail_script or not mail_script.is_file()):
        raise ValueError("choose the installed Python mail client with --mail-script")
    mail = None if control_only else MailSession(client_state, args.agent, [sys.executable, str(mail_script)])
    if args.submit is not None or args.submit_file is not None:
        text = (args.submit_file.read_text() if args.submit_file else
                sys.stdin.read() if args.submit == "-" else args.submit)
        try: print(json.dumps(mail.publish(mail.prepare(text)), ensure_ascii=False)); return 0
        finally: mail.close()
    log = (client_state / "controls.log").open("a")
    controls = Controls([str(root / "run.sh"), "terminal-control"], cwd=root,
                        environment=environment, log=log)
    try:
        if args.status:
            print(json.dumps(controls.request("status"), ensure_ascii=False)); return 0
        if args.command:
            name, _, arguments = args.command.partition(" ")
            print(json.dumps(controls.request("command", name=name, arguments=arguments), ensure_ascii=False)); return 0
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.patch_stdout import patch_stdout
        session = PromptSession(history=FileHistory(str(client_state / "history")), mouse_support=False)
        stop, work, outgoing, menu, menu_lock = threading.Event(), queue.Queue(), queue.Queue(), [], threading.Lock()
        view_epoch = [0]

        def show(body, epoch):
            with menu_lock:
                if body.get("kind") == "menu" and epoch != view_epoch[0]:
                    print("This menu is no longer active.", flush=True)
                    return
                menu[:] = [button for row in body.get("rows", []) for button in row]
                print("\n" + clean(body.get("text", json.dumps(body, ensure_ascii=False) if "kind" not in body else "")), flush=True)
                for n, button in enumerate(menu, 1): print("%d  %s" % (n, clean(button[0])), flush=True)

        def worker():
            while not stop.is_set():
                item = work.get()
                try:
                    show(controls.request(item[0], **item[1]), item[2])
                except Exception as error:
                    print("Operation incomplete: " + clean(str(error)), flush=True)

        def publisher():
            while not stop.is_set():
                item = outgoing.get()
                try:
                    if item is None:
                        results = mail.retry()
                        unconfirmed = [r for r in results if r["status"] == "unconfirmed"]
                        print("%d acceptance receipts; %d inputs still unconfirmed" % (len(results)-len(unconfirmed), len(unconfirmed)), flush=True)
                        for result in unconfirmed: print(clean(result["id"] + ": " + result["error"]), flush=True)
                    else:
                        mail.publish(item); print("accepted · " + item["id"], flush=True)
                except Exception as error:
                    print("Input acceptance unconfirmed; /retry keeps its original ID: " + clean(str(error)), flush=True)

        def replies():
            previous_error = ""
            while not stop.is_set():
                try:
                    value = mail.receive()
                    if value: print("\n" + clean(value.get("from", args.agent)) + ":\n" + clean(value["text"]), flush=True)
                    else: stop.wait(.1)
                    previous_error = ""
                except Exception as error:
                    label = clean(str(error))
                    if label != previous_error: print("Reply channel unavailable: " + label, flush=True)
                    previous_error = label
                    stop.wait(2)

        def events():
            reader = Reader(args.dir)
            # Follow from attachment; the full work log remains separately readable.
            for path in args.dir.glob("*.jsonl"):
                stat = path.stat(); reader.offsets[path.name] = (stat.st_ino, stat.st_size)
            while not stop.wait(.15):
                for event in reader.read():
                    if event.get("session") != args.agent and event.get("parent_session") != args.agent: continue
                    kind = event.get("kind", "")
                    if kind in ("text.delta", "tool.output"):
                        print(clean(event.get("text", "")), end="", flush=True)
                    elif kind in ("tool.started", "tool.finished", "session.finished", "gap"):
                        print("\n" + clean(event.get("name") or kind) + " · " + clean(event.get("status", "")), flush=True)

        with patch_stdout(raw=False):
            for target in (worker, publisher, replies, events): threading.Thread(target=target, daemon=True).start()
            print("Attached to %s. /models /activity /help · Ctrl-D detaches; cognition keeps running." % args.agent)
            pending = sum(1 for _ in (client_state / "pending").glob("*.json"))
            if pending: print("%d inputs await confirmed acceptance; /retry reuses their IDs." % pending)
            stored = sorted((client_state / "received").glob("*.json"), key=lambda p:p.stat().st_mtime)
            if stored:
                print("Recent stored replies; /replies shows the complete local archive.")
                for path in stored[-3:]: print(clean(json.loads(path.read_text())["text"]))
            work.put(("command", dict(name="/activity", arguments=""), view_epoch[0]))
            while True:
                try: text = session.prompt(args.agent + "> ")
                except KeyboardInterrupt: continue
                except EOFError: break
                if not text.strip(): continue
                with menu_lock:
                    view_epoch[0] += 1
                    epoch = view_epoch[0]
                if text in ("/quit", "/detach"): break
                if text == "/help":
                    print("/models /model /modes /engines /fuel /energy /activity /health /quota /wake /start /stop\n"
                          "/retry retries locally retained submissions with their original IDs.\n"
                          "/detach exits this console. Paste text, then Enter, to send it. // sends a literal slash.")
                    continue
                if text == "/retry": outgoing.put(None); continue
                if text == "/close-menu":
                    with menu_lock: menu.clear()
                    print("Menu closed.")
                    continue
                if text == "/status": work.put(("status", {}, epoch)); continue
                if text == "/replies":
                    for path in sorted((client_state / "received").glob("*.json"), key=lambda p:p.stat().st_mtime):
                        print(clean(json.loads(path.read_text())["text"]))
                    continue
                with menu_lock:
                    if menu and text.isdigit() and not 0 < int(text) <= len(menu):
                        print("Choose a displayed number, or /close-menu.")
                        continue
                    choice = menu[int(text) - 1][1] if text.isdigit() and 0 < int(text) <= len(menu) else None
                    menu.clear()
                if choice: work.put(("choice", dict(value=choice), epoch))
                elif text.startswith("/") and not text.startswith("//"):
                    name, _, arguments = text.partition(" "); work.put(("command", dict(name=name, arguments=arguments), epoch))
                else:
                    try: outgoing.put(mail.prepare(text[1:] if text.startswith("//") else text))
                    except OSError as error: print("Input not accepted locally: " + clean(str(error)), flush=True)
            stop.set()
        return 0
    finally:
        if mail is not None: mail.close()
        controls.close(); log.close()


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print("Console unavailable: " + str(error), file=sys.stderr); raise SystemExit(1)
