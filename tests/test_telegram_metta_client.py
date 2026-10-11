#!/usr/bin/env python3
"""channels/telegram.metta, Lila's Telegram client in MeTTa, end to end.

The real CeTTa channel service owns a local Bot API; a CeTTa process runs the
MeTTa client with Lila's own modules. Each step is a separate process, as a
restart would be: what the service holds survives it.

Environment: CETTA_CHANNEL_ROOT (CeTTa checkout with telegram-channel/1 and
lib/cwp), CETTA_SERVICE_BIN (its Telegram service), CETTA_BIN (a cetta with
Python for Lila's modules).
"""
import json
import os
import pathlib
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

# An end-to-end script, run directly. Imported by test discovery it would run
# at import time and leave its environment in the process for every test.
if __name__ != "__main__":
    raise unittest.SkipTest("an end-to-end script: run it with python3 directly")

ROOT = pathlib.Path(__file__).resolve().parents[1]
CETTA_ROOT = pathlib.Path(os.environ["CETTA_CHANNEL_ROOT"]).resolve()
SERVICE = str(pathlib.Path(os.environ["CETTA_SERVICE_BIN"]).resolve())
CETTA = str(pathlib.Path(os.environ["CETTA_BIN"]).resolve())
sys.path[:0] = [str(CETTA_ROOT / "tests")]
sys.argv[1:] = [SERVICE]

from test_telegram_transport import TOKEN, Peer, peer  # noqa: E402

OPERATOR, FRIEND, CHAT, GROUP = 7, 5, 42, -100200


class BotAPI(Peer):
    def __init__(self, *args):
        self.updates, self.calls, self.message = [], [], 500
        super().__init__(*args)

    def add(self, update, chat, sender, text, **extra):
        message = {"message_id": update, "date": 1790000000,
                   "chat": {"id": chat, "type": "private" if chat > 0 else "group",
                            **({"title": "Lab"} if chat < 0 else {"first_name": "Zed"})},
                   "from": {"id": sender, "first_name": "Zed" if sender == OPERATOR else "Friend",
                            "is_bot": False}, "text": text}
        message.update(extra)
        with self.lock:
            self.updates.append({"update_id": update, "message": message})

    def tap(self, update, chat, sender, data, message_id):
        with self.lock:
            self.updates.append({"update_id": update, "callback_query": {
                "id": "q%d" % update, "from": {"id": sender, "first_name": "Zed", "is_bot": False},
                "data": data, "message": {"message_id": message_id, "date": 1790000000,
                                          "chat": {"id": chat, "type": "private", "first_name": "Zed"}}}})

    def menus(self):
        with self.lock:
            return [(n, d) for n, d in self.calls if "reply_markup" in d]

    def tap_answers(self, update):
        with self.lock:
            return [d for n, d in self.calls if n == "answerCallbackQuery" and d["callback_query_id"] == "q%d" % update]

    def receipt(self, connection, method, path, body):
        data = json.loads(body)
        name = path.rsplit("/", 1)[-1]
        if name == "getUpdates":
            with self.lock:
                updates = [u for u in self.updates if u["update_id"] >= data["offset"]]
            return 200, [], json.dumps({"ok": True, "result": updates}).encode()
        with self.lock:
            self.calls.append((name, data))
            if name in ("deleteMessage", "answerCallbackQuery"):
                return 200, [], b'{"ok":true,"result":true}'
            if name == "getMe":
                return 200, [], json.dumps({"ok": True, "result": {"id": int(TOKEN.split(":")[0]), "is_bot": True,
                                                                    "username": "LilaTestBot"}}).encode()
            self.message += 1
            message = self.message
        if data.get("text") == "mode async slow":
            self.slow_started = True
            time.sleep(6)
        return 200, [], json.dumps({"ok": True, "result": {"message_id": message,
                                    "chat": {"id": data["chat_id"]}}}).encode()

    def texts(self):
        with self.lock:
            return [(d.get("chat_id"), d.get("text")) for n, d in self.calls if n == "sendMessage"]


