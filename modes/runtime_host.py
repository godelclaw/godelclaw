"""Production host for the isolated MeTTa loops.

Only lifecycle, provider, filesystem and CWP channel boundaries live here.
The cognitive loops remain in iter/core.metta and Omega/src/loop.metta.
"""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import cognitive_health
import durable_telegram
import engine_modes
import lifecycle
import loop_modes
import mode_channel
import synthetic_llm
import llm_policy
import provider_protocol


def root():
    return Path(os.environ["METTACLAW_MODE_RUNTIME"])


def shared():
    return Path(os.environ["METTACLAW_MODE_STATE_ROOT"])


def mode():
    return os.environ["METTACLAW_ACTIVE_LOOP_MODE"]


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()))
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def record(kind, **fields):
    with (root() / "runtime.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "kind": kind,
                                 "mode": mode(), **fields}) + "\n")


def channel():
    return durable_telegram.Channel(
        os.environ["METTACLAW_TELEGRAM_SERVICE_SOCKET"],
        str(shared() / "mode-channel-sequence"))


def gate():
    """Stop/switch only between provider/tool turns; controls stay separate."""
    while True:
        if engine_modes.recycle_requested():
            record("recycle")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)
        if lifecycle.cognition_enabled():
            return True
        time.sleep(0.25)


def sleep(seconds):
    deadline = time.monotonic() + max(0, float(seconds))
    while True:
        gate()
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        time.sleep(min(left, 0.25))


def receive():
    """Offer input without acknowledging it until the loop saves history.

    The service remains the durable owner. Switching before a checkpoint
    leaves the input pending for whichever mode starts next. Commands and
    menu taps belong exclusively to the independent command responder.
    """
    gate()
    offered = []
    texts = []
    client = channel()
    handover = [("handover:" + uid, json.dumps(["input", "", "message", "", raw]))
                for uid, raw in mode_channel.handover_inputs() if raw]
    for task, body in handover + client.pending(128):
        value = json.loads(body)
        if value[0] == "command" and value[2] == "/delete":
            client.answer(task, "Message deletion is unavailable in " + mode() + " mode; switch to godelclaw.")
            continue
        if value[0] in ("command", "callback"):
            continue
        if value[0] != "input":
            with (root() / "channel-receipts.jsonl").open("a") as stream:
                stream.write(json.dumps({"task": task, "receipt": value}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            texts.append("[channel receipt; this is delivery status, not a user message]\n" + json.dumps(value))
            offered.append(task)
            continue
        update = value[4]
        import telegram
        kind, message = telegram._extract_message(update)
        message = message or {}
        text = message.get("text") or message.get("caption") or ""
        # Preserve the durable update even for attachment-only messages.
        if not text:
            text = "[attachment received; use the agent attachment tools to inspect it]"
        destination = mode_channel.remember_route(message)
        if mode() == "iter":
            # Use Iter's existing named-channel send interface unchanged.
            (root() / "channels" / (destination + ".py")).write_text(
                'import runtime_host\ndef send(content): return runtime_host.send(content, '
                + repr(destination) + ')\n')
        texts.append("[reply_channel=" + destination + "]\n" +
                     telegram._format_message(update, kind, message, text))
        offered.append(task)
    if offered:
        atomic(root() / "offered-input.json", offered)
        record("input", count=len(offered))
    return "\n".join(texts)


def commit_inputs():
    path = root() / "offered-input.json"
    tasks = read_json(path, [])
    if not tasks:
        return True
    remaining = []
    client = channel()
    for task in tasks:
        try:
            if task.startswith("handover:"):
                mode_channel.acknowledge_handover([task.split(":", 1)[1]])
            else:
                client.acknowledge(task)
        except (OSError, durable_telegram.ChannelError):
            remaining.append(task)
    atomic(path, remaining)
    return not remaining


def send(message, destination="telegram"):
    result = mode_channel.enqueue(str(message), str(destination))
    record("send-queued", destination=str(destination), characters=len(str(message)))
    return result


def route_context():
    return mode_channel.context()


def recover_outbox():
    # Preserve intents from the previous adapter using their original key.
    # Submission is now owned by the control responder, not a tool worker.
    for path in sorted((root() / "outbox").glob("*.json")):
        item = read_json(path, None)
        if item:
            mode_channel.store(shared() / "channel-outbox" / (item["key"] + ".json"), item)
            path.unlink()


def boot():
    if mode() == "omega":
        import helper
        helper.TWO_ARG_COMMANDS.add("send-channel")
    # Provider failure backoff is operational pacing. Keep mode/stop controls
    # responsive while retaining the existing adapter's backoff durations.
    synthetic_llm.time = SimpleNamespace(**{
        name: (sleep if name == "sleep" else getattr(time, name))
        for name in dir(time) if not name.startswith("_")})
    loop_modes.record_active(mode(), os.getpid(), str(root()))
    recover_outbox()
    record("boot", pid=os.getpid(), engine=os.environ["METTACLAW_ACTIVE_ENGINE"])
    print("[mode-runtime] started " + mode(), flush=True)
    return True


def iter_boundary(*args):
    cognitive_health.turn_settled()
    record("boundary")
    gate()
    return True


class Message:
    def __init__(self, value):
        self.value = value

    def model_dump(self, exclude_none=True):
        return {k: v for k, v in self.value.items() if not exclude_none or v is not None}


class IterClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        gate()
        record("provider-request", protocol="tool-calls")
        payload = provider_request(request)
        choice = payload["choices"][0]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=Message(choice["message"]), finish_reason=choice.get("finish_reason"))])


