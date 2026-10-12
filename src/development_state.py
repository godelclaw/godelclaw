"""Read-only projection of the agent's active Iter programs.

File tools write directly, so there are no pending source proposals to show.
"""

from __future__ import annotations

import json


def _iter_programs() -> dict:
    try:
        import iter_authoring

        observed = json.loads(iter_authoring.list_transformations())
        if not isinstance(observed, dict):
            raise ValueError("Iter inventory is not an object")
        return observed
    except Exception as error:
        return {
            "state": "unavailable",
            "error_kind": type(error).__name__,
        }


def view() -> str:
    """Render one deterministic development snapshot for model context."""

    return json.dumps(
        {
            "schema": 2,
            "iter_transformations": _iter_programs(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
