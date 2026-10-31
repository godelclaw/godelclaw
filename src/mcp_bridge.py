import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from tool_output import retain_text

def _json(data):
    text = json.dumps(data, ensure_ascii=True, sort_keys=True)
    try:
        limit = int(os.environ.get("METTACLAW_MCP_MAX_OUTPUT_CHARS", "40000"))
    except ValueError:
        limit = 40000
    if limit > 0 and len(text) > limit:
        return json.dumps(
            {
                "truncated": True,
                "chars": len(text),
                "prefix": text[:limit],
                "artifact": retain_text(text),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    return text


def _error(message):
    return _json({"error": str(message)})


def _expand(value):
    return os.path.expandvars(str(value))


def _candidate_config_paths():
    explicit = os.environ.get("METTACLAW_MCP_CONFIG") or os.environ.get("MCP_CONFIG_PATH")
    if explicit:
        yield Path(_expand(explicit))

    cwd = Path.cwd().resolve()
    for root in (cwd, *cwd.parents):
        candidate = root / ".mcp.json"
        yield candidate
        if root == Path.home():
            break

    yield Path.home() / ".mcp.json"


def _load_config():
    seen = set()
    for path in _candidate_config_paths():
        if path in seen:
            continue
        seen.add(path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError(f"{path} has no object field mcpServers")
        return servers
    raise FileNotFoundError("no .mcp.json found")


def _server(name):
    servers = _load_config()
    spec = servers.get(str(name))
    if not isinstance(spec, dict):
        available = ", ".join(sorted(servers))
        raise KeyError(f"unknown MCP server {name!r}; available: {available}")
    return spec


def _headers(spec):
    headers = spec.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object")
    return {str(k): _expand(v) for k, v in headers.items()}


def _env(spec):
    env = spec.get("env")
    if env is None:
        return None
    if not isinstance(env, dict):
        raise ValueError("env must be an object")
    return {str(k): _expand(v) for k, v in env.items()}


def _timeout(spec, key, default):
    try:
        return max(float(spec.get(key, os.environ.get(key.upper(), default))), 0.1)
    except (TypeError, ValueError):
        return default


@asynccontextmanager
async def _streams(spec):
    transport = str(spec.get("type", "stdio")).lower().replace("_", "-")
    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command = spec.get("command")
        if not command:
            raise ValueError("stdio MCP server needs command")
        params = StdioServerParameters(
            command=_expand(command),
            args=[_expand(arg) for arg in spec.get("args", [])],
            env=_env(spec),
            cwd=_expand(spec["cwd"]) if spec.get("cwd") else None,
        )
        async with stdio_client(params) as streams:
            yield streams
        return

    if transport in ("sse", "http-sse"):
        from mcp.client.sse import sse_client

        url = spec.get("url")
        if not url:
            raise ValueError("sse MCP server needs url")
        async with sse_client(
            _expand(url),
            headers=_headers(spec),
            timeout=_timeout(spec, "connectTimeoutSeconds", 30.0),
            sse_read_timeout=_timeout(spec, "readTimeoutSeconds", 300.0),
        ) as streams:
            yield streams
        return

    if transport in ("streamable-http", "http"):
        from mcp.client.streamable_http import streamablehttp_client

        url = spec.get("url")
        if not url:
            raise ValueError("streamable-http MCP server needs url")
        async with streamablehttp_client(
            _expand(url),
            headers=_headers(spec),
            timeout=_timeout(spec, "connectTimeoutSeconds", 30.0),
            sse_read_timeout=_timeout(spec, "readTimeoutSeconds", 300.0),
        ) as (read_stream, write_stream, _session_id):
            yield read_stream, write_stream
        return

    raise ValueError(f"unsupported MCP transport {transport!r}")


async def _with_session(server_name, operation):
    import anyio
    from mcp import ClientSession

    spec = _server(server_name)
    read_timeout = _timeout(spec, "readTimeoutSeconds", 30.0)
    total_timeout = _timeout(spec, "totalTimeoutSeconds", max(60.0, read_timeout + 10.0))
    with anyio.fail_after(total_timeout):
        async with _streams(spec) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=read_timeout),
            ) as session:
                await session.initialize()
                return await operation(session, spec)


def _run(operation, server_name):
    import anyio

    return anyio.run(_with_session, str(server_name), operation)


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _atlas_payload(result):
    """Recover the structured Atlas result without trusting rendered output."""

    value = _model_dump(result)
    if (
        not isinstance(value, dict)
        or value.get("isError") is True
        or value.get("is_error") is True
    ):
        return None
    for key in ("structuredContent", "structured_content"):
        structured = value.get(key)
        if isinstance(structured, dict):
            return structured
    if "receipt" in value:
        return value
    content = value.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        item = _model_dump(item)
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            parsed = json.loads(str(item.get("text", "")))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def servers():
    try:
        config = _load_config()
        return _json(
            {
                "servers": [
                    {
                        "name": name,
                        "type": str(spec.get("type", "stdio")),
                    }
                    for name, spec in sorted(config.items())
                    if isinstance(spec, dict)
                ]
            }
        )
    except Exception as exc:
        return _error(exc)


def tools(server_name):
    async def run(session, _spec):
        result = await session.list_tools()
        return {
            "server": str(server_name),
            "tools": [
                {
                    "name": tool.name,
                    "description": (tool.description or "").splitlines()[0][:300],
                }
                for tool in result.tools
            ],
        }

    try:
        return _json(_run(run, server_name))
    except Exception as exc:
        return _error(exc)


def tool_info(server_name, tool_name):
    async def run(session, _spec):
        result = await session.list_tools()
        for tool in result.tools:
            if tool.name == str(tool_name):
                return _model_dump(tool)
        available = [tool.name for tool in result.tools]
        return {"error": f"unknown tool {tool_name!r}", "available": available}

    try:
        return _json(_run(run, server_name))
    except Exception as exc:
        return _error(exc)


def call_tool(server_name, tool_name, arguments_json="{}"):
    try:
        arguments = json.loads(str(arguments_json or "{}"))
    except json.JSONDecodeError as exc:
        return _error(f"arguments must be a JSON object: {exc}")
    if not isinstance(arguments, dict):
        return _error("arguments must be a JSON object")

    async def run(session, spec):
        read_timeout = _timeout(spec, "readTimeoutSeconds", 30.0)
        result = await session.call_tool(
            str(tool_name),
            arguments,
            read_timeout_seconds=timedelta(seconds=read_timeout),
        )
        return _model_dump(result)

    try:
        result = _run(run, server_name)
        if str(tool_name) in {"atlas_query", "atlas_revise"}:
            payload = _atlas_payload(result)
            if payload is not None:
                try:
                    import effect_receipts

                    effect_receipts.record_atlas_result(
                        server_name, tool_name, payload
                    )
                except Exception:
                    # The optional receipt projection must not alter the MCP
                    # call's result.  Its absence remains visible next turn.
                    pass
        return _json(result)
    except Exception as exc:
        return _error(exc)
