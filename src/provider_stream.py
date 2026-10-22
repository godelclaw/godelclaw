"""Bounded SSE-to-Chat-Completions assembly. No I/O, retries or tool execution.

Native transport retains bytes up to its cap; this adapter sees a complete
body only. Deltas are provisional until both finish and [DONE] are present.
"""
import json


def anthropic(body, limit=1048576):
    """Assemble Messages SSE without granting partial blocks any authority."""
    message = None
    blocks, partial, closed = {}, {}, set()
    stopped = False
    stop_reason = None
    for number, (event, data) in enumerate(events(body, limit)):
        if number >= 65536 or stopped:
            raise ValueError("data after completion or too many events")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("invalid Messages event")
        kind = value.get("type")
        if event and event != kind:
            raise ValueError("conflicting Messages event type")
        if kind == "error":
            raise ValueError("provider stream error")
        if kind == "ping":
            continue
        if kind == "message_start":
            if message is not None:
                raise ValueError("duplicate message start")
            message = value.get("message")
            if (not isinstance(message, dict) or not message.get("id")
                    or message.get("role") != "assistant" or message.get("content")):
                raise ValueError("invalid message start")
            message = dict(message)
        elif kind == "content_block_start":
            index = value.get("index")
            block = value.get("content_block")
            if (message is None or type(index) is not int or index < 0
                    or index in blocks or not isinstance(block, dict)):
                raise ValueError("invalid block start")
            blocks[index] = dict(block)
            partial[index] = {}
        elif kind == "content_block_delta":
            index, delta = value.get("index"), value.get("delta")
            if type(index) is not int or index not in blocks or index in closed or not isinstance(delta, dict):
                raise ValueError("delta outside block")
            block = blocks[index]
            fields = {
                "text_delta": ("text", "text", "text"),
                "thinking_delta": ("thinking", "thinking", "thinking"),
                "signature_delta": ("thinking", "signature", "signature"),
                "input_json_delta": ("tool_use", "input", "partial_json"),
            }
            spec = fields.get(delta.get("type"))
            if spec is None:
                continue
            expected, target, source = spec
            if block.get("type") != expected or not isinstance(delta.get(source), str):
                raise ValueError("block/delta kind mismatch")
            partial[index].setdefault(target, []).append(delta[source])
        elif kind == "content_block_stop":
            index = value.get("index")
            if type(index) is not int or index not in blocks or index in closed:
                raise ValueError("invalid block stop")
            for field, pieces in partial[index].items():
                joined = "".join(pieces)
                if field == "input":
                    parsed = json.loads(joined)
                    if not isinstance(parsed, dict):
                        raise ValueError("tool input must be an object")
                    blocks[index][field] = parsed
                else:
                    blocks[index][field] = blocks[index].get(field, "") + joined
            closed.add(index)
        elif kind == "message_delta":
            if message is None or not isinstance(value.get("delta"), dict):
                raise ValueError("invalid message delta")
            message.update(value["delta"])
            stop_reason = value["delta"].get("stop_reason", stop_reason)
            usage = value.get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError("invalid usage")
            message["usage"] = {**message.get("usage", {}), **usage}
        elif kind == "message_stop":
            if (message is None or closed != set(blocks) or stop_reason is None
                    or sorted(blocks) != list(range(len(blocks)))):
                raise ValueError("unfinished message")
            stopped = True
    if not stopped:
        raise ValueError("truncated Messages stream")
    message["content"] = [blocks[index] for index in sorted(blocks)]
    return json.dumps(message, ensure_ascii=False)


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
