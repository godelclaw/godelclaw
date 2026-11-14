"""Production host for the isolated MeTTa loops.

Only lifecycle, provider, filesystem and CWP channel boundaries live here.
The cognitive loops remain in iter/core.metta and Omega/src/loop.metta.
"""
import json
import importlib.util
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
import llm_policy


def root():
    return Path(os.environ["METTACLAW_MODE_RUNTIME"])


def shared():
    return Path(os.environ["METTACLAW_MODE_STATE_ROOT"])


def mode():
    return os.environ["METTACLAW_ACTIVE_LOOP_MODE"]


def standalone():
    return os.environ.get("METTACLAW_MODE_PROFILE") == "standalone"


def standalone_flag():
    return "true" if standalone() else "false"


def native_memory_flag():
    """MeTTa Boolean spelling for the qualified CeTTa semantic-memory path."""
    return "false" if standalone() else "true"


def terminal():
    spec = importlib.util.spec_from_file_location("mode_terminal", root() / "terminal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def terminal_enqueue(content):
    path = root() / "terminal-outbox" / (f"{time.time_ns():020d}-{os.getpid()}.json")
    mode_channel.store(path, {"text": str(content)})
    return True


def drain_terminal():
    for path in sorted((root() / "terminal-outbox").glob("*.json")):
        item = json.loads(path.read_text())
        print(item["text"], flush=True)
        path.unlink()
    return True


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
    if standalone():
        return True
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
    if standalone():
        return terminal().receive()
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
    if standalone():
        return True
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
    if standalone():
        if str(destination) not in ("telegram", "terminal"):
            raise ValueError("standalone mode has only the terminal channel")
        terminal().send(str(message))
        sys.stdout.flush()
        return "SUCCESS"
    result = mode_channel.enqueue(str(message), str(destination))
    record("send-queued", destination=str(destination), characters=len(str(message)))
    return result


def route_context():
    if standalone():
        return ""
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
        # Older Omega pins registered textual commands here. Current Omega
        # advertises structured tools, so there may be no parser registry.
        import omega_helper
        if hasattr(omega_helper, "TWO_ARG_COMMANDS"):
            omega_helper.TWO_ARG_COMMANDS.add("send-channel")
    if not standalone():
        loop_modes.record_active(mode(), os.getpid(), str(root()))
        recover_outbox()
    record("boot", pid=os.getpid())
    print("[mode-runtime] started " + mode(), flush=True)
    return True


def iter_boundary(*args):
    cognitive_health.turn_settled()
    record("boundary")
    gate()
    return True


def install_iter():
    import iter_host
    # Supervised Iter calls its provider natively (iter:structured); the
    # upstream SDK client it builds at start is never used.
    iter_host.namespace["openai"] = SimpleNamespace(OpenAI=lambda **settings: None)
    iter_host.namespace["time"] = SimpleNamespace(sleep=sleep)
    iter_host.namespace["receive"] = receive
    boot()
    return True


def omega_request(request):
    """An Omega request as the canonical request of the native structured
    call (modes/omega/native/host.metta), written as JSON; returns its path.
    Supervised, the system message leads with the reply route."""
    import tool_protocol
    value = tool_protocol.request_from_omega(request)
    # The native request names its reasoning effort as a reasoning option.
    value["reasoning"] = {"effort": value.pop("reasoning_effort")}
    if value["messages"]:
        route = route_context().strip()
        if route:
            first = value["messages"][0]
            if first.get("role") == "system":
                first["content"] = route + "\n\n" + first.get("content", "")
    path = root() / ".omega-provider-request.json"
    atomic(path, value)
    record("provider-request", protocol="tool-calls")
    return str(path)


def omega_response(request, path):
    """The native call's reply as Omega's response, checked against the
    request's tools as Omega checks a provider's. A failed call (no reply
    path) answers with no calls, which Omega reports."""
    import providers as omega_providers
    import tool_protocol
    response = omega_providers.LLMResponse()
    if str(path):
        reply = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        response = tool_protocol.response_to_omega({"choices": [{"message": reply.get("message")}]},
                                                   omega_providers)
        record("provider-response", protocol="tool-calls")
    else:
        record("provider-error", error="native-call-failed")
    return omega_providers._validate_response(request, response)


def install_omega():
    import providers as omega_providers
    if standalone():
        load_omega_provider_plugins()
    else:
        # Supervised calls are native (omega-host:chat); Omega only starts
        # its configured provider.
        omega_providers.registerLLMProvider("Configured", omega_providers.LLMProvider())
    boot()
    return True


def config(key, default):
    if standalone():
        import config as omega_config
        defaults = {"commchannel": "terminal", "embeddingprovider": "Disabled",
                    "memoryDirectory": str(root() / "repos/Omega/memory")}
        value = omega_config.config_get_by_key(str(key), defaults.get(str(key), default))
        if str(key) == "commchannel":
            if os.environ.get("OMEGA_commchannel", "terminal") != "terminal":
                raise ValueError("standalone Omega core supports commchannel=terminal")
            return "terminal"
        if str(key) == "embeddingprovider":
            return "Disabled"
        if str(key) == "spamShield":
            return "true" if str(value).lower() == "true" else "false"
        return value
    overrides = {"commchannel": "telegram", "provider": "Configured",
                 "memoryDirectory": str(root() / "repos/Omega/memory"),
                 "embeddingprovider": "Disabled"}
    if str(key) == "spamShield":
        return "true" if str(default).lower() == "true" else "false"
    return overrides.get(str(key), default)


def init_config():
    if standalone():
        import config as omega_config
        omega_config.init_config([])
    return True


def start_provider(provider):
    if standalone():
        import providers as omega_providers
        load_omega_provider_plugins()
        omega_providers.llmProviderStart(str(provider))
    return True


def load_omega_provider_plugins():
    """Register the standalone provider slice without SDK-name shadowing."""
    spec = importlib.util.spec_from_file_location(
        "lib_llm_ext", root() / "repos/Omega/providers/lib_llm_ext.py")
    library = importlib.util.module_from_spec(spec)
    sys.modules["lib_llm_ext"] = library
    spec.loader.exec_module(library)
    for name in ("openaiapi", "openai"):
        spec = importlib.util.spec_from_file_location(
            "omega_provider_" + name,
            root() / "repos/Omega/providers" / (name + ".py"))
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        plugin.loadOmegaPlugin()
    return True


def provider_retry_boundary():
    if llm_policy.configuration(mode())["profile"] == "managed":
        while cognitive_health.retry_wait_seconds() > 0:
            sleep(.25)
    return True


def omega_sleep(seconds):
    commit_inputs()
    if not standalone():
        cognitive_health.turn_settled()
    record("boundary")
    return sleep(seconds)


def get_prompt(provider):
    if standalone():
        path = root() / ("repos/Omega/memory/prompt_" + str(provider) + ".txt")
        if path.exists():
            return path.read_text()
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


def join_path(parts):
    import omega_helper
    return omega_helper.joinPath(parts)


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
