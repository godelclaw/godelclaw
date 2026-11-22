"""Test-only scripted OpenAI boundary and observation of the pinned reference."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ["ITER_FIXTURE_ROOT"]) if "ITER_FIXTURE_ROOT" in os.environ else Path(".")
SCRIPT = json.loads((ROOT / "scenario.json").read_text()) if (ROOT / "scenario.json").exists() else {}
TRACE = ROOT / "trace.jsonl"
_boundary = 0
_receive = 0
_reply = 0


def record(kind, **fields):
    with TRACE.open("a") as f:
        f.write(json.dumps({"kind": kind, **fields}, ensure_ascii=False) + "\n")


def boundary(experience, post, steps, burst, pending, bucket):
    global _boundary
    persisted = ROOT / "experience.json"
    record("boundary", iteration=_boundary, experience=experience,
           post=post, steps=steps, burst=burst, pending=pending, bucket=bucket,
           persisted=json.loads(persisted.read_text()) if persisted.exists() else None)
    _boundary += 1
    if _boundary > SCRIPT["iterations"]:
        os._exit(0)


def receive():
    global _receive
    events = SCRIPT.get("events", [])
    value = events[_receive] if _receive < len(events) else ""
    _receive += 1
    record("receive", event=value)
    return value


def sleep(seconds):
    record("sleep", seconds=seconds)


class Message:
    def __init__(self, data):
        self.data = json.loads(json.dumps(data))
        self.content = data.get("content")
        self.tool_calls = [SimpleNamespace(id=c["id"], type=c["type"],
                           function=SimpleNamespace(**c["function"]))
                           for c in data.get("tool_calls", [])]

    def model_dump(self, exclude_none=False):
        data = dict(self.data)
        data["content"] = self.content
        if self.tool_calls:
            data["tool_calls"] = [{"id": c.id, "type": c.type,
                                   "function": vars(c.function)} for c in self.tool_calls]
        if exclude_none:
            data = {k: v for k, v in data.items() if v is not None}
        return data


class OpenAI:
    def __init__(self, **config):
        record("client", **config)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        global _reply
        record("request", **kwargs)
        replies = SCRIPT["replies"]
        reply = replies[_reply]
        _reply += 1
        if "raise" in reply:
            record("provider_error", error=reply["raise"])
            raise RuntimeError(reply["raise"])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=Message(reply["message"]), finish_reason=reply.get("finish", "tool_calls"))], usage=None)


def install(ns):
    ns["openai"] = SimpleNamespace(OpenAI=OpenAI)
    ns["time"] = SimpleNamespace(sleep=sleep)
    ns["uuid"] = SimpleNamespace(uuid4=lambda: "fixture")
    ns["get_current_time"] = lambda: "2026-01-01 00:00:00"
    ns["receive"] = receive
    original_save = ns["save_experience"]
    def save(experience):
        original_save(experience)
        record("save", experience=experience)
    ns["save_experience"] = save


def reference(path):
    upstream_dir = str(Path(path).resolve().parent)
    if upstream_dir not in sys.path:
        sys.path.insert(0, upstream_dir)
    stubbed_openai = False
    if "openai" not in sys.modules:
        try:
            import openai  # noqa: F401
        except ModuleNotFoundError:
            sys.modules["openai"] = SimpleNamespace(OpenAI=OpenAI)
            stubbed_openai = True
    text = Path(path).read_text()
    marker = text.index("try:\n    with open(\"experience.json\"")
    ns = {"__file__": str(Path(path).resolve()), "__name__": "iter_reference"}
    exec(compile(text[:marker], str(path), "exec"), ns)
    install(ns)
    if stubbed_openai:
        orig_popen = ns["subprocess"].Popen
        runner = (
            "import os,sys,types,runpy; sys.argv=sys.argv[1:]; "
            "sys.path.insert(0,os.path.dirname(os.path.abspath(sys.argv[0]))); "
            "sys.modules.setdefault('openai',types.SimpleNamespace()); "
            "runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        def wrapped_popen(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) >= 3 and cmd[2] == "--invoke":
                cmd = [cmd[0], "-c", runner, *cmd[1:]]
            return orig_popen(cmd, *args, **kwargs)
        ns["subprocess"] = SimpleNamespace(**{**vars(ns["subprocess"]), "Popen": wrapped_popen})
    line = next(i for i, s in enumerate(text.splitlines(), 1) if s == "while True:")
    def observe(frame, event, arg):
        if frame.f_code.co_filename == str(path) and event == "line" and frame.f_lineno == line:
            state = frame.f_globals
            boundary(state["experience"], state["post_task_mode"], state["autonomous_steps"],
                     state["new_burst"], state["pending_event_append"], state["cleanup_bucket"])
        return observe
    sys.settrace(observe)
    exec(compile("\n" * text[:marker].count("\n") + text[marker:], str(path), "exec"), ns)


if __name__ == "__main__":
    reference(sys.argv[1])
