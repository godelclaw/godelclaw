"""Provider wire adapters; no credentials, requests, retries or loop state.

The canonical conversation uses Chat Completions message/tool identities.
Reasoning payloads remain opaque and are replayed only to their own provider.
"""
import copy
import hashlib
import json

CACHE_MARKER = "_cache_boundary_"
# Inside a stable part, this separates complete task-transcript chunks. They
# never change once complete, so each can be its own cacheable block.
CHUNK_MARKER = "_cache_chunk_"
OMEGA_DELIMITER = ":-:-:-:"


def chunk_blocks(part):
    part = str(part)
    if CHUNK_MARKER not in part:
        return [part] if part.strip() else []
    return [block.strip("\n") for block in part.split(CHUNK_MARKER) if block.strip()]


def joined(stable):
    """Stable parts for providers that cache by prefix: chunks are plain text."""
    return ["\n\n".join(chunk_blocks(part)) for part in stable]


def split_prompt(prompt):
    text = str(prompt)
    parts = text.split(CACHE_MARKER, 3)
    if len(parts) > 1:
        stable, user = parts[:-1], parts[-1]
    elif OMEGA_DELIMITER in text:
        system, _, user = text.partition(OMEGA_DELIMITER)
        stable, user = [system.strip()], user.strip()
        if not user:
            user = "EMPTY / NO NEW USER INPUT."
    else:
        cut = text.find(" LOOPS_LEFT: ")
        stable, user = ([text[:cut]], text[cut:]) if cut > 0 else ([], text)
    if not user.strip() and stable:
        user = stable.pop()
    return [part for part in stable if part.strip()], user


def anthropic_reasoning(effort=None, enable_thinking=None):
    result = {}
    if effort not in (None, "", "default"):
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("unsupported Anthropic reasoning effort")
        result["output_config"] = {"effort": effort}
    # Adaptive thinking is already mandatory on some models. An omitted
    # setting preserves that model's default; never translate 'none' to a
    # disabled request which those models reject.
    if enable_thinking is True:
        result["thinking"] = {"type": "adaptive"}
    elif enable_thinking is False:
        raise ValueError("disabling Anthropic thinking requires a model-specific capability")
    return result


def cache_key(provider, model, system):
    stable = system.split(" LAST_SKILL_USE_RESULTS: ", 1)[0].strip()
    return provider + ":" + model + ":" + hashlib.sha256(stable.encode()).hexdigest()[:24]


def prompt_observation(prompt):
    """Only sizes and prefix identities; no prompt bytes or token estimates."""
    stable, user = split_prompt(prompt)
    prefix = "".join(stable)
    return dict(stable_chars=len(prefix), dynamic_chars=len(user),
                stable_digest=hashlib.sha256(prefix.encode()).hexdigest(),
                blocks=[dict(chars=len(part), digest=hashlib.sha256(part.encode()).hexdigest()) for part in stable])


def cache_boundaries(stable):
    """System block endpoints of the caller's declared prompt regions."""
    ends, count = [], 0
    for part in stable:
        blocks = chunk_blocks(part)
        if blocks:
            count += len(blocks)
            ends.append(count - 1)
    return ends


def anthropic_cache_system(body, boundaries=()):
    """Keep earlier stable checkpoints as well as the growing history's end.

    Lookback can read a previously written endpoint; it does not create cache
    entries for every unchanged block. Never consume slots already used by a
    caller's tool/message breakpoints, or turn an explicit cache opt-out on.
    """
    system = body.get("system")
    if not isinstance(system, list) or not system:
        return body
    marked = [i for i, block in enumerate(system) if "cache_control" in block]
    if not marked:
        return body
    marker = system[marked[-1]]["cache_control"]
    elsewhere = sum("cache_control" in tool for tool in body.get("tools", []))
    elsewhere += sum("cache_control" in block for message in body.get("messages", [])
                     for block in message.get("content", []) if isinstance(block, dict))
    elsewhere += int("cache_control" in body)
    slots = max(0, 4 - elsewhere - len(marked))
    # The tail checkpoint may itself change. Its predecessor protects the
    # completed history before it; declared region ends protect static text.
    candidates = list(boundaries) + [len(system) - 2, 0]
    for index in candidates:
        if slots == 0:
            break
        if 0 <= index < len(system) and index not in marked:
            system[index]["cache_control"] = copy.deepcopy(marker)
            marked.append(index)
            slots -= 1
    return body


