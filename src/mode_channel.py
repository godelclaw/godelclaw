"""Addressed mode I/O; the independently supervised channel owns delivery.

Tool workers only record intentions. The control responder submits their
stable keys, so a slow network, stopped cognition or disconnected tool worker
cannot stop operator controls or lose a queued send.
"""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

import durable_telegram

_last_health_at = 0


def state():
    configured = os.environ.get("METTACLAW_MODE_STATE_ROOT")
    if configured:
        return Path(configured)
    return Path(os.environ["METTACLAW_ENGINE_STATE_PATH"]).parent / "modes"


def read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def store(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".intent-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def primary():
    # Reading another conversation never changes the operator destination.
    return (os.environ.get("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID") or
            os.environ.get("METTACLAW_TELEGRAM_CHAT_ID") or
            os.environ.get("TELEGRAM_CHAT_ID") or "").split(",")[0].strip()


def route_name(chat, thread=0):
    chat, thread = int(chat), int(thread)
    if chat == 0 or thread < 0:
        raise ValueError("invalid Telegram destination")
    return "telegram_%s_%d" % (str(chat).replace("-", "m"), thread)


def remember_route(message):
    chat = message.get("chat", {})
    chat_id = int(chat["id"])
    thread = int(message.get("message_thread_id", 0))
    name = route_name(chat_id, thread)
    folder = state() / "channel-routes"
    store(folder / (name + ".json"), {"chat": str(chat_id), "thread": thread,
          "type": chat.get("type", "unknown"), "title": chat.get("title", "")})
    return name


def resolve(name):
    if name == "telegram":
        return {"chat": str(int(primary())), "thread": 0}
    # Validate the name before using it as a path. Routes are learned only
    # from inputs accepted by the channel service, never from message text.
    import re
    if not re.fullmatch(r"telegram_(?:m)?[0-9]+_[0-9]+", str(name)):
        raise ValueError("unknown channel; use a destination from the route context")
    route = read(state() / "channel-routes" / (name + ".json"), None)
    if route is None or route_name(route["chat"], route["thread"]) != name:
        raise ValueError("unknown Telegram destination")
    return route


def context():
    lines = ["CHANNEL ROUTES (host configuration):",
             '"telegram" addresses the private operator only; reading a group does not change it.',
             "Choose the destination explicitly for every group or thread reply.",
             "A sender, quoted sender and intended recipient are different people.",
             "Sibling conversation belongs in an explicit group route, not the operator's private chat.",
             "A send result means durably queued, not confirmed delivered.",
             "/mode, /model, /start and /stop are handled by the independent control responder."]
    if primary():
        lines.append("telegram: operator chat=" + primary())
    for path in sorted((state() / "channel-routes").glob("*.json")):
        route = read(path, None)
        if route:
            lines.append(path.stem + ": " + json.dumps(route, ensure_ascii=False))
    return "\n".join(lines)


def enqueue(text, destination="telegram"):
    route = resolve(destination)
    folder = state() / "channel-outbox"
    folder.mkdir(parents=True, exist_ok=True)
    client = durable_telegram.Channel("", str(folder / "sequence"))
    key = client.key(route["chat"], route["thread"])
    store(folder / (key + ".json"), {"key": key,
          "commands": [["send", piece, "plain"] for piece in durable_telegram.chunks(str(text))]})
    return "durably queued: " + key


def handover_inputs():
    """Unacknowledged Bot API inputs retained during the one-poller handover."""
    if not os.environ.get("METTACLAW_ENGINE_STATE_PATH"):
        return []
    return [(p.stem, read(p, None)) for p in sorted((state() / "channel-handover").glob("*.json"))]


def acknowledge_handover(ids):
    if not os.environ.get("METTACLAW_ENGINE_STATE_PATH"):
        return True
    folder = state() / "channel-handover"
    for update_id in ids:
        if not str(update_id).isdigit():
            continue
        path = folder / (str(update_id) + ".json")
        if path.exists():
            path.replace(path.with_suffix(".done"))
    return True


def pump():
    """One bounded submission per control tick, independent of cognition.

    A lost submission reply leaves the exact same key and bytes for retry;
    Telegram delivery and uncertain-delivery holds belong to the service.
    """
    path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET")
    if not path:
        return 0
    folder = state() / "channel-outbox"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "pump.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        for intent in sorted(folder.glob("*.json")):
            item = read(intent, None)
            if not item:
                return 0
            client = durable_telegram.Channel(path, str(folder / "sequence"), timeout=.2)
            body = json.dumps(item["commands"], ensure_ascii=False, separators=(",", ":"))
            try:
                code, _, _ = client.rpc(durable_telegram.SUBMIT, item["key"], body)
            except (OSError, durable_telegram.ChannelError):
                return 0
            if code == durable_telegram.STORED:
                intent.unlink()
                return 1
            if code not in (durable_telegram.UNAVAILABLE, durable_telegram.LIMIT):
                # A refusal is a terminal receipt, not an excuse to try a
                # different recipient or send the same text with a new key.
                store(folder / (intent.stem + ".refused"), {**item, "code": code})
                intent.unlink()
            return 0
    return 0


def heartbeat():
    """Report local control/channel readiness, separately from Telegram polling."""
    path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET")
    if not path:
        return 0
    global _last_health_at
    if time.monotonic() - _last_health_at >= 5:
        client = durable_telegram.Channel(path, "", timeout=.2)
        try:
            code, _, _ = client.rpc(durable_telegram.NEXT)
            ready = code in (durable_telegram.IDLE, durable_telegram.TASK)
        except (OSError, durable_telegram.ChannelError):
            ready = False
        import input_ack
        store(state() / "control-status.json", {"pid": os.getpid(),
              "observed_at": time.time(), "channel_ready": ready,
              "operator_inbox": input_ack.input_view() if ready else None})
        _last_health_at = time.monotonic()
    return 1
