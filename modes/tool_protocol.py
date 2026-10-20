"""Canonical MeTTa tool records described by protocol/ToolCalls.module.

Provider JSON and Omega's Python objects are boundary codecs.  Between them the
canonical value is a MeTTa constructor around the runtime's ordinary JsonValue
tree, so no JSON document is hidden inside a string.
"""

import hashlib
import json
from pathlib import Path


SCHEMA = Path(__file__).with_name("protocol") / "ToolCalls.module"


def schema_sha256():
    return hashlib.sha256(SCHEMA.read_bytes()).hexdigest()


def canonical_json(value):
    """Canonical JSON for a structured carrier; reject non-JSON values."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def argument_object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    # Round-trip now so later projections cannot discover an unserializable
    # host object after the call was admitted.
    return json.loads(canonical_json(value))


def json_atom(value):
    """Project a Python JSON value to the Json* atoms used by CeTTa."""
    if value is None:
        return "JsonNull"
    if isinstance(value, bool):
        return ("JsonBool", value)
    if isinstance(value, str):
        return ("JsonString", value)
    if isinstance(value, (int, float)):
        return ("JsonNumber", canonical_json(value))
    if isinstance(value, list):
        return ("JsonArray", tuple(json_atom(item) for item in value))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return ("JsonObject", tuple(
            ("JsonPair", key, json_atom(item))
            for key, item in sorted(value.items())))
    raise ValueError("value is not JSON")


def json_value(atom):
    """Invert ``json_atom`` and reject ambiguous or malformed records."""
    if atom == "JsonNull":
        return None
    if not isinstance(atom, tuple) or not atom:
        raise ValueError("expected a JsonValue atom")
    tag = atom[0]
    if tag == "JsonString" and len(atom) == 2 and isinstance(atom[1], str):
        return atom[1]
    if tag == "JsonBool" and len(atom) == 2 and isinstance(atom[1], bool):
        return atom[1]
    if tag == "JsonNumber" and len(atom) == 2 and isinstance(atom[1], str):
        value = json.loads(atom[1])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("malformed JsonNumber")
        return value
    if tag == "JsonArray" and len(atom) == 2 and isinstance(atom[1], tuple):
        return [json_value(item) for item in atom[1]]
    if tag == "JsonObject" and len(atom) == 2 and isinstance(atom[1], tuple):
        result = {}
        for pair in atom[1]:
            if (not isinstance(pair, tuple) or len(pair) != 3 or
                    pair[0] != "JsonPair" or not isinstance(pair[1], str)):
                raise ValueError("malformed JsonPair")
            if pair[1] in result:
                raise ValueError("duplicate JSON object key: " + pair[1])
            result[pair[1]] = json_value(pair[2])
        return result
    raise ValueError("malformed JsonValue atom")


def _record(tag, fields):
    return (tag, ("JsonObject", tuple(
        ("JsonPair", name, json_atom(value)) for name, value in fields)))


def _fields(atom, tag, required):
    if not isinstance(atom, tuple) or len(atom) != 2 or atom[0] != tag:
        raise ValueError("expected " + tag)
    value = json_value(atom[1])
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(tag + " fields must be " + ", ".join(required))
    return value


def ToolSpecV1(name, description, parameters):
    return _record("ToolSpecV1", (("name", str(name)),
        ("description", str(description)), ("parameters", parameters)))


def ToolCallV1(call_id, name, arguments):
    return _record("ToolCallV1", (("id", str(call_id)),
        ("name", str(name)), ("arguments", argument_object(arguments))))


def ToolResultV1(call_id, status, content):
    return _record("ToolResultV1", (("id", str(call_id)),
        ("status", str(status)), ("content", str(content))))


def spec_from_omega(tool):
    properties = {parameter.name: {"type": "string"}
                  for parameter in tool.parameters}
    schema = {"type": "object", "properties": properties,
              "required": list(properties), "additionalProperties": False}
    return ToolSpecV1(tool.name, tool.description, schema)


def call_from_provider(value):
    function = value.get("function") if isinstance(value, dict) else None
    if not isinstance(function, dict):
        raise ValueError("provider tool call lacks a function")
    call_id, name = value.get("id"), function.get("name")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("provider tool call lacks an id")
    if not isinstance(name, str) or not name:
        raise ValueError("provider tool call lacks a name")
    return ToolCallV1(call_id, name, function.get("arguments", {}))


def provider_call(call):
    fields = _fields(call, "ToolCallV1", ("id", "name", "arguments"))
    arguments = argument_object(fields["arguments"])
    return {"id": fields["id"], "type": "function", "function": {
        "name": fields["name"], "arguments": canonical_json(arguments)}}


def _omega_call(call):
    return ToolCallV1(call.id, call.name, call.arguments)


def result_from_omega(message):
    return ToolResultV1(message.callid, "returned", message.content or "")


def message_from_omega(message):
    result = {"role": str(message.role), "content": message.content}
    if hasattr(message, "callid"):
        returned = _fields(result_from_omega(message), "ToolResultV1",
                           ("id", "status", "content"))
        result["tool_call_id"] = returned["id"]
        result["content"] = returned["content"]
    if hasattr(message, "calls"):
        result["tool_calls"] = [provider_call(_omega_call(call))
                                for call in message.calls]
    return {key: value for key, value in result.items() if value is not None}


def request_from_omega(request):
    specs = [spec_from_omega(tool) for tool in request.tools]
    specs = [_fields(spec, "ToolSpecV1",
                     ("name", "description", "parameters")) for spec in specs]
    return {
        "messages": [message_from_omega(message) for message in request.messages],
        "tools": [{"type": "function", "function": {
            "name": spec["name"], "description": spec["description"],
            "parameters": spec["parameters"]}} for spec in specs],
        "tool_choice": "required",
        "max_tokens": int(request.max_tokens),
        "reasoning_effort": str(request.reasoning_mode),
    }


def response_to_omega(payload, omega):
    response = omega.LLMResponse()
    try:
        message = payload["choices"][0]["message"]
        calls = message.get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return response.with_error("provider response lacks a message")
    for value in calls:
        try:
            call = _fields(call_from_provider(value), "ToolCallV1",
                           ("id", "name", "arguments"))
            converted = (omega.LLMToolCall().with_id(call["id"])
                         .with_name(call["name"]).with_arguments(call["arguments"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            call_id = value.get("id", "") if isinstance(value, dict) else ""
            function = value.get("function", {}) if isinstance(value, dict) else {}
            name = function.get("name", "") if isinstance(function, dict) else ""
            converted = (omega.LLMToolCall().with_id(str(call_id))
                         .with_name(str(name)).with_arguments({})
                         .with_error(str(error)))
        response.add_tool_call(converted)
    return response
