"""Cold-caller fixture instrumentation, never a runtime implementation."""
import json
import os
from pathlib import Path
import time

import small_views_probe as base

settings = {}
pending = None
previous_end = None


def loaders(conversation=None, views=None):
    result = []
    for spec, loader in base.original_loaders(conversation, views):
        if spec.source_id in settings["retained_views"]:
            loader = lambda source=spec.source_id: settings["retained_views"][source]
        result.append((spec, loader))
    return result


def configure(path, native):
    global settings
    settings = json.loads(Path(path).read_text())
    base.apply(path)
    base.retained_loaders = loaders
    return base.setup(native, 1)


def git_events(lines):
    """Pair real wrapper invocations with exits; reject incomplete evidence."""
    events = []
    for line in lines:
        fields = line.split("\t")
        if fields[0] == "start":
            if events and "returncode" not in events[-1]:
                raise AssertionError("overlapping/incomplete Git probe")
            events.append({"pid": fields[1], "argv": fields[2:]})
        elif fields[0] == "end":
            if not events or events[-1]["pid"] != fields[1] or "returncode" in events[-1]:
                raise AssertionError("unmatched Git exit")
            events[-1]["returncode"] = int(fields[2])
        else:
            raise AssertionError("unknown Git trace entry")
    if any("returncode" not in event for event in events):
        raise AssertionError("missing Git completion")
    return events


def trace_lines():
    trace = Path(os.environ["SMALL_VIEWS_GIT_TRACE"])
    return trace.read_text().splitlines() if trace.exists() else []


def begin(workload, sample, wait_seconds):
    global pending
    assert pending is None
    start = time.monotonic_ns()
    if workload == "cold":
        assert wait_seconds > 2
        time.sleep(wait_seconds)  # Required cache-expiry wait, OUTSIDE the timer.
    ready = time.monotonic_ns()
    if workload == "cold":
        assert ready - start > 2_000_000_000
        assert previous_end is not None and ready - previous_end > 2_000_000_000
    pending = {"workload": workload, "sample": sample, "wait_start_ns": start,
               "ready_ns": ready, "wait_elapsed_ns": ready - start,
               "previous_call_end_ns": previous_end, "trace_offset": len(trace_lines()),
               "calls_before": base.calls.copy(), "boundaries_before": base.boundary_calls.copy(),
               "capability_events_before": len(base.probe_events())}
    return 1


def record(path, start, end, text):
    global pending, previous_end
    assert pending is not None
    start, end = int(start), int(end)
    row = pending.copy()
    pending = None
    assert start >= row["ready_ns"] and end >= start
    if row["workload"] == "cold":
        assert start - row["previous_call_end_ns"] > 2_000_000_000
    events = git_events(trace_lines()[row["trace_offset"]:])
    for event in events:
        assert event["pid"].isdigit()
        outputs = Path(os.environ["SMALL_VIEWS_GIT_OUTPUTS"])
        event["stdout"] = (outputs / (event["pid"] + ".stdout")).read_text()
        event["stderr"] = (outputs / (event["pid"] + ".stderr")).read_text()
    expected = settings["git_probe_argv"] if row["workload"] in {"prime", "cold"} else []
    assert [event["argv"] for event in events] == expected, events
    assert all(event["returncode"] == 0 for event in events), events
    rendered = str(text)
    assert rendered.count("SOURCE[") == 13 and "SOURCE[context-bundle]" not in rendered
    assert "SOURCE[project-evidence] status=known" in rendered
    assert base.forbidden_calls == 0
    delta = {key: value - row["calls_before"].get(key, 0) for key, value in base.calls.items()}
    assert all(value == 1 for value in delta.values()), delta
    row.update(start_ns=start, end_ns=end, ms=(end - start) / 1_000_000,
               text=rendered, git_probes=events, git_probe_count=len(events),
               evidence_refresh_count=len(events) // 12, loader_calls=delta,
               forbidden=base.forbidden_calls,
               boundaries={key: value - row["boundaries_before"].get(key, 0)
                           for key, value in base.boundary_calls.items()},
               capability_probe_count=len(base.probe_events()) - row["capability_events_before"])
    previous_end = end
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=True) + "\n")
    return 1
