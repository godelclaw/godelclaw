"""Environments for the processes that tests start.

A test run from an agent's own shell inherits that agent's METTACLAW_*
settings: its engine file, recycle and wake requests, health file, ledger,
call tag and channel credentials. A runtime that a test starts must never
act on those. Settings a test assigns itself are kept.
"""
import os

# Captured when a test module imports this one, before any test runs.
_INHERITED = {k: v for k, v in os.environ.items() if k.startswith("METTACLAW_")}


def isolated_environ(**overrides):
    """os.environ without the inherited METTACLAW_* settings, plus overrides."""
    env = {k: v for k, v in os.environ.items() if _INHERITED.get(k) != v}
    env.update(overrides)
    return env
