"""Iter's host/data boundary, with control flow kept in core.metta.

The pinned upstream helpers (sections 0–3) supply component discovery,
transformations, channels and atomic JSON persistence. Box is a codec boundary:
structured JSON stays opaque, avoiding evaluator-specific dict representations.
The optional fixture module supplies an offline provider and controlled clock.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / "upstream/iter.py"
namespace = {"__file__": str(UPSTREAM), "__name__": "iter_upstream_helpers"}
source = UPSTREAM.read_text().split("# 4. Main loop")[0]
exec(compile(source, str(UPSTREAM), "exec"), namespace)
# In an embedded interpreter sys.executable is CeTTa, not a Python executable.
# Supply the host interpreter explicitly to the unchanged subprocess helper.
PYTHON = os.getenv("ITER_PYTHON", str(Path(sys.prefix) / "bin/python"))
namespace["sys"] = SimpleNamespace(argv=[str(UPSTREAM)], executable=PYTHON, exit=sys.exit)


class Box:
    def __init__(self, value):
        self.value = value


def unbox(value):
    return value.value if isinstance(value, Box) else value


def boxed(value):
    return Box(value) if isinstance(value, (dict, list)) or value is None else value


def boolean(value):
    return value is True or str(value).lower() == "true"


def get(value, key):
    return boxed(unbox(value)[key])


def size(value):
    return len(unbox(value))


def truth(value):
    # Janus exposes a Python bool as (@ true), which SWI-PeTTa's `if` does
    # not treat as its Boolean atom. Use the same explicit atom on both hosts.
    return "true" if bool(unbox(value)) else "false"


def flag(value, key):
    return truth(unbox(value)[key])


def equal(left, right):
    return truth(unbox(left) == unbox(right))


def not_equal(left, right):
    return truth(unbox(left) != unbox(right))


def put(value, key, item):
    unbox(value)[key] = unbox(item)
    return value


def append(value, item):
    unbox(value).append(unbox(item))
    return value


def extend(value, items):
    unbox(value).extend(unbox(items))
    return value


def take(value, count):
    return Box(unbox(value)[:count])


def tail(value, count):
    return Box(unbox(value)[-count:])


def drop_head(value):
    return Box(unbox(value)[1:])


def empty_list():
    return Box([])


def message(role, content):
    return Box({"role": role, "content": unbox(content)})


def step(text):
    return "Step " + namespace["get_current_time"]() + ": " + str(unbox(text))


def concat(a, b):
    return str(unbox(a)) + str(unbox(b))


def initialize():
    global client
    if os.getenv("ITER_FIXTURE_ROOT"):
        import iter_fixture
        iter_fixture.install(namespace)
    try:
        experience = json.loads(Path("experience.json").read_text())
    except FileNotFoundError:
        experience = []
    client = namespace["openai"].OpenAI(
        api_key=namespace["API_KEY"], base_url=namespace["BASE_URL"],
        timeout=600, max_retries=0,
        default_headers={"X-APC-Tenant": "iter", "x-session-id": "fixture" if os.getenv("ITER_FIXTURE_ROOT") else str(namespace["uuid"].uuid4())})
    namespace["time"].sleep(10)
    Path("memory").mkdir(exist_ok=True)
    Path("transformations").mkdir(exist_ok=True)
    return Box(experience)


def observe(experience, post, steps, burst, pending, bucket):
    if os.getenv("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        runtime_host.iter_boundary()
    if os.getenv("ITER_FIXTURE_ROOT"):
        import iter_fixture
        iter_fixture.boundary(unbox(experience), boolean(post), steps, boolean(burst), pending, bucket)
    return True


def sleep(seconds):
    namespace["time"].sleep(seconds)
    return True


def receive():
    return namespace["receive"]()


def save(experience):
    namespace["save_experience"](unbox(experience))
    if os.getenv("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        with open("experience.json", "rb") as stream:
            os.fsync(stream.fileno())
        descriptor = os.open(".", os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        runtime_host.commit_inputs()
        history = unbox(experience)
        tools = 0
        for message in reversed(history):
            if message.get("role") != "tool":
                break
            tools += 1
        runtime_host.record("checkpoint", messages=len(history), tool_results=tools)
    return True


def cleanup_message(value):
    """Codec mutation; the core selects which messages and when to prune."""
    item = unbox(value)
    if item.get("role") == "tool" and len(item.get("content", "")) > 0:
        item["content"] = " [TRUNCATED]"
    for key in ("reasoning", "reasoning_details", "reasoning_content"):
        item.pop(key, None)
    return True


def tools():
    loaded, omitted, errors = namespace["load_tools"]()
    return Box({"snapshot": loaded, "schemas": namespace["native_tools"](loaded),
                "omitted": omitted, "errors": errors})


def tool_limit(count):
    return f"[TOOL LIMIT REACHED: {count} tools are currently omitted. Consolidate or remove tools if they are needed.]"


def memory():
    paths = [p for p in sorted(Path("memory").rglob("*")) if p.is_file()
             and not any(part.startswith("_") for part in p.relative_to("memory").parts)]
    contents = [(p, p.read_text(encoding="utf-8", errors="replace").strip()) for p in paths]
    return Box({"paths": paths, "contents": contents,
                "length": sum(len(content) for _, content in contents)})


def memory_surface(value, contents):
    data = unbox(value)
    if boolean(contents):
        return f"[{3000 - data['length']} CHARACTERS BELOW MAXIMUM]\n./memory/:\n" + "\n\n".join(
            f"{path}:\n{content}" for path, content in data["contents"])
    return "./memory/:\n" + "\n".join(str(path) for path in data["paths"])


def memory_overflow(length):
    return f"[MEMORY FOLDER TOTAL CHARACTER CAPACITY BY FILES NOT BEGINNING WITH _ EXCEEDED BY {length - 3000} CHARS. FIX THIS FIRST.]"


def request_plan(experience, temporary, loaded, surface):
    # Component/file and provider I/O, with request policy selected by core.metta.
    transformations = namespace["load_transformation_descriptions"]()
    prompt = Path("prompt.txt").read_text(encoding="utf-8", errors="replace").strip()
    messages = [{"role": "system", "content": "prompt.txt:\n" + prompt +
                 "\n\n./transformations/:\n" + transformations + "\n\n" + surface}]
    messages += unbox(experience) + unbox(temporary)
    messages, schemas, error = namespace["apply_transformation"](messages, unbox(loaded)["schemas"])
    if error:
        messages += [{"role": "user", "content": error}]
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        messages.insert(0, {"role": "system", "content": runtime_host.route_context()})
    return Box(dict(model=namespace["MODEL"], messages=messages, tools=schemas,
                    tool_choice="required", max_tokens=2524,
                    extra_body={"enable_thinking": True}))


def request(experience, temporary, loaded, surface):
    response = client.chat.completions.create(**unbox(
        request_plan(experience, temporary, loaded, surface)))
    choice = response.choices[0]
    return Box({"message": choice.message.model_dump(exclude_none=True),
                "finish": getattr(choice, "finish_reason", None)})


def retry_no_tool(content):
    return f"[YOUR PREVIOUS RESPONSE CONTAINED NO TOOL CALL AND WAS NOT DELIVERED. CALL AT LEAST ONE TOOL NOW. IF YOU INTENDED THIS CONTENT AS COMMUNICATION, USE send: {unbox(content)!r}]"


def optional(value, key):
    return boxed(unbox(value).get(key))


def decode(arguments):
    try:
        value = json.loads(arguments)
        return Box({"ok": True, "value": value, "object": isinstance(value, dict)})
    except json.JSONDecodeError as error:
        return Box({"ok": False, "error": f"Invalid tool arguments from model: {error}"})


def has_tool(loaded, name):
    return truth(name in unbox(loaded)["snapshot"])


def unknown(name):
    return f"Unknown tool: {name!r}"


def native_processes():
    return truth(os.environ.get("METTACLAW_ACTIVE_ENGINE") != "petta")


def reference_dynamic(loaded, name, arguments):
    """Keep the pinned upstream process helper on hosts without lib/proc."""
    result = namespace["invoke_dynamic"](unbox(loaded)["snapshot"][name][0],
                                          "run", **unbox(arguments))
    return boxed(result["result"]) if result["ok"] else f"Tool execution failed: {result['error']}"


def prepare_dynamic(loaded, name, arguments):
    result_fd, result_file = tempfile.mkstemp(prefix="iter-result-", suffix=".json")
    payload_fd, payload_file = tempfile.mkstemp(prefix="iter-payload-", suffix=".json")
    os.close(result_fd)
    os.close(payload_fd)
    Path(payload_file).write_text(json.dumps({"args": [], "kwargs": unbox(arguments)}))
    path = unbox(loaded)["snapshot"][name][0]
    # Match invoke_dynamic's DEVNULL streams. Descendants must not retain lib/proc
    # pipes after a worker exits, changing its completion/timeout behavior.
    redirect = ("import os,sys; fd=os.open(os.devnull,os.O_RDWR); "
                "[os.dup2(fd,i) for i in (0,1,2)]; "
                "os.close(fd) if fd>2 else None; os.execv(sys.executable,sys.argv[1:])")
    return Box({"argv": [PYTHON, "-c", redirect, PYTHON, str(UPSTREAM), "--invoke", str(path.resolve()), "run", result_file, payload_file],
                "cwd": str(Path.cwd()), "env": [[k, v] for k, v in os.environ.items()],
                "result": result_file, "payload": payload_file})


def expression_field(value, key):
    # Deliberately expose only argv/env as inert expression data for lib/proc.
    return unbox(value)[key]


def finish_dynamic(value, process):
    data = unbox(value)
    status = process[0]
    if str(status) == "Error":
        raise RuntimeError("lib/proc rejected invocation: " + str(process[-1]))
    if os.getenv("ITER_FIXTURE_ROOT") and str(status) == "proc:failed":
        raise RuntimeError("lib/proc rejected fixture invocation: " + str(process[1]))
    try:
        if str(status) == "proc:timed-out":
            return "Tool execution failed: TIMEOUT after 5s"
        try:
            result = json.loads(Path(data["result"]).read_text())
        except Exception:
            code = process[1] if str(status) == "proc:exited" else -1
            if os.getenv("ITER_FIXTURE_ROOT"):
                Path("child-error.log").write_text(str(process[-1]))
            return f"Tool execution failed: Dynamic process exited with code {code} without a valid result"
        return boxed(result["result"]) if result["ok"] else f"Tool execution failed: {result['error']}"
    finally:
        for path in (data["result"], data["payload"]):
            Path(path).unlink(missing_ok=True)


def tool_message(call, output):
    return Box({"role": "tool", "tool_call_id": unbox(call)["id"], "content": step(output)})


def stringify(value):
    return str(unbox(value))


def clip(value):
    return value[:5000] + " [TRUNCATED]" if len(value) > 5000 else value