def anthropic_cache_observation(body):
    """Hash the actual encoded blocks in linear time; retain no prompt text."""
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    settings = {key: value for key, value in body.items()
                if key not in ("system", "messages", "max_tokens", "stream", "metadata", "cache_control")}
    prefix = hashlib.sha256(encoded(settings))
    scope = prefix.hexdigest()
    blocks = []
    system = body.get("system", [])
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    for index, block in enumerate(system):
        content = encoded({k: v for k, v in block.items() if k != "cache_control"})
        prefix.update(len(content).to_bytes(8, "big"))
        prefix.update(content)
        marker = block.get("cache_control")
        blocks.append(dict(index=index, bytes=len(content),
                           digest=hashlib.sha256(content).hexdigest(),
                           prefix_digest=prefix.hexdigest(),
                           ttl=marker.get("ttl", "5m") if marker else None))
    return dict(version=1, scope=scope, blocks=blocks,
                messages_digest=hashlib.sha256(encoded(body.get("messages", []))).hexdigest())


def cache_observation(usage):
    def count(value):
        return value if type(value) is int and 0 <= value <= 10**12 else None
    if not isinstance(usage, dict):
        return dict(status="unknown")
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = count(details.get("cached_tokens")) if isinstance(details, dict) else None
    if cached is None:
        cached = count(usage.get("cache_read_input_tokens", usage.get("prompt_cache_hit_tokens")))
    total = count(usage.get("prompt_tokens", usage.get("input_tokens")))
    if "cache_read_input_tokens" in usage and total is not None:
        write = count(usage.get("cache_creation_input_tokens", 0))
        total = total + cached + write if cached is not None and write is not None else None
    if total is None or cached is None or cached > total:
        return dict(status="unknown")
    return dict(status="reported", input_tokens=total, cached_tokens=cached,
                fraction=cached / total if total else 0.0)


def synthetic_body(body):
    """Map the compatible conversation's effort to Synthetic's wire field."""
    result = dict(body)
    reasoning = result.pop("reasoning", None)
    if reasoning is not None:
        if not isinstance(reasoning, dict) or set(reasoning) - {"effort"}:
            raise ValueError("unsupported Synthetic reasoning options")
        effort = reasoning.get("effort")
        if effort not in (None, "", "default", "low", "medium", "high"):
            raise ValueError("unsupported Synthetic reasoning effort")
        if effort not in (None, "", "default"):
            if "reasoning_effort" in result and result["reasoning_effort"] != effort:
                raise ValueError("conflicting Synthetic reasoning effort")
            result["reasoning_effort"] = effort
    if "reasoning_effort" in result and result["reasoning_effort"] not in ("low", "medium", "high"):
        raise ValueError("unsupported Synthetic reasoning effort")
    return result


