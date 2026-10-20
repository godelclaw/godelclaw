"""Concrete inputs for the frozen twelve-scenario matrix per mode."""
import json


def call(name="echo", arguments=None, ident="call-1"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments if arguments is not None else {})}}


def reply(*calls, content=None, finish="tool_calls", **fields):
    message = {"role": "assistant", **fields}
    if content is not None:
        message["content"] = content
    if calls:
        message["tool_calls"] = list(calls)
    return {"message": message, "finish": finish}


def echo(text="ok", ident="call-1"):
    return call("echo", {"text": text}, ident)


def iter_cases():
    normal = reply(echo())
    cases = {
        "human-roundtrip": {"events": ["[fixture] hello"], "replies": [normal], "iterations": 1},
        "autonomous-continuation": {"replies": [normal, normal], "iterations": 2},
        "nop-in-batch": {"replies": [reply(call("nop"), echo(ident="last")), normal], "iterations": 2},
        "fast-budget": {"replies": [normal] * 51, "iterations": 51},
        "plain-text-retry": {"events": ["[fixture] hello"], "replies": [reply(content="hello there", finish="stop"), normal], "iterations": 1},
        "length-retry": {"replies": [reply(content="unfinished", finish="length"), normal], "iterations": 1},
        "bad-tools": {"replies": [reply(call("missing"),
            {**call("echo", ident="bad-json"), "function": {"name": "echo", "arguments": "{"}},
            {**call("echo", ident="bad-object"), "function": {"name": "echo", "arguments": "[]"}},
            call("raise_error", ident="raises"), call("no_result", ident="none"))], "iterations": 1,
            "files": {"tools/no_result.py": 'DESCRIPTION="No result"\ndef run(): return None\n'}},
        "tool-caps": {"replies": [reply(*[echo("x" * 5001, str(i)) for i in range(12)])], "iterations": 1},
        "transforms-and-reload": {"iterations": 2,
            "replies": [reply(call("maker"), call("znew", ident="before")), reply(call("znew", ident="after"))],
            "files": {
                "transformations/a.py": 'DESCRIPTION="First"\ndef transform(messages, tools): return messages+[{"role":"user","content":"A"}],tools\n',
                "transformations/b.py": 'DESCRIPTION="Failure"\ndef transform(messages, tools): raise ValueError("fixture transform failure")\n',
                "transformations/c.py": 'DESCRIPTION="Last"\ndef transform(messages, tools): return messages+[{"role":"user","content":"C"}],tools\n',
            }},
        "memory-surface": {"iterations": 1, "replies": [normal],
            "variants": [{"files": {"memory/a.txt": "m" * 3000, "memory/_private/key.txt": "HIDDEN"}},
                         {"files": {"memory/a.txt": "m" * 3001, "memory/_private/key.txt": "HIDDEN"}}]},
        "compaction": {"iterations": 2, "replies": [normal, normal],
            "experience": [{"role": "tool" if i % 2 == 0 else "assistant", "content": f"old-{i}", "reasoning": "old-reason", "reasoning_details": "detail"} for i in range(99)]},
        "failure-and-restart": {"iterations": 2, "events": ["[fixture] preserve me"],
            "replies": [{"raise": "fixture provider failure"}, normal], "restart": True},
    }
    # The maker runs in an isolated child; its new file is seen only on reload.
    cases["transforms-and-reload"]["files"]["tools/maker.py"] = (
        'from pathlib import Path\nDESCRIPTION="Install fixture tool"\n'
        'def run():\n Path("tools/znew.py").write_text(' +
        repr('DESCRIPTION="New tool"\ndef run(): return "new"\n') + ')\n return "installed"\n')
    return cases


def omega_cases():
    defaults = {"embeddingprovider": "Fixture", "commchannel": "fixture",
                "provider": "Fixture"}
    def omega_reply(*calls):
        return {"calls": list(calls)}
    def omega_call(name, arguments=None, ident="call-1"):
        return {"id": ident, "name": name, "arguments": arguments or {}}
    cases = {
        "human-roundtrip": {"events": ["hello"], "replies": [omega_reply(omega_call("send", {"content": "hi"}))], "iterations": 1},
        "empty-and-multiple-calls": {"events": ["hello"], "replies": [omega_reply(), omega_reply(omega_call("send", {"content": "hello"}), omega_call("nop", ident="nop"))], "iterations": 2},
        "invalid-and-unknown": {"replies": [omega_reply(omega_call("mystery", {"value": "wrong"}), omega_call("send", {"content": "good"}, "send")), omega_reply(omega_call("metta", {"sexpression": "("}))], "iterations": 2},
        "provider-failure": {"events": ["hello"], "replies": [{"raise": "fixture provider failure"}], "iterations": 1},
        "duplicate-input": {"events": ["same", "same", "", "next"], "replies": [omega_reply(omega_call("pin", {"message": "a"}, "a")), omega_reply(omega_call("pin", {"message": "b"}, "b")), omega_reply(omega_call("pin", {"message": "c"}, "c"))], "iterations": 4, "config": {"maxNewInputLoops": 2}},
        "budget-exhaustion": {"replies": [omega_reply(omega_call("pin", {"message": "a"}, "a")), omega_reply(omega_call("pin", {"message": "b"}, "b"))], "iterations": 4, "config": {"maxNewInputLoops": 2}},
        "wake-boundary": {"replies": [omega_reply(omega_call("pin", {"message": "a"}, "a")), omega_reply(omega_call("pin", {"message": "b"}, "b"))], "iterations": 4, "config": {"maxNewInputLoops": 1, "wakeupInterval": 1}},
        "ordered-batch": {"replies": [omega_reply(omega_call("send", {"content": "first"}, "first"), omega_call("metta", {"sexpression": "(+ 1 2)"}, "calc"), omega_call("send", {"content": "last"}, "last"))], "iterations": 1,
            "trace_contains": ['(COMMAND_RETURN: ((metta "(+ 1 2)") "SUCCESS, RETURN:']},
        "history-feedback": {"events": ["history please"], "replies": [omega_reply(omega_call("missing", {"value": "bad"})), omega_reply(omega_call("send", {"content": "next"}))], "iterations": 2,
            "history": "old history bytes " * 5, "config": {"maxHistory": 31, "maxFeedback": 24}},
        "duplicate-send": {"replies": [omega_reply(omega_call("send", {"content": "same message"}, "a1"), omega_call("send", {"content": "same message"}, "a2")), omega_reply(omega_call("send", {"content": "same message"}, "a3"))], "iterations": 2},
        "extensions-heartbeat": {"replies": [omega_reply(omega_call("fixture-skill", {"text": "hello"}))], "iterations": 1,
            "setup": '(= (fixture-skill $text) (send $text))\n!(add-skill fixture-skill "Fixture action" (text))\n!(add-prompt-extension fixture-extension "FIXTURE_EXTENSION")\n!(add-heartbeat-listener fixture-listener (|-> ($k) (log INFO "fixture" (LISTENER $k))))'},
        "restart": {"events": ["hello"], "replies": [omega_reply(omega_call("send", {"content": "saved"}))], "iterations": 1, "restart": True},
    }
    for case in cases.values():
        case["config"] = {**defaults, **case.get("config", {})}
    return cases
