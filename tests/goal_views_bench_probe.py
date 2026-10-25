"""Use the accepted independent cold-cache witnesses for project 04."""
import json
from pathlib import Path

import goal_views_probe as goals
import small_views_cold_probe as cold


def configure(path, native):
    cold.settings = json.loads(Path(path).read_text())
    cold.settings["compatibility_only"] = False
    cold.base.apply(path)
    goals.retained_views = cold.settings["retained_views"]
    return goals.setup(native, 1)


def profile(path, label, start, end, value):
    assert cold.base.forbidden_calls == 0
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"label": str(label), "start_ns": int(start), "end_ns": int(end),
            "ms": (int(end) - int(start)) / 1e6, "value": str(value)}) + "\n")
    return 1
