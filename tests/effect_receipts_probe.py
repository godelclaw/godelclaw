"""Generated-fixture observations, not a runtime persistence/cleaning implementation."""
import json
from pathlib import Path


def crossing(path, label, *values):
    row = {"label": label, "values": [{"type": type(v).__module__ + "." + type(v).__qualname__,
                                      "str": str(v), "repr": repr(v)} for v in values]}
    with Path(path).open("a") as stream:
        stream.write(json.dumps(row, ensure_ascii=True) + "\n")
    return 1


def surrogate_wire():
    return json.dumps("\ud800", ensure_ascii=True)
