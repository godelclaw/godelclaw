"""Canonical tool-call boundary described by protocol/ToolCalls.module.

Provider JSON and Omega's Python objects are codecs.  The canonical values are
small enough to inspect as MeTTa atoms and retain opaque provider call IDs.
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ToolSpecV1:
    name: str
    description: str
    parameters: dict

    def atom(self):
        return ("ToolSpecV1", self.name, self.description,
                canonical_json(self.parameters))


@dataclass(frozen=True)
class ToolCallV1:
    call_id: str
    name: str
    arguments: dict

    def atom(self):
        return ("ToolCallV1", self.call_id, self.name,
                canonical_json(self.arguments))


@dataclass(frozen=True)
class ToolResultV1:
    call_id: str
    status: str
    content: str

    def atom(self):
        return ("ToolResultV1", self.call_id, self.status, self.content)


def spec_from_omega(tool):
    properties = {parameter.name: {"type": "string"}
                  for parameter in tool.parameters}
    schema = {"type": "object", "properties": properties,
              "required": list(properties), "additionalProperties": False}
    return ToolSpecV1(str(tool.name), str(tool.description), schema)


def call_from_provider(value):
    function = value.get("function") if isinstance(value, dict) else None
    if not isinstance(function, dict):
        raise ValueError("provider tool call lacks a function")
    call_id, name = value.get("id"), function.get("name")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("provider tool call lacks an id")
    if not isinstance(name, str) or not name:
        raise ValueError("provider tool call lacks a name")
    return ToolCallV1(call_id, name, argument_object(function.get("arguments", {})))


def provider_call(call):
    return {"id": call.call_id, "type": "function", "function": {
        "name": call.name, "arguments": canonical_json(call.arguments)}}


def _omega_call(call):
    return ToolCallV1(str(call.id), str(call.name),
                      argument_object(call.arguments))


def message_from_omega(message):
    result = {"role": str(message.role), "content": message.content}
    if hasattr(message, "callid"):
        result["tool_call_id"] = str(message.callid)
    if hasattr(message, "calls"):
        result["tool_calls"] = [provider_call(_omega_call(call))
                                for call in message.calls]
    return {key: value for key, value in result.items() if value is not None}


def request_from_omega(request):
    specs = [spec_from_omega(tool) for tool in request.tools]
    return {
        "messages": [message_from_omega(message) for message in request.messages],
        "tools": [{"type": "function", "function": {
            "name": spec.name, "description": spec.description,
            "parameters": spec.parameters}} for spec in specs],
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
            call = call_from_provider(value)
            converted = (omega.LLMToolCall().with_id(call.call_id)
                         .with_name(call.name).with_arguments(call.arguments))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            call_id = value.get("id", "") if isinstance(value, dict) else ""
            function = value.get("function", {}) if isinstance(value, dict) else {}
            name = function.get("name", "") if isinstance(function, dict) else ""
            converted = (omega.LLMToolCall().with_id(str(call_id))
                         .with_name(str(name)).with_arguments({})
                         .with_error(str(error)))
        response.add_tool_call(converted)
    return response