def command_body(protocol, model, tokens, effort, prompt):
    stable, user = split_prompt(prompt)
    if protocol == "anthropic":
        body = {"model": model, "max_tokens": int(tokens),
                "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}]}
        blocks = [{"type": "text", "text": text} for part in stable for text in chunk_blocks(part)]
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
            body["system"] = blocks
        body.update(anthropic_reasoning(str(effort)))
        anthropic_cache_system(body, cache_boundaries(stable))
        return "/messages", body
    stable = joined(stable)
    system = "".join(stable)
    if protocol == "responses":
        body = {"model": model, "input": user, "max_output_tokens": int(tokens),
                "store": False}
        if str(effort) not in ("default", "", "None"):
            body["reasoning"] = {"effort": str(effort)}
        if stable:
            body.update(instructions=system, prompt_cache_key=cache_key("openai", model, stable[0]))
        return "/responses", body
    if protocol != "chat-completions":
        raise ValueError("unsupported provider protocol")
    messages = ([{"role": "system", "content": system}] if stable else [])
    messages.append({"role": "user", "content": user})
    body = {"model": model, "messages": messages, "max_tokens": int(tokens)}
    if str(effort) not in ("default", "", "None"):
        body["reasoning"] = {"effort": str(effort)}
    return "/chat/completions", body


def anthropic_body(request, model):
    messages, system = [], []

    def text_blocks(value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [{"type": "text", "text": value}]
        if isinstance(value, list) and all(isinstance(b, dict) and b.get("type") == "text" for b in value):
            return copy.deepcopy(value)
        raise ValueError("unsupported Anthropic message content")

    for item in request["messages"]:
        role = item["role"]
        if role in ("system", "developer"):
            if messages:
                raise ValueError("system instructions must precede the conversation")
            system.extend(text_blocks(item.get("content")))
            continue
        if role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": item["tool_call_id"],
                       "content": item.get("content", "")}]
            if messages and messages[-1]["role"] == "user" and any(
                    b["type"] != "tool_result" for b in messages[-1]["content"]):
                raise ValueError("tool results must precede user text")
        elif role in ("user", "assistant"):
            blocks = text_blocks(item.get("content"))
            reasoning = item.get("reasoning_details", []) if role == "assistant" else []
            if reasoning:
                if not isinstance(reasoning, list) or any(
                        b.get("type") not in ("thinking", "redacted_thinking") for b in reasoning):
                    raise ValueError("unsupported Anthropic reasoning")
                blocks = copy.deepcopy(reasoning) + blocks
            for call in item.get("tool_calls", []) or []:
                if role != "assistant":
                    raise ValueError("tool calls require an assistant message")
                fn = call["function"]
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": fn["name"], "input": json.loads(fn["arguments"])})
        else:
            raise ValueError("unsupported Anthropic message role")
        if blocks:
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
    tools = []
    for tool in request.get("tools", []):
        fn = tool["function"]
        tools.append({"name": fn["name"], "description": fn.get("description", ""),
                      "input_schema": fn["parameters"]})
    payload = {"model": model, "max_tokens": request["max_tokens"], "messages": messages}
    if tools:
        payload.update(tools=tools, tool_choice={"type": "auto"})
    if system:
        if request.get("cache_system", True):
            system[-1]["cache_control"] = {"type": "ephemeral"}
        payload["system"] = system
    extra = request.get("extra_body", {})
    effort = request.get("reasoning", extra.get("reasoning", {})).get("effort")
    payload.update(anthropic_reasoning(effort, extra.get("enable_thinking")))
    anthropic_cache_system(payload)
    return payload


def responses_body(request, model):
    items, system = [], []
    for message in request["messages"]:
        role = message["role"]
        if role in ("system", "developer"):
            if items:
                raise ValueError("system instructions must precede the conversation")
            system.append(message.get("content", ""))
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message.get("content", "")})
        elif role in ("user", "assistant"):
            for reasoning in message.get("reasoning_details", []) or []:
                if role != "assistant" or reasoning.get("type") != "reasoning":
                    raise ValueError("unsupported Responses reasoning")
                items.append(copy.deepcopy(reasoning))
            content = message.get("content")
            if content:
                if not isinstance(content, str):
                    raise ValueError("unsupported Responses content")
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls", []) or []:
                if role != "assistant":
                    raise ValueError("tool calls require an assistant message")
                fn = call["function"]
                items.append({"type": "function_call", "call_id": call["id"],
                              "name": fn["name"], "arguments": fn["arguments"]})
        else:
            raise ValueError("unsupported Responses message role")
    payload = {"model": model, "input": items, "max_output_tokens": request["max_tokens"],
               "store": False, "include": ["reasoning.encrypted_content"]}
    if system:
        text = "\n\n".join(system)
        payload.update(instructions=text, prompt_cache_key=cache_key("openai", model, text))
    if "reasoning" in request:
        payload["reasoning"] = request["reasoning"]
    if request.get("tools"):
        payload["tools"] = [{"type": "function", **tool["function"]} for tool in request["tools"]]
        payload["tool_choice"] = request.get("tool_choice", "auto")
    return payload