def start(program, env):
    """Start a MeTTa program with Lila's modules in the background."""
    source = ("!(import! &self ./src/helper.py)\n!(import! &self ./channels/telegram.py)\n"
              "!(import! &self ./channels/telegram.metta)\n" + program)
    f = tempfile.NamedTemporaryFile("w", suffix=".metta", dir=ROOT, delete=False)
    f.write(source); f.close()
    p = subprocess.Popen([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", f.name],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT, env=env)
    return p, f.name


def finish(started, timeout=120):
    p, name = started
    out, err = p.communicate(timeout=timeout)
    os.unlink(name)
    return [line for line in out.splitlines() if line][3:], err


def run(program, env):
    """Run a MeTTa program with Lila's modules; return its printed results."""
    source = ("!(import! &self ./src/helper.py)\n!(import! &self ./channels/telegram.py)\n"
              "!(import! &self ./channels/telegram.metta)\n" + program)
    with tempfile.NamedTemporaryFile("w", suffix=".metta", dir=ROOT, delete=False) as f:
        f.write(source)
    try:
        p = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", f.name],
                           capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    finally:
        os.unlink(f.name)
    # The three imports each print true.
    lines = [line for line in p.stdout.splitlines() if line][3:]
    return lines, p.stderr


def expect(lines, err, wanted, label):
    for w in wanted:
        assert any(w in line for line in lines), (label, w, lines[-12:], err[-2000:])


with tempfile.TemporaryDirectory(prefix="lila-telegram-metta-") as temp:
    temp = pathlib.Path(temp)
    token = temp / "token"; token.write_text(TOKEN); token.chmod(0o600)
    state = temp / "service"; state.mkdir(mode=0o700)
    path = temp / "telegram.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(str(path)); listener.listen(16); path.chmod(0o600)
    ledger = temp / "telegram.jsonl"
    env = dict(os.environ,
               METTACLAW_TELEGRAM_CLIENT="metta",
               METTACLAW_TELEGRAM_TRANSPORT="durable",
               METTACLAW_TELEGRAM_SERVICE_SOCKET=str(path),
               METTACLAW_TELEGRAM_LOG_PATH=str(ledger),
               METTACLAW_TELEGRAM_OPERATOR_IDS=str(OPERATOR),
               METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS="%d,%d" % (CHAT, GROUP),
               METTACLAW_TELEGRAM_PRIMARY_CHAT_ID=str(CHAT),
               METTACLAW_TELEGRAM_HEALTH_PATH=str(temp / "health.json"),
               METTACLAW_TELEGRAM_SERVICE_TIMEOUT="20",
               METTACLAW_ENERGY_PATH=str(temp / "energy.json"),
               METTACLAW_TELEGRAM_OFFSET_PATH=str(temp / "offset"),
               METTACLAW_LIFECYCLE_PATH=str(temp / "lifecycle.json"),
               METTACLAW_WAKE_REQUEST_PATH=str(temp / "wake.requested"),
               METTACLAW_LOOP_MODE_PATH=str(temp / "loop-mode"),
               METTACLAW_FUEL_MODE_PATH=str(temp / "fuel-mode"),
               METTACLAW_ENGINE_STATE_PATH=str(temp / "engine"),
               METTACLAW_MODEL_STATE_PATH=str(temp / "model-state.json"),
               XDG_STATE_HOME=str(temp / "state-home"),
               METTACLAW_TELEGRAM_BOT_TOKEN=TOKEN,
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")]))
    (temp / "lifecycle.json").write_text(json.dumps({"schema": 1, "state": "running"}))
    (temp / "energy.json").write_text(json.dumps({"default": "full", "senders": {str(FRIEND): "light"}}))
    with peer("http/1.1", None, None, BotAPI) as (api, origin):
        env["METTACLAW_TELEGRAM_BASE_URL"] = origin
        service = subprocess.Popen([SERVICE, "--run", "--root", str(CETTA_ROOT), "--state-dir", str(state),
                                    "--worker", "lila", "--chat", str(CHAT), "--chat", str(GROUP),
                                    "--operator", str(OPERATOR), "--program", "channel",
                                    "--commands", str(ROOT / "channels" / "telegram_commands.metta"),
                                    "--command-deadline-ms", "3000",
                                    "--credential-file", str(token), "--mock-origin", origin,
                                    "--listener-fd", str(listener.fileno())],
                                   pass_fds=(listener.fileno(),), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        # The command responder runs beside the loop throughout, as in
        # production (run.sh telegram-control).
        responder = start('!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n', env)
        try:
            out = temp / "out.txt"

            def batch(extra=""):
                lines, err = run('!(tg:start)\n!(fs:write "%s" (tg:activity-batch))\n%s' % (out, extra), env)
                return (out.read_text() if out.exists() else None), lines, err

            # 1. Two messages arrive: from the operator, and from a friend in the
            #    group, replying to the operator. They become one batch, each
            #    formatted exactly as the Python client formats it, and logged
            #    to the ledger exactly as it logs them.
            api.add(71, CHAT, OPERATOR, "hello Lila")
            api.add(72, GROUP, FRIEND, "hi all", reply_to_message={"message_id": 70, "text": "earlier",
                                                                     "from": {"id": OPERATOR, "username": "zed"}})
            time.sleep(1.5)
            text, lines, err = batch("!(tg:last-message-is-human)\n!(tg:last-message-arm-loops)\n!(tg:pending-count)\n")
            sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
            os.environ.update({k: v for k, v in env.items() if k.startswith("METTACLAW_")})
            import telegram as py
            with api.lock:
                updates = [u for u in api.updates]
            expected = "\n".join(py._format_message(u, "message", u["message"], u["message"]["text"]) for u in updates)
            assert text == expected, ("format", text, expected)
            assert lines[-3:] == ["1", "10", "2"], lines[-6:]
            logged = [json.loads(line) for line in ledger.read_text().splitlines()]
            assert [r["update_id"] for r in logged] == [71, 72] and all(r["queued"] and r["allowed"] for r in logged)
            for record, update in zip(logged, updates):
                for field in ("kind", "chat_id", "chat_type", "chat_title", "message_id", "from", "from_id",
                              "from_is_bot", "text", "caption", "raw", "note"):
                    want = {"kind": "message", "chat_id": str(update["message"]["chat"]["id"]),
                            "chat_type": update["message"]["chat"]["type"],
                            "chat_title": py._display_chat(update["message"]["chat"]),
                            "message_id": update["message"]["message_id"],
                            "from": py._display_user(update["message"]["from"]),
                            "from_id": str(update["message"]["from"]["id"]), "from_is_bot": False,
                            "text": update["message"]["text"], "caption": None, "raw": update, "note": ""}[field]
                    assert record[field] == want, (field, record[field], want)
            print("batch and ledger match the Python client")

            # 2. The turn was never acknowledged (the process ended): the next
            #    process reads the same messages again, from the service.
            again, lines, err = batch()
            assert again == expected, (again, lines, err, expected,
                __import__("durable_telegram").Channel(str(path), os.devnull).pending(64),
                ledger.read_text()[-3000:], service.poll())
            # 3. Acknowledged, they are gone from the service.
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n!(tg:ack-activity-batch)\n!(tg:pending-count)\n', env)
            assert lines[-2:] == ["1", "0"], lines[-4:]
            acks = [json.loads(l) for l in ledger.read_text().splitlines() if '"activity_ack"' in l]
            assert acks and acks[-1]["update_ids"] == ["71", "72"], acks
            empty, lines, err = batch()
            assert empty == "", empty
            print("unacknowledged input survives a restart; acknowledged input is gone")

            # 4. Sending: to the primary chat by default, and a long message
            #    in parts to the group, each part logged with its message id.
            long_text = "\n".join("line %03d %s" % (i, "x" * 60) for i in range(80))
            lines, err = run('!(tg:start)\n!(tg:send "hello Zed" "")\n!(tg:send "%s" "%d")\n'
                             '!(tg:send "nope" "99")\n' % (long_text.replace("\n", "\\n"), GROUP), env)
            assert lines[-3].startswith('"sent message ') and lines[-3].endswith(' to chat 42"'), (lines, err[-3000:])
            assert " (in 2 parts: " in lines[-2] and "to chat %d" % GROUP in lines[-2], lines
            assert lines[-1] == '"send failed: target chat is not allowed"', lines
            sent = api.texts()
            assert sent[0] == (CHAT, "hello Zed") and "".join(t for c, t in sent[1:]).replace("\n", "") == \
                long_text.replace("\n", "") and all(len(t) <= 4096 for c, t in sent), sent
            outbound = [json.loads(l) for l in ledger.read_text().splitlines() if '"own_send"' in l]
            assert len(outbound) == 3 and outbound[0]["text"] == "hello Zed" and outbound[0]["kind"] == "outbound"
            first_id = outbound[0]["message_id"]
            print("sends: default chat, parts, refusal, own-send ledger")

            # 5. A model send goes out at most once per cognitive turn.
            lines, err = run('!(tg:start)\n!(py-call (telegram.begin_effect_turn "t1"))\n'
                             '!(tg:send-effect "once" "")\n!(tg:send-effect "once" "")\n'
                             '!(py-call (telegram.begin_effect_turn "t2"))\n!(tg:send-effect "once" "")\n', env)
            assert lines[-4].startswith('"sent message') and lines[-3] == '"duplicate send suppressed"' \
                and lines[-1].startswith('"sent message'), lines
            # 6. Deleting: only an own send the ledger witnesses, once.
            lines, err = run('!(tg:start)\n!(tg:delete-recorded "" %d)\n!(tg:delete-recorded "" %d)\n'
                             '!(tg:delete-recorded "" 12345)\n!(tg:delete-my-recent "%d" 1)\n'
                             % (first_id, first_id, GROUP), env)
            assert lines[-4] == '"deleted message %d from chat 42"' % first_id, lines
            assert lines[-3].startswith('"delete refused: no current own-send receipt') and \
                lines[-2].startswith('"delete refused'), lines
            assert lines[-1].startswith('"deleted message ') and "from chat %d" % GROUP in lines[-1], lines
            tombstones = [json.loads(l) for l in ledger.read_text().splitlines() if '"outbound_delete"' in l]
            assert len(tombstones) == 2 and tombstones[0]["message_id"] == first_id
            print("model sends once per turn; deletes only witnessed own sends, with tombstones")

            # 7. Resting: an ordinary message does not end the rest; the
            #    operator's does, and is then read by the next turn.
            rest = start('!(tg:start)\n!(tg:sleep-until-message 4)\n', env)
            time.sleep(1.0); api.add(80, GROUP, FRIEND, "just chatting")
            lines, err = finish(rest)
            assert lines[-1] == '"rested 4s"', (lines, err[-500:])
            rest = start('!(tg:start)\n!(tg:sleep-until-message 30)\n!(fs:write "%s" (tg:activity-batch))\n' % out, env)
            time.sleep(1.5); began = time.monotonic(); api.add(81, CHAT, OPERATOR, "wake up")
            lines, err = finish(rest)
            assert lines[-2] == '"woken by operator message"' and time.monotonic() - began < 5, (lines, err[-500:])
            assert "just chatting" in out.read_text() and "wake up" in out.read_text()
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n!(tg:ack-activity-batch)\n', env)
            print("rest: ordinary input waits, the operator wakes")

            # 8. Operator commands: /wake is declared, so the service asks and
            #    the responder answers, ending the rest. /energy is a button
            #    menu, still Python's, which sends its keyboard directly.
            rest = start('!(tg:start)\n!(tg:sleep-until-message 30)\n', env)
            time.sleep(1.5); began = time.monotonic(); api.add(82, CHAT, OPERATOR, "/wake")
            lines, err = finish(rest)
            assert lines[-1] == '"woken by /wake"' and time.monotonic() - began < 5, (lines, err[-500:])
            def replied(text):
                return any(t == text for c, t in api.texts())
            deadline = time.monotonic() + 10
            while not replied("Waking.") and time.monotonic() < deadline:
                time.sleep(.1)
            assert replied("Waking."), api.texts()[-3:]
            rest = start('!(tg:start)\n!(tg:sleep-until-message 6)\n', env)
            time.sleep(1.0); api.add(83, CHAT, OPERATOR, "/energy")
            lines, err = finish(rest)
            with api.lock:
                keyboards = [d for n, d in api.calls if n == "sendMessage" and "reply_markup" in d]
            assert keyboards and keyboards[-1]["text"].startswith("arming energy"), api.calls[-3:]
            energy_menu = keyboards[-1]
            buttons = [b["callback_data"] for row in energy_menu["reply_markup"]["inline_keyboard"] for b in row]
            assert buttons[:3] == ["energy:default:full", "energy:default:mid", "energy:default:light"], buttons
            assert "energy:%d:mid" % FRIEND in buttons, buttons
            print("commands: the service asks, the responder answers and wakes her; menus come with their buttons")

            # 8b. Taps on menu buttons: the responder changes the setting, the
            #     tap is answered with the outcome, and the menu is redrawn in
            #     place. Nothing reaches her loop.
            def menu_message(text_start):
                with api.lock:
                    sent = [(i, d) for i, (n, d) in enumerate(api.calls) if n == "sendMessage"
                            and "reply_markup" in d and d["text"].startswith(text_start)]
                return sent[-1] if sent else None
            def await_(predicate, label, seconds=10):
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    value = predicate()
                    if value:
                        return value
                    time.sleep(.05)
                raise AssertionError((label, api.calls[-4:]))
            api.tap(86, CHAT, OPERATOR, "energy:%d:mid" % FRIEND, 777)
            answer = await_(lambda: api.tap_answers(86), "energy tap answered")
            assert answer[0]["text"] == "%d arming set to mid(30)" % FRIEND, answer
            assert json.loads((temp / "energy.json").read_text())["senders"][str(FRIEND)] == "mid"
            redraw = await_(lambda: [d for n, d in api.calls if n == "editMessageText" and d["message_id"] == 777],
                            "energy menu redrawn")
            labels = [b["text"] for row in redraw[-1]["reply_markup"]["inline_keyboard"] for b in row]
            assert "● %d: mid" % FRIEND in labels and redraw[-1]["text"].startswith("arming energy — "), labels
            import loop_modes
            api.add(87, CHAT, OPERATOR, "/modes")
            await_(lambda: menu_message(loop_modes.mode_view()[:20]), "modes menu")
            other = next(m for m in loop_modes.MODES if m != loop_modes.current_mode())
            wake_file = temp / "wake.requested"
            if wake_file.exists():
                wake_file.unlink()
            api.tap(88, CHAT, OPERATOR, "mode:" + other, 778)
            answer = await_(lambda: api.tap_answers(88), "mode tap answered")
            assert loop_modes.current_mode() == other and wake_file.read_text() == "mode switch", answer
            redraw = await_(lambda: [d for n, d in api.calls if n == "editMessageText" and d["message_id"] == 778],
                            "modes menu redrawn")
            labels = [b["text"] for row in redraw[-1]["reply_markup"]["inline_keyboard"] for b in row]
            # A responder without a cognitive process marks the selection as
            # pending; it must not claim that the requested loop is running.
            assert "◌ " + other in labels, labels
            callbacks = [b["callback_data"] for row in redraw[-1]["reply_markup"]["inline_keyboard"] for b in row]
            assert callbacks == ["mode:godelclaw", "mode:coding", "mode:iter", "mode:omega"], callbacks
            wake_file.unlink()
            print("taps: energy and mode changed by the responder, answered, and redrawn in place")

            # 8c. A command addressed to her bot by name, as a group's command
            #     menu sends it, is a command: the service learned the name.
            api.add(89, CHAT, OPERATOR, "/activity@LilaTestBot")
            await_(lambda: [d for n, d in api.calls if n == "sendMessage" and
                            d.get("reply_parameters", {}).get("message_id") == 89], "addressed command answered")
            print("a command addressed to her bot by name is answered")

            # 9. Runtime events from Python threads (a finished job) become
            #    activity; Python and Prolog callers read the client's state.
            api.add(104, CHAT, OPERATOR, "unread in 42")
            import durable_telegram
            await_(lambda: any('"unread in 42"' in body for _, body in
                               durable_telegram.Channel(str(path), os.devnull).pending(256)),
                   "input durable before freezing the activity frontier")
            lines, err = run('!(tg:start)\n!(py-call (telegram.enqueue_runtime_event "job 7 done" "job"))\n'
                             '!(fs:write "%s" (tg:activity-batch))\n'
                             '!(py-call (telegram.pendingActivityCount))\n'
                             '!(py-call (telegram.preparedActivityCount))\n' % out, env)
            batch_text = out.read_text()
            assert "[runtime] job 7 done" in batch_text and "unread in 42" in batch_text, (batch_text, lines[-6:], err[-1500:])
            assert lines[-2:] == ["2", "2"], lines
            api.add(105, CHAT, OPERATOR, "newer in 42")
            time.sleep(1.0)
            lines, err = run('!(tg:start)\n!(fs:write "%s" (tg:activity-batch))\n'
                             '!(py-call (telegram.begin_effect_turn "t9"))\n'
                             '!(py-call (telegram.metta_publish "batch_update_ids" "104"))\n'
                             '!(py-call (telegram.effect_command_unaffected "t9" ("send-telegram-chat" "42" "x")))\n'
                             '!(py-call (telegram.effect_command_unaffected "t9" ("send-telegram-chat" "%d" "x")))\n'
                             % (out, GROUP), env)
            # A message the turn has not read withholds a send into its own
            # chat only; another chat's send goes ahead.
            assert lines[-2:] == ["0", "1"], (lines, err[-500:])
            lines, err = run('!(tg:start)\n!(fs:write "%s" (tg:activity-batch))\n!(tg:ack-activity-batch)\n'
                             '!(py-call (telegram.pendingActivityCount))\n' % out, env)
            assert lines[-2:] == ["1", "0"], lines
            print("runtime events, published counts and the unread-input guard work from the MeTTa client")

            # 10. The command responder, a process of its own with no model
            #     call, answers settings commands while no loop client runs at
            #     all, exactly as the Python handler words them.
            import loop_modes
            if True:
                wake_file = temp / "wake.requested"
                expected_view = py._command_answer("/mode", "", str(CHAT))
                sent_at = api.command_time = time.monotonic()
                api.add(110, CHAT, OPERATOR, "/mode")
                deadline = time.monotonic() + 10
                while expected_view not in [t for c, t in api.texts()] and time.monotonic() < deadline:
                    time.sleep(.05)
                assert expected_view in [t for c, t in api.texts()], (expected_view, api.texts()[-3:])
                latency = time.monotonic() - sent_at
                other = next(m for m in loop_modes.MODES if m != loop_modes.current_mode())
                api.add(111, CHAT, OPERATOR, "/mode " + other)
                deadline = time.monotonic() + 10
                while not wake_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                assert wake_file.read_text() == "mode switch" and loop_modes.current_mode() == other
                # A rest begins after that wake request: the request is stale.
                # /wake during the rest ends it.
                rest = start('!(tg:start)\n!(tg:sleep-until-message 30)\n', env)
                time.sleep(2.0); assert rest[0].poll() is None and not wake_file.exists()
                began = time.monotonic(); api.add(112, CHAT, OPERATOR, "/wake")
                lines, err = finish(rest)
                assert lines[-1] == '"woken by /wake"' and time.monotonic() - began < 5, (lines, err[-500:])
                # /delete stays with the loop client: answered once it runs.
                own = [json.loads(l) for l in ledger.read_text().splitlines() if '"own_send"' in l]
                deleted = {json.loads(l)["message_id"] for l in ledger.read_text().splitlines() if '"outbound_delete"' in l}
                target = next(r for r in reversed(own) if r["message_id"] not in deleted and r["chat_id"] == str(CHAT))
                api.add(113, CHAT, OPERATOR, "/delete %d %d" % (CHAT, target["message_id"]))
                time.sleep(1.0)
                lines, err = run('!(tg:start)\n!(tg:poll)\n', env)
                deadline = time.monotonic() + 10
                wanted = "deleted message %d from chat %d" % (target["message_id"], CHAT)
                while wanted not in [t for c, t in api.texts()] and time.monotonic() < deadline:
                    time.sleep(.1)
                assert wanted in [t for c, t in api.texts()], api.texts()[-3:]
                rss = int(next(l.split()[1] for l in open("/proc/%d/status" % responder[0].pid) if l.startswith("VmRSS")))
            # 11. Her conversation history, rendered in MeTTa exactly as the
            #     Python reader renders it, for a turn that has prepared input
            #     and for one that has not; and the per-chat recall skill,
            #     for Czech text too.
            api.add(115, GROUP, FRIEND, "Ahoj, jak se máš? " + "č" * 300)
            api.add(116, CHAT, OPERATOR, "a newer message")
            time.sleep(1.0)
            window_file = temp / "window.txt"
            recent_file = temp / "recent.txt"
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n'
                             '!(fs:write "%s" (tg:conversation-window 64))\n'
                             '!(fs:write "%s" (tg:recent-activity 5 "lab" 40))\n'
                             '!(py-call (telegram.metta_value "pending_count" 0))\n'
                             % (window_file, recent_file), env)
            py._log_path = str(ledger)
            py._primary_chat_id = str(CHAT)
            # Both learn chat titles from the messages they read.
            py._chat_titles = {str(CHAT): "Zed", str(GROUP): "Lab"}
            mine = window_file.read_text()
            # The batch was the last thing logged, so the frontier is the
            # ledger's end; the prepared messages are excluded.
            py._context_frontier_bytes = None
            py._current_batch_update_ids = {"115", "116"}
            expected = py.conversation_window(max_events=64)
            assert mine == expected, ("window", mine[-600:], expected[-600:])
            assert recent_file.read_text() == py.recent_activity(5, "lab", 40), (
                recent_file.read_text(), py.recent_activity(5, "lab", 40))
            assert "…" in recent_file.read_text() and "Ahoj" in recent_file.read_text()
            # A history that fills the budget still leaves the route line in.
            big = "\n".join(json.dumps({"received_at": "2026-09-29T10:00:00+0200", "kind": "message", "allowed": True,
                                         "queued": True, "note": "", "chat_id": str(CHAT), "chat_title": "Zed",
                                         "message_id": 9000 + i, "from": "Zed", "text": "y" * 900}) for i in range(60))
            with ledger.open("a") as f:
                f.write(big + "\n")
            lines, err = run('!(tg:start)\n!(tg:set context-frontier -1)\n!(fs:write "%s" (tg:conversation-window 64))\n' % window_file, env)
            full = window_file.read_text()
            assert full.startswith("TELEGRAM ROUTE") and len(full) <= 30000, (len(full), full[:80])
            print("conversation window and chat recall match the Python readers")

            # A short-lived mode tool records intent only. The independent
            # responder submits it while no cognitive engine is running;
            # a delivery slower than Iter's 5s tool deadline cannot kill it.
            began = time.monotonic()
            worker = subprocess.run([sys.executable, "-c", "import mode_channel; print(mode_channel.enqueue('mode async slow'))"],
                                    cwd=ROOT, env=env, text=True, capture_output=True, timeout=5)
            assert worker.returncode == 0 and worker.stdout.startswith("durably queued:"), worker.stderr
            assert time.monotonic() - began < 5
            try:
                await_(lambda: getattr(api, "slow_started", False), "mode send submitted outside cognition")
            except AssertionError:
                print("mode queue debug", worker.stdout.strip(),
                      [(p.name, p.read_text()[:600]) for p in (temp / "modes/channel-outbox").glob("*.*")],
                      durable_telegram.Channel(str(path), os.devnull).pending(256)[-6:])
                raise
            chosen = "omega" if loop_modes.current_mode() != "omega" else "iter"
            began = time.monotonic()
            api.add(200, CHAT, OPERATOR, "/mode " + chosen)
            await_(lambda: loop_modes.current_mode() == chosen, "control applied while delivery is slow")
            assert time.monotonic() - began < 3, "control blocked on send"
            assert responder[0].poll() is None
            key = worker.stdout.strip().split(": ", 1)[1]
            await_(lambda: any(key in body and '"delivered"' in body for _, body in
                               durable_telegram.Channel(str(path), os.devnull).pending(256)),
                   "delayed send has a delivery receipt")
            print("mode send survives a 6s delivery and exited tool worker; /mode persists within 3s during delivery")

            # 13. A queued operator input gets a receipt notice without a
            # cognitive process. It stays pending, and restarting the responder
            # resubmits the same identity without sending a duplicate notice.
            batch("!(tg:ack-activity-batch)\n")
            responder[0].terminate()
            responder[0].communicate(timeout=10)
            os.unlink(responder[1])
            notice_env = dict(env, METTACLAW_OBSERVED_STRATEGY="1",
                              METTACLAW_STRATEGY_PATH=str(temp / "notice-ledger.sqlite3"),
                              METTACLAW_WORKING_SET_PATH=str(temp / "notice-working.json"),
                              METTACLAW_HISTORY_PATH=str(temp / "notice-history.metta"),
                              METTACLAW_INPUT_ACK_SECONDS="0.05")
            responder = start('!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n', notice_env)
            notice = "Received. Your message is queued; /activity shows whether I'm waiting, retrying, or resting."
            question = "still waiting for this independent receipt probe"
            api.add(201, CHAT, OPERATOR, question)
            await_(lambda: notice in [t for _, t in api.texts()], "independent receipt notice")
            pending = lambda: durable_telegram.Channel(str(path), os.devnull).pending(256)
            assert any(question in body for _, body in pending()), "notice consumed cognitive input"
            responder[0].terminate()
            responder[0].communicate(timeout=10)
            os.unlink(responder[1])
            responder = start('!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n', notice_env)
            time.sleep(.8)
            assert sum(t == notice for _, t in api.texts()) == 1, "restart duplicated receipt notice"
            assert any(question in body for _, body in pending()), "restart consumed cognitive input"
            print("independent receipt notice preserves input and survives restart without duplicate delivery")

            # Processing the actual batch must pass exact IDs through the
            # MeTTa join into the host ledger, then retire the bot's notice.
            ack_probe = temp / "ack_failure_probe.py"
            ack_probe.write_text('import telegram\noriginal=telegram.handover_ack\n'
                'def refuse():\n    telegram.handover_ack=lambda ids: 0\n    return 1\n'
                'def restore():\n    telegram.handover_ack=original\n    return 1\n')
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n'
                '!(py-call (helper.turn_commit_stage 1 1 1))\n'
                '!(py-call (helper.working_set_save 9 1 "notice successor" 0))\n'
                '!(import! &self "' + str(ack_probe) + '")\n'
                '!(py-call (ack_failure_probe.refuse))\n'
                '!(tg:ack-activity-batch)\n'
                '!(== (tg:prepared-items) ())\n'
                '!(py-call (ack_failure_probe.restore))\n'
                '!(tg:ack-activity-batch)\n', notice_env)
            assert [line.lower() for line in lines[-4:]] == ['0', 'false', '1', '1'], lines
            assert lines[-1] == "1", (lines[-5:], err[-1000:])
            await_(lambda: any(name == "deleteMessage" for name, _ in api.calls),
                   "processed queue notice was deleted")
            assert not any(question in body for _, body in pending()), "processed input stayed queued"
            print("native acknowledgement clears exact input IDs and deletes the correlated notice")

            # Group message IDs are scoped to their original chat. A visible
            # reply may precede the input's durable processing boundary; it
            # must not retire the notice or redirect it to a selected DM.
            group_question = "group input for delayed durable settlement"
            api.add(202, GROUP, OPERATOR, group_question)
            await_(lambda: any(c == GROUP and t == notice for c, t in api.texts()),
                   "independent group receipt notice")
            def group_notice():
                with sqlite3.connect(notice_env["METTACLAW_STRATEGY_PATH"]) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute("SELECT * FROM input_notices WHERE update_id='202'").fetchone()
                    return dict(row) if row else None
            await_(lambda: group_notice() and group_notice()["message_id"] is not None,
                   "correlated group send receipt")
            target = group_notice()["message_id"]
            def group_deleted():
                return any(name == "deleteMessage" and data["chat_id"] == GROUP and
                           data["message_id"] == target for name, data in api.calls)
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n'
                '!(tg:send "visible group answer" "%d")\n' % GROUP, notice_env)
            expect(lines, err, ["sent message"], "group reply delivery before settlement")
            assert group_notice()["processed"] == 0 and not group_deleted()
            assert any(group_question in body for _, body in pending())
            responder[0].terminate()
            responder[0].communicate(timeout=10)
            os.unlink(responder[1])
            responder = start('!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n', notice_env)
            lines, err = run('!(tg:start)\n!(tg:activity-batch)\n'
                '!(py-call (helper.history_append "group processing history"))\n'
                '!(py-call (helper.turn_commit_stage 1 1 1))\n'
                '!(py-call (helper.working_set_save 10 1 "group successor" 0))\n'
                '!(import! &self "' + str(ack_probe) + '")\n'
                '!(py-call (ack_failure_probe.refuse))\n'
                '!(tg:ack-activity-batch)\n'
                '!(== (tg:prepared-count) 0)\n'
                '!(py-call (ack_failure_probe.restore))\n'
                '!(tg:ack-activity-batch)\n', notice_env)
            assert lines[-4:] == ['0', 'false', '1', '1'], (lines[-8:], err[-1000:])
            assert (temp / "notice-history.metta").read_text().strip() == "group processing history"
            await_(lambda: group_deleted() and group_notice()["state"] == "deleted",
                   "processed group notice deletion confirmed")
            assert group_notice()["processed"] == 1
            assert not any(group_question in body for _, body in pending())
            assert sum(c == GROUP and t == notice for c, t in api.texts()) == 1
            assert all(data["chat_id"] == GROUP for name, data in api.calls
                       if name == "deleteMessage" and data["message_id"] == target)
            print("group notice survives visible reply and restart; exact durable acknowledgement clears it")

            print("responder: /mode answered in %.0f ms with no loop running, as the Python handler words it; "
                  "/wake ends a rest; /delete stays with the loop client (responder RSS %d kB)" % (latency * 1000, rss))
        finally:
            if responder[0].poll() is None:
                responder[0].kill()
            responder[0].communicate(timeout=10); os.unlink(responder[1])
            service.send_signal(signal.SIGTERM); service.communicate(timeout=15); listener.close()
    print("telegram.metta: Lila's Telegram client in MeTTa passed end to end")
