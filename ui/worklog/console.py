"""Attach an input composer to an existing agent without starting cognition."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from stream_sink import control
from terminal_client import Controls, MailSession
from ui.worklog.viewer import Reader
from ui.worklog.inlineview.adapter import EventBlocks
from ui.worklog.inlineview.textsafe import clean

PRIME_AGE = 7 * 86400       # display files this recent may hold the agent's current state
PRIME_FILES = 8             # newest files whose tails are absorbed at attachment
PRIME_BYTES = 1 << 20       # bytes read from the end of each
STATUS_PERIOD = 5           # seconds between runtime status samples for the status row
STATUS_BACKOFF = 300        # longest pause after repeated sampling failures
HELP = """Type a message and press Enter to send it; Alt-Enter or Ctrl-J starts a new line, and pasted text keeps its lines.
Agent controls, shared with Telegram (a menu is answered by typing its number):
  /models /model /modes /engines /fuel /energy /activity /health /quota /wake /start /stop
  /status       the structured runtime status
  /retry        resend locally retained inputs with their original IDs
  /replies      the complete local archive of replies
  /close-menu   close the open menu
  /detach       leave the console (also Ctrl-D); cognition keeps running
  //text        send text that begins with a slash
"""
# 16-colour names only, so the terminal's own palette (light or dark) decides the shades.
STYLE = {
    "rule": "ansibrightblack", "placeholder": "ansibrightblack", "prompt.marker": "ansicyan bold",
    "activity.spinner": "ansicyan bold", "activity.session": "bold", "activity.state": "ansicyan",
    "activity.command": "", "activity.meta": "ansibrightblack", "activity.hint": "ansibrightblack",
    "activity.warning": "ansiyellow",
    "status": "noreverse", "status.agent": "bold", "status.model": "ansimagenta", "status.state": "",
    "status.busy": "ansicyan", "status.work": "bold", "status.label": "ansibrightblack", "status.value": "",
    "status.warning": "ansiyellow", "status.error": "ansired bold", "status.sep": "ansibrightblack",
    "status.meta": "ansibrightblack",
}


def EditorProxy(StdoutProxy):
    """prompt_toolkit's patch_stdout proxy, drawing each batch of output in ONE terminal write.

    run_in_terminal (what patch_stdout uses) erases the editor, prints, and redraws it with
    three separate writes, so a terminal can show a frame without the editor on every event.
    Here, in the application's own loop and in order: erase the editor, print the batch, ask
    for the cursor row, redraw the editor, then flush once. It waits (briefly) for an
    outstanding cursor-row answer first, as run_in_terminal does. Each prompt runs a fresh
    event loop that is closed when the prompt returns, dropping callbacks still queued on it,
    so text is kept here until it is written: on the loop of the prompt now running, or
    directly when none is (release() is called after each prompt returns).
    Output is raw: every caller hands it sanitised whole lines."""
    import collections

    class Proxy(StdoutProxy):
        def __init__(self):
            super().__init__(sleep_between_writes=0.05, raw=True)
            self.pending, self.guard, self.loop = collections.deque(), threading.Lock(), None
            self.writing = threading.Lock()     # taking text and writing it are one step, so order holds

        def _take(self):
            with self.guard:
                text = "".join(self.pending)
                self.pending.clear()
                self.loop = None
            return text

        def _direct(self):
            with self.writing:
                text = self._take()
                if text:
                    self._output.enable_autowrap()
                    self._output.write_raw(text)
                    self._output.flush()

        def release(self):
            """No prompt is running: write whatever is still waiting for a loop."""
            if self._get_app_loop() is None:
                self._direct()

        def _write_and_flush(self, loop, text):
            with self.guard:
                self.pending.append(text)
                if loop is not None and loop is self.loop:
                    return                      # a drain is already queued on this loop
                self.loop = loop
            if loop is None:
                return self._direct()
            try:
                loop.call_soon_threadsafe(self._drain, loop, time.monotonic() + .2)
            except RuntimeError:                # that prompt has just ended
                self._direct()

        def _drain(self, loop, deadline):
            app = self.app_session.app
            if app is None or app.loop is not loop or not app.is_running:
                return self._direct()
            if (app._running_in_terminal or app.renderer.waiting_for_cpr) and time.monotonic() < deadline:
                loop.call_later(.005, self._drain, loop, deadline)
                return
            with self.writing:
                text = self._take()
                if not text:
                    return
                output = self._output
                if app._running_in_terminal:
                    output.write_raw(text)
                    output.flush()
                    return
                renderer, flush = app.renderer, output.flush
                output.flush = lambda: None
                try:
                    renderer.erase()                # also re-enables autowrap for the text
                    output.write_raw(text)
                    renderer.request_absolute_cursor_position()
                finally:
                    output.flush = flush
                app._redraw()                       # renders and flushes everything at once

    return Proxy()


def reflowed_rows(renderer, size):
    """Rows by which re-wrapping at a narrower width moved the cursor down: each editor row
    drawn above the cursor that no longer fits takes extra rows, and so does the cursor's
    own row up to the cursor."""
    screen, old = renderer._last_screen, renderer._last_size
    if screen is None or old is None or size.columns >= old.columns:
        return 0
    columns = max(1, size.columns)
    cursor = renderer._cursor_pos
    extra = 0
    for row in range(cursor.y):
        used = 0
        for column, char in screen.data_buffer[row].items():
            if char.char.strip():
                used = max(used, column + max(1, char.width))
        extra += max(0, used - 1) // columns
    return extra + cursor.x // columns


def tail_events(path, size, limit=PRIME_BYTES):
    """Complete JSON events in the last `limit` bytes before `size` (a partial first line is dropped)."""
    try:
        with Path(path).open("rb") as stream:
            start = max(0, size - limit)
            stream.seek(start)
            data = stream.read(size - start)
    except OSError:
        return []
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]
    events = []
    for line in lines[:-1]:          # the last piece has no newline (empty or still being written)
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


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


def configured_name(args):
    """The agent's configured display name, METTACLAW_AGENT_NAME: the instance's
    own environment, else the environment files of its systemd units (its own
    service, then its control services such as the Telegram channel that names
    it in replies; the first unit that sets it, later files overriding earlier
    ones within a unit). Only that one setting is read; an unconfigured agent is
    named by its identifier."""
    try:
        config = json.loads(args.instances.read_text()) if args.instances.is_file() else {}
    except (OSError, ValueError):
        return None
    selected = next((i for i in config.get("instances", []) if i.get("name") == args.agent), {})
    name = selected.get("environment", {}).get("METTACLAW_AGENT_NAME")
    units = [selected.get("service")] + list(selected.get("control_services", []))
    for unit in (u for u in units if u):
        if name:
            break
        import subprocess
        try:
            files = subprocess.run(["systemctl", "--user", "show", unit, "--property=EnvironmentFiles",
                                    "--value"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            files = ""
        for line in files.splitlines():
            try:
                text = Path(line.split(" (ignore_errors=")[0].strip()).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for row in text.splitlines():
                if row.startswith("METTACLAW_AGENT_NAME="):
                    name = row.split("=", 1)[1].strip().strip("\"'") or name
    name = (name or "").strip()
    return name if name and len(name) <= 32 and name.isprintable() else None


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
    agent_label = configured_name(args) or args.agent
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
        from prompt_toolkit.filters import Condition, is_done, to_filter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
        from prompt_toolkit.layout.containers import VerticalAlign
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.patch_stdout import StdoutProxy
        from prompt_toolkit.renderer import CPR_Support
        from prompt_toolkit.styles import Style
        ascii_only = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8"
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        columns = lambda: shutil.get_terminal_size((80, 24)).columns
        blocks = EventBlocks(args.agent, columns, ascii_only=ascii_only, color=color, label=agent_label)
        stop, work, outgoing, menu, menu_lock = threading.Event(), queue.Queue(), queue.Queue(), [], threading.Lock()
        view_epoch = [0]
        facts, reply_fault, unconfirmed = [{}, 0.0], [""], [0]     # facts: [last status sample, when]

        def say(text):
            """Everything printed above the editor: whole, sanitised lines."""
            print(text if text.endswith("\n") else text + "\n", end="", flush=True)

        def note(text, style=""):
            say(blocks.line(text, style))

        def live_facts():
            sample, when = facts        # replaced whole by the poller, never mutated
            return sample if time.time() - when < 30 else {}

        def menu_hint():
            with menu_lock:
                count = len(menu)
            return ("menu open%stype 1-%d to choose, or /close-menu" % (blocks._sep(), count)) if count else None

        def notes():
            out = []
            if unconfirmed[0]:
                out.append("%d unconfirmed%s/retry" % (unconfirmed[0], blocks._sep()))
            if reply_fault[0]:
                out.append("replies unavailable")
            return out

        def prompt_message():
            # Rows above the draft: a rule that separates the editor from scrollback, then the
            # activity row. They are redrawn in place and erased before anything scrolls, so
            # neither ever reaches the scrollback.
            rule = ("-" if ascii_only else "─") * max(1, columns() - 1)
            return ([("class:rule", rule), ("", "\n")] + blocks.activity(menu_hint()) +
                    [("", "\n"), ("class:prompt.marker", "> " if ascii_only else "› ")])

        bindings = KeyBindings()

        @bindings.add("escape", "enter")
        @bindings.add("c-j")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        session = PromptSession(history=FileHistory(str(client_state / "history")), mouse_support=False,
                                message=prompt_message, prompt_continuation=lambda width, line, wrap: "  ",
                                placeholder=lambda: [("class:placeholder", "Message " + agent_label)],
                                key_bindings=bindings, erase_when_done=True, refresh_interval=.25,
                                style=Style.from_dict(STYLE))
        # The status row is our own window rather than prompt_toolkit's bottom toolbar, which is
        # hidden whenever the cursor row is being re-measured (after every output) and would make
        # the editor hop by a row.
        status_row = ConditionalContainer(
            Window(FormattedTextControl(lambda: blocks.status(live_facts(), notes())), height=1,
                   dont_extend_height=True, style="class:status"), filter=~is_done)
        session.layout.current_window.dont_extend_height = to_filter(True)
        inner = HSplit([session.layout.container, status_row], align=VerticalAlign.BOTTOM)
        # Bottom anchoring. prompt_toolkit's inline application spans from its start row to the bottom
        # of the screen; aligning to the bottom keeps the rule, activity row, draft and status row on the
        # last rows whatever is printed above. No padding text, no scroll region.
        # It re-measures that area after every output with a cursor-position query. Until the screen has
        # filled once, drawing before the answer would put the editor under the new text and then jump it
        # down, so nothing is drawn while a measurement is pending. Once the editor has reached the last
        # row, output can only scroll it, never move it, so it is drawn at once (no blink per event); a
        # resize measures again.
        renderer = session.app.renderer
        settled = [None]

        def measured():
            if renderer.cpr_support != CPR_Support.SUPPORTED:
                return True
            size = session.app.output.get_size()
            if settled[0] == size:
                return True
            if not renderer.height_is_known:
                return False
            below = getattr(renderer, "_min_available_height", 0)
            if 0 < below <= inner.preferred_height(size.columns, size.rows).preferred:
                settled[0] = size
            return True
        session.layout = session.app.layout = Layout(ConditionalContainer(inner, filter=Condition(measured)),
                                                     focused_element=session.layout.current_window)
        # When tmux narrows a pane it re-wraps every line longer than the new width, so the rule and
        # activity row above the cursor take more rows and the cursor moves down by that many;
        # prompt_toolkit's erase on resize would then leave their first rows behind (and they would
        # scroll into the history). Start the erase that many rows higher. Only inside tmux, whose
        # reflow is known: a terminal that does not reflow would lose real output instead.
        resize = session.app._on_resize

        def on_resize():
            if os.environ.get("TMUX"):
                try:
                    up = reflowed_rows(renderer, session.app.output.get_size())
                    if up:
                        session.app.output.cursor_up(up)
                except Exception:
                    pass
            resize()
        session.app._on_resize = on_resize

        def show(body, epoch):
            with menu_lock:
                if body.get("kind") == "menu" and epoch != view_epoch[0]:
                    note("This menu is no longer active.", "dim")
                    return
                menu[:] = [button for row in body.get("rows", []) for button in row]
                text = body.get("text", json.dumps(body, ensure_ascii=False) if "kind" not in body else "")
                say("\n" + blocks.menu(text, [button[0] for button in menu]))

        def worker():
            while not stop.is_set():
                item = work.get()
                try:
                    show(controls.request(item[0], **item[1]), item[2])
                except Exception as error:
                    note("Operation incomplete: " + clean(str(error)), "warning")

        def publisher():
            while not stop.is_set():
                item = outgoing.get()
                try:
                    if item is None:
                        results = mail.retry()
                        unconfirmed_results = [r for r in results if r["status"] == "unconfirmed"]
                        note("%d acceptance receipts; %d inputs still unconfirmed" % (
                            len(results) - len(unconfirmed_results), len(unconfirmed_results)))
                        for result in unconfirmed_results: note(result["id"] + ": " + result["error"], "warning")
                    else:
                        mail.publish(item); note("  accepted · " + item["id"], "dim")
                except Exception as error:
                    note("Input acceptance unconfirmed; /retry keeps its original ID: " + clean(str(error)), "warning")
                count_unconfirmed()

        def count_unconfirmed():
            try: unconfirmed[0] = sum(1 for _ in (client_state / "pending").glob("*.json"))
            except OSError: pass

        def replies():
            previous_error = ""
            while not stop.is_set():
                try:
                    value = mail.receive()
                    if value: say("\n" + blocks.reply(agent_label if value.get("from") in (None, "", args.agent) else value["from"], value["text"]))
                    else: stop.wait(.1)
                    previous_error = reply_fault[0] = ""
                except Exception as error:
                    label = clean(str(error))
                    if label != previous_error: note("Reply channel unavailable: " + label, "warning")
                    previous_error = reply_fault[0] = label
                    stop.wait(2)

        def poller():
            # The same read-only runtime status /status prints (model, input queue, provider
            # phase), sampled only while no operator request is using the control process, with
            # a short reply timeout, and less and less often while it keeps failing.
            pause = 1
            while not stop.wait(pause):
                try:
                    value = controls.try_request("status", reply_timeout=2)
                    if isinstance(value, dict):
                        facts[:] = [value, time.time()]
                    pause = STATUS_PERIOD if value is not None else 1
                except Exception:
                    pause = min(STATUS_BACKOFF, max(STATUS_PERIOD, pause * 2))
                count_unconfirmed()

        def mine(event):
            return event.get("session") == args.agent or event.get("parent_session") == args.agent

        def events():
            reader = Reader(args.dir)
            # Follow from attachment; the full work log remains separately readable. The tails of the
            # most recent files are absorbed silently so the status row starts with the agent's state.
            recent = []
            for path in args.dir.glob("*.jsonl"):
                try: stat = path.stat()
                except OSError: continue
                reader.offsets[path.name] = (stat.st_ino, stat.st_size)
                if time.time() - stat.st_mtime < PRIME_AGE:
                    recent.append((stat.st_mtime, path, stat.st_size))
            enabled, epoch = control(args.dir)
            backlog = []
            for _, path, size in sorted(recent)[-PRIME_FILES:] if enabled else []:
                backlog += [e for e in tail_events(path, size) if e.get("epoch", "initial") == epoch and mine(e)]
            if backlog:
                blocks.prime(sorted(backlog, key=lambda e: (e.get("time", 0), e.get("producer", ""), e.get("sequence", 0))))
            previous_failure = ""
            while not stop.wait(.15):
                batch = [e for e in reader.read() if mine(e)]
                # Whole lines only: a fragment flushed through patch_stdout is redrawn on the draft's row.
                blocks.consumed = 0
                try:
                    text = blocks.feed(batch)
                except Exception as error:   # last resort: the reader already moved past this batch, so show it as plain text
                    text = blocks.recover(batch, error)
                failure = blocks.fault
                if failure and failure != previous_failure: note("Display renderer fault; the affected events are shown as plain text: " + clean(failure), "warning")
                previous_failure = failure
                if text: print(text, end="", flush=True)

        def terminate(number, frame):
            raise SystemExit(128 + number)   # unwinds through prompt_toolkit, which restores the tty
        for name in ("SIGTERM", "SIGHUP"): signal.signal(getattr(signal, name), terminate)

        # raw: the blocks carry SGR colour; every printed string is sanitised first (see say/blocks).
        proxy = EditorProxy(StdoutProxy)
        streams = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = proxy
        try:
            for target in (worker, publisher, replies, events, poller): threading.Thread(target=target, daemon=True).start()
            note("Attached to %s. /help lists commands; Ctrl-D detaches and cognition keeps running." % agent_label, "dim")
            count_unconfirmed()
            if unconfirmed[0]: note("%d inputs await confirmed acceptance; /retry reuses their IDs." % unconfirmed[0], "warning")
            stored = sorted((client_state / "received").glob("*.json"), key=lambda p:p.stat().st_mtime)
            if stored:
                note("Recent stored replies; /replies shows the complete local archive.", "dim")
                for path in stored[-3:]:
                    value = json.loads(path.read_text())
                    say(blocks.reply(agent_label if value.get("from") in (None, "", args.agent) else value["from"], value["text"]))
            work.put(("command", dict(name="/activity", arguments=""), view_epoch[0]))
            while True:
                try: text = session.prompt()
                except KeyboardInterrupt: continue
                except EOFError: break
                finally: proxy.release()
                if not text.strip(): continue
                say(blocks.user_input(text))
                with menu_lock:
                    view_epoch[0] += 1
                    epoch = view_epoch[0]
                if text in ("/quit", "/detach"): break
                if text == "/help":
                    say(HELP)
                    continue
                if text == "/retry": outgoing.put(None); continue
                if text == "/close-menu":
                    with menu_lock: menu.clear()
                    note("Menu closed.", "dim")
                    continue
                if text == "/status": work.put(("status", {}, epoch)); continue
                if text == "/replies":
                    for path in sorted((client_state / "received").glob("*.json"), key=lambda p:p.stat().st_mtime):
                        value = json.loads(path.read_text())
                        say(blocks.reply(agent_label if value.get("from") in (None, "", args.agent) else value["from"], value["text"]))
                    continue
                with menu_lock:
                    if menu and text.isdigit() and not 0 < int(text) <= len(menu):
                        note("Choose a displayed number, or /close-menu.", "warning")
                        continue
                    choice = menu[int(text) - 1][1] if text.isdigit() and 0 < int(text) <= len(menu) else None
                    menu.clear()
                if choice: work.put(("choice", dict(value=choice), epoch))
                elif text.startswith("/") and not text.startswith("//"):
                    name, _, arguments = text.partition(" "); work.put(("command", dict(name=name, arguments=arguments), epoch))
                else:
                    try: outgoing.put(mail.prepare(text[1:] if text.startswith("//") else text))
                    except OSError as error: note("Input not accepted locally: " + clean(str(error)), "warning")
                    count_unconfirmed()
            stop.set()
            note("Detached from %s; cognition keeps running." % agent_label, "dim")
        finally:
            sys.stdout, sys.stderr = streams
            proxy.close()
        return 0
    finally:
        if mail is not None: mail.close()
        controls.close(); log.close()


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print("Console unavailable: " + str(error), file=sys.stderr); raise SystemExit(1)