def provider_request(request):
    """Provider fidelity and operator admission are shared across modes."""
    provider_retry_boundary()
    selected = synthetic_llm.current_model()
    record("model-applied", model=selected)
    cognitive_health.expect_turn(mode())
    try:
        response = synthetic_llm.structured_chat(request)
        cognitive_health.turn_completed(len(json.dumps(response)))
        record("provider-response", protocol="tool-calls")
        return response
    except Exception as error:
        delay = getattr(error, "retry_after", 15)
        cognitive_health.turn_failed(type(error).__name__, retry_after=delay)
        record("provider-error", error=type(error).__name__)
        if llm_policy.configuration(mode())["profile"] == "managed":
            sleep(min(delay, 15))
        raise RuntimeError("provider failed: " + type(error).__name__) from None


def anthropic_payload(request, model):
    return provider_protocol.anthropic_body(request, model)


def anthropic_response(response):
    return provider_protocol.normalize_response("anthropic", response)


def install_iter():
    import iter_host
    iter_host.namespace["openai"] = SimpleNamespace(OpenAI=IterClient)
    iter_host.namespace["time"] = SimpleNamespace(sleep=sleep)
    iter_host.namespace["receive"] = receive
    boot()
    return True


def config(key, default):
    overrides = {"commchannel": "telegram", "provider": "Configured",
                 "memoryDirectory": str(root() / "repos/Omega/memory"),
                 "embeddingprovider": "Disabled"}
    if str(key) == "spamShield":
        return "true" if str(default).lower() == "true" else "false"
    return overrides.get(str(key), default)


def provider(prompt, max_tokens, reasoning):
    return synthetic_llm.chat(provider_model(), max_tokens, reasoning, provider_prompt(prompt))


def provider_model():
    gate()
    provider_retry_boundary()
    cognitive_health.expect_turn(mode())
    selected = synthetic_llm.current_model()
    record("provider-request", protocol="command-text", model=selected)
    return selected


def provider_prompt(prompt):
    return route_context() + "\n\n" + str(prompt)


def provider_retry_boundary():
    if llm_policy.configuration(mode())["profile"] == "managed":
        while cognitive_health.retry_wait_seconds() > 0:
            sleep(.25)
    return True


def omega_sleep(seconds):
    commit_inputs()
    cognitive_health.turn_settled()
    record("boundary")
    return sleep(seconds)


def get_prompt(provider):
    return (root() / "repos/Omega/memory/prompt.txt").read_text()


def get_history(limit):
    path = root() / "repos/Omega/memory/history.metta"
    data = path.read_bytes() if path.exists() else b""
    return data[-int(limit):].decode("utf-8", errors="replace") if limit else ""


def append_history(text):
    path = root() / "repos/Omega/memory/history.metta"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(str(text) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    commit_inputs()
    record("checkpoint")
    return "APPEND-FILE-SUCCESS"


def string_replace(text, separators, replacement):
    return "".join(str(replacement) if char in str(separators) else char for char in str(text))


def read_file(path):
    return Path(str(path)).read_text()


def path_exists(path, kind):
    target = Path(str(path))
    exists = target.is_dir() if kind == "directory" else target.is_file()
    return "true" if exists else "false"


def write_file(path, text, append=False):
    target = Path(str(path))
    with target.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write(str(text))
        stream.flush()
        os.fsync(stream.fileno())
    return "verified " + str(target) + ": " + str(target.stat().st_size) + " bytes"


def remember(text):
    with (root() / "remembered.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "text": str(text)}) + "\n")
    return "REMEMBER-SUCCESS"


def query(text):
    path = root() / "remembered.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    return "\n".join(line for line in lines if str(text).casefold() in line.casefold())[-5000:]


def log(*args):
    # Upstream verbose logging includes entire prompts; retain only event names.
    return True


def noop(*args):
    return True
