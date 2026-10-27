"""Generate Lean checks from observed control/action traces, not synthetic runs.

Host parsing, provider payloads and formatted history remain opaque. The
reference/CeTTa differential checks those separately; this replay checks the
explicitly modeled control boundary against both sets of recorded observations.
"""
import argparse
import json
from pathlib import Path


def lit(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"({value})" if value < 0 else str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(lit(x) for x in value) + "]"
    raise TypeError(value)


def sexpr(text):
    decoder = json.JSONDecoder(strict=False)
    def read(i):
        while i < len(text) and text[i].isspace():
            i += 1
        if text[i] == "(":
            result = []
            i += 1
            while True:
                while text[i].isspace():
                    i += 1
                if text[i] == ")":
                    return result, i + 1
                value, i = read(i)
                result.append(value)
        if text[i] == '"':
            value, length = decoder.raw_decode(text[i:])
            return value, i + length
        end = i
        while end < len(text) and text[end] not in "() \n\t\r":
            end += 1
        return text[i:end], end
    return read(0)[0]


def omega_state(row, history):
    wake = "none" if row.get("wake") is None else f"(some {lit(row['wake'])})"
    return f"(OmegaArchitecture.State.mk {lit(row['loops'])} {lit(row['previous'])} {lit(row['last_results'])} {lit(history)} {wake})"


def omega_replay(rows, scenario):
    cfg = scenario.get("config", {})
    config = f"(OmegaArchitecture.Config.mk {cfg.get('maxNewInputLoops', 50)} {cfg.get('maxWakeLoops', 1)} {cfg.get('wakeupInterval', 600)} {cfg.get('maxOutputToken', 6000)} {lit(cfg.get('reasoningMode', 'medium'))})"
    prior = {"loops": 0, "previous": "", "last_results": [], "wake": None}
    history = [scenario["history"]] if scenario.get("history") else []
    persisted = scenario.get("history", "")
    checks = []
    starts = [i for i, row in enumerate(rows) if row["kind"] == "heartbeat"]
    for position, begin in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(rows)
        segment = rows[begin:end]
        heartbeat = rows[begin]
        event = next(r["event"] for r in segment if r["kind"] == "receive")
        request = next((r for r in segment if r["kind"] == "request"), None)
        failure = next((r for r in segment if r["kind"] == "provider_error"), None)
        boundary = next((r for r in segment if r["kind"] == "boundary"), None)
        state = omega_state(prior, history)
        wire = request["request"] if request else {}
        messages = wire.get("messages", [])
        context = messages[0].get("content", "") if messages else ""
        message = messages[-1].get("content", "") if messages else ""
        tools = request.get("tools", []) if request else []
        args = (f"{config} {heartbeat['iteration']} {state} {lit(event)} "
                f"{lit(context)} {lit(message)} {lit(tools)} {heartbeat['now']}")
        if failure:
            commands = []
        assert boundary is not None
        entry = boundary["history"][len(persisted):]
        assert boundary["history"].startswith(persisted)
        commands = []
        if request and not failure:
            response_log = next(r["message"] for r in segment
                if r["kind"] == "log" and r["message"].startswith("(RESPONSE:")
                and "LLMResponse[" not in r["message"]
                and not r["message"].startswith("(RESPONSE: (RESULTS:"))
            parsed = sexpr(response_log)[1]
            if parsed and parsed[0] == "Error":
                response = f"(.responseError {lit(str(parsed))} {lit(boundary['last_results'])})"
            else:
                commands = [[str(item[0]), str(item[1][0])] for item in parsed]
                calls = "[" + ", ".join(
                    f"(OmegaArchitecture.Call.mk {lit(ident)} {lit(name)})"
                    for ident, name in commands) + "]"
                response = f"(.calls {calls} \"\" {lit(boundary['last_results'])})"
        else:
            response = (f"(.providerFailure {lit(failure['error'])} {lit(boundary['last_results'])})"
                        if failure else f"(.calls [] \"\" {lit(boundary['last_results'])})")
        expression = f"(OmegaArchitecture.cycle {args} {response} {lit(entry)})"
        if entry:
            history = history + [entry]
        expected = omega_state(boundary, history)
        checks += [f"example : {expression}.state = {expected} := by decide",
                   f"example : (OmegaArchitecture.dispatches {expression}.actions).map OmegaArchitecture.Call.id = {lit([item[0] for item in commands])} := by decide"]
        if request:
            novel = event != "" and event != ("" if heartbeat["iteration"] == 1 else prior["previous"])
            expected_stimulus = (boundary["previous"] if novel else
                "[NO NEW USER INPUT. CONTINUE AUTONOMOUS WORK. DO NOT REPEAT THE PREVIOUS RESPONSE. ONLY USE send FOR GENUINELY NEW INFORMATION OR WHEN USER INPUT IS NEEDED.]")
            assert message == expected_stimulus, (message, expected_stimulus)
            assert wire["max_tokens"] == cfg.get("maxOutputToken", 6000)
            assert wire["reasoning_effort"] == cfg.get("reasoningMode", "medium")
        prior, persisted = boundary, boundary["history"]
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = ["import OmegaArchitecture", "set_option maxRecDepth 10000"]
    count = 0
    for base in args.traces:
        for root in sorted(base.iterdir()):
            identity = root / "identity.json"
            if not identity.exists():
                continue
            info = json.loads(identity.read_text())
            # Semantic mutants are checked by the differential harness, not replayed
            # as if they were traces of the intended implementation.
            if info["scenario"] in {"duplicate-rearms", "inclusive-wake", "no-decrement"}:
                continue
            rows = [json.loads(line) for line in (root / "trace.jsonl").read_text().splitlines()]
            source.append("-- " + json.dumps(info))
            checks = omega_replay(rows, json.loads((root / "scenario.json").read_text()))
            source.extend(checks)
            count += len(checks)
    args.output.write_text("\n\n".join(source) + "\n")
    print(f"{count} observed control/action checks: {args.output}")


if __name__ == "__main__":
    main()
