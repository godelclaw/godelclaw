"""Separate provider admission from unresolved command effects.

Observation declarations are trusted host contracts, not deductions from a
tool's name or schema. Undeclared operations conservatively count as effects.
An observation can fail or incur resource cost; its declared contract permits
fresh observations while another command's outcome is unknown. This does not
establish freshness of old observations. No operation is automatically replayed.
"""

import json


DENIAL_CODES = frozenset({"request-not-selectable", "cognition-paused",
    "provider-in-flight", "allowance-exhausted", "admission-cooldown",
    "strategy-changed", "goal-not-selectable"})


class AdmissionDenied(RuntimeError):
    def __init__(self, code):
        self.reason_code = code if code in DENIAL_CODES else "strategy-changed"
        super().__init__(self.reason_code)


def failure_code(error):
    # Never log arbitrary exception messages or user-defined attributes.
    if isinstance(error, AdmissionDenied) and error.reason_code in DENIAL_CODES:
        return error.reason_code
    return type(error).__name__


# These inspect retained state or return information. Keep scheduling/goal
# writes, arbitrary shell/MeTTa/MCP code and outgoing messages outside this set.
OBSERVATION_TOOLS = frozenset({
    "help", "check", "vitals", "jobs", "job-output", "gateway-tools",
    "gateway-status", "llm-quota", "llm-models", "energy", "modes", "mode",
    "tmux-windows", "tmux-peek", "requests", "request-status", "request-page",
    "request-read", "command-receipts", "topics", "model-jobs", "model-result",
    "read-file", "read-lines", "ls-tree", "grep-files", "recent-messages",
    "query", "search", "search-tavily", "search-ddg", "sibling-status",
    "mcp-servers", "mcp-tools", "mcp-tool-info", "iter-transformations",
    "mailbox-status", "mailbox-read",
})


def admitted_prefix(proposal):
    """Recover only the admitted prefix; deferred suffixes carry no authority."""
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except (TypeError, ValueError):
            return None
    if isinstance(proposal, dict):
        commands, limit = proposal.get("commands"), proposal.get("limit")
        if (not isinstance(commands, (list, tuple)) or not commands or
                type(limit) is not int or limit <= 0):
            return None
        commands = commands[:limit]
    elif isinstance(proposal, (list, tuple)) and proposal:
        commands = [proposal]
    else:
        return None
    if any(not isinstance(c, (list, tuple)) or not c or
           not isinstance(c[0], str) for c in commands):
        return None
    return commands


def observational(proposal):
    commands = admitted_prefix(proposal)
    return commands is not None and all(c[0] in OBSERVATION_TOOLS for c in commands)


def outstanding(row):
    return (row["status"] in ("issued", "uncertain") or
            (row["receipt"] is not None and not row["observed"]))


def effect_blockers(rows, exclude=None):
    return [r["ticket"] for r in rows if r["ticket"] != exclude and
            outstanding(r) and not observational(r["command"])]


def may_issue(rows, proposal, exclude=None):
    return observational(proposal) or not effect_blockers(rows, exclude)


def thought_denial(*, available, paused, pending, allowance, cooldown, now):
    """Unfinished commands are intentionally absent from this decision."""
    if not available:
        return "request-not-selectable"
    if paused:
        return "cognition-paused"
    if pending is not None:
        return "provider-in-flight"
    if allowance is not None and allowance <= 0:
        return "allowance-exhausted"
    if now < cooldown:
        return "admission-cooldown"
    return None
