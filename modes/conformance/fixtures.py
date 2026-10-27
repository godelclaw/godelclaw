"""Concrete inputs for Omega's frozen twelve-scenario matrix.

Iter's matrix qualified the retired port of iter.py; Iter now runs the
vendored MeTTa loop itself."""


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
