"""Bounded SSE-to-Chat-Completions assembly. No I/O, retries or tool execution.

Native transport retains bytes up to its cap; this adapter sees a complete
body only. Deltas are provisional until both finish and [DONE] are present.
"""
import json


def events(body, limit=1048576):
    if not isinstance(body, str) or len(body.encode("utf-8")) > limit:
        raise ValueError("stream body exceeds its bound")
    data = []
    kind = ""
    for line in body.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            if data:
                yield kind, "\n".join(data)
            data, kind = [], ""
        elif not line.startswith(":"):
            field, separator, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data":
                data.append(value if separator else "")
            elif field == "event":
                kind = value
    if data:
        # Some compatible servers close immediately after their complete
        # application terminator, without the final SSE blank line. Accept
        # only this exact sentinel; unfinished JSON events still fail closed.
        if not kind and data == ["[DONE]"]:
            yield kind, "[DONE]"
        else:
            raise ValueError("incomplete SSE event at EOF")


def chat(body, limit=1048576):
    text, reasoning, refusal = [], [], []
    tools = {}
    identity = None
    finish, usage, done = None, {}, False
    for number, (event, data) in enumerate(events(body, limit)):
        if number >= 65536:
            raise ValueError("stream event bound exceeded")
        if done:
            raise ValueError("data after stream completion")
        if data == "[DONE]":
            done = True
            continue
        value = json.loads(data)
        if event == "error" or not isinstance(value, dict) or "error" in value:
            raise ValueError("provider stream error")
        current = value.get("id")
        if not isinstance(current, str) or not current:
            raise ValueError("missing stream identity")
        if identity is None:
            identity = current
        elif identity != current:
            raise ValueError("mixed stream identities")
        if value.get("usage") is not None:
            if not isinstance(value["usage"], dict):
                raise ValueError("invalid stream usage")
            usage = value["usage"]
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("expected one stream choice")
        for choice in choices:
            if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
                raise ValueError("foreign stream choice")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ValueError("invalid stream delta")
            if finish is not None:
                raise ValueError("choice after finish")
            if delta.get("role") not in (None, "assistant"):
                raise ValueError("invalid stream role")
            for field, destination in (("content", text), ("reasoning_content", reasoning),
                                       ("reasoning", reasoning), ("refusal", refusal)):
                part = delta.get(field)
                if part is not None:
                    if not isinstance(part, str):
                        raise ValueError("invalid stream text")
                    destination.append(part)
            calls = delta.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ValueError("invalid stream tools")
            for call in calls:
                index = call.get("index") if isinstance(call, dict) else None
                if type(index) is not int or not 0 <= index < 32 or call.get("type") not in (None, "function"):
                    raise ValueError("invalid stream tool index")
                tool = tools.setdefault(index, {"id": [], "name": [], "arguments": []})
                function = call.get("function", {})
                if not isinstance(function, dict):
                    raise ValueError("invalid stream function")
                for field, part in (("id", call.get("id")), ("name", function.get("name")),
                                    ("arguments", function.get("arguments"))):
                    if part is not None:
                        if not isinstance(part, str):
                            raise ValueError("invalid stream tool text")
                        tool[field].append(part)
            reason = choice.get("finish_reason")
            if reason is not None:
                if reason not in ("stop", "tool_calls", "length", "content_filter", "refusal"):
                    raise ValueError("unknown stream finish")
                finish = reason
    if not done or finish is None or identity is None:
        raise ValueError("truncated provider stream")
    message = {"role": "assistant", "content": "".join(text)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if refusal:
        message["refusal"] = "".join(refusal)
    if tools:
        if finish != "tool_calls" or sorted(tools) != list(range(len(tools))):
            raise ValueError("unfinished tool batch")
        complete = []
        ids = set()
        for _, tool in sorted(tools.items()):
            id_, name, arguments = ("".join(tool[field]) for field in ("id", "name", "arguments"))
            if not id_ or not name or id_ in ids or not isinstance(json.loads(arguments), dict):
                raise ValueError("invalid complete tool call")
            ids.add(id_)
            complete.append({"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}})
        message["tool_calls"] = complete
    elif finish == "tool_calls":
        raise ValueError("missing completed tool batch")
    return json.dumps({"id": identity, "choices": [{"index": 0, "message": message,
                       "finish_reason": finish}], "usage": usage}, ensure_ascii=False)