def structured_body(protocol, model, request):
    if protocol == "anthropic":
        return "/messages", anthropic_body(request, model)
    if protocol == "responses":
        return "/responses", responses_body(request, model)
    if protocol != "chat-completions":
        raise ValueError("unsupported provider protocol")
    result = {k: copy.deepcopy(v) for k, v in request.items() if k not in ("model", "extra_body", "cache_system")}
    result.update(copy.deepcopy(request.get("extra_body", {})))
    result["model"] = model
    return "/chat/completions", result


def canonical_usage(usage):
    result = copy.deepcopy(usage or {})
    # Missing usage is not an authoritative observation of zero output.
    for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        if source in result:
            result[target] = result[source]
    return result


def normalize_response(protocol, payload):
    """Keep provider completion, usage and signed/opaque reasoning metadata."""
    if protocol == "chat-completions":
        choice = payload["choices"][0]
        message = copy.deepcopy(choice["message"])
        reason = choice.get("finish_reason", "unknown")
        result = {"choices": [{"message": message, "finish_reason": reason}],
                  "provider_stop": reason, "usage": copy.deepcopy(payload.get("usage", {}))}
    elif protocol == "anthropic":
        text, calls, reasoning = [], [], []
        for block in payload["content"]:
            kind = block["type"]
            if kind == "text":
                text.append(block["text"])
            elif kind == "tool_use":
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
            elif kind in ("thinking", "redacted_thinking"):
                reasoning.append(copy.deepcopy(block))
            else:
                raise ValueError("unsupported Anthropic response block")
        message = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = calls
        if reasoning:
            message["reasoning_details"] = reasoning
        raw = payload.get("stop_reason", "unknown")
        reason = {"max_tokens": "length", "tool_use": "tool_calls", "end_turn": "stop",
                  "stop_sequence": "stop"}.get(raw, raw)
        usage = payload.get("usage", {})
        result = {"choices": [{"message": message, "finish_reason": reason}], "provider_stop": raw,
                  "usage": canonical_usage(usage)}
    elif protocol == "responses":
        text, calls, reasoning, refusals = [], [], [], []
        for item in payload["output"]:
            kind = item["type"]
            if kind == "message":
                for block in item["content"]:
                    if block["type"] == "output_text":
                        text.append(block["text"])
                    elif block["type"] == "refusal":
                        refusals.append(block["refusal"])
                    else:
                        raise ValueError("unsupported Responses message block")
            elif kind == "function_call":
                calls.append({"id": item["call_id"], "type": "function", "function": {
                    "name": item["name"], "arguments": item["arguments"]}})
            elif kind == "reasoning":
                reasoning.append(copy.deepcopy(item))
            else:
                raise ValueError("unsupported Responses output item")
        message = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = calls
        if reasoning:
            message["reasoning_details"] = reasoning
        if refusals:
            message["refusal"] = "\n".join(refusals)
        status = payload.get("status", "unknown")
        incomplete = (payload.get("incomplete_details") or {}).get("reason")
        reason = ("length" if status == "incomplete" and incomplete == "max_output_tokens" else
                  "refusal" if refusals else "tool_calls" if calls and status == "completed" else
                  "stop" if status == "completed" else status)
        usage = payload.get("usage") or {}
        result = {"choices": [{"message": message, "finish_reason": reason}],
                  "provider_stop": incomplete or status,
                  "usage": canonical_usage(usage)}
    else:
        raise ValueError("unsupported provider protocol")
    result["response_id"] = payload.get("id")
    return result
