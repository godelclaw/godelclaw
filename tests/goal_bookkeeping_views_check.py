"""Affected project-04 view checks through project-04b's isolated harness.

No live-style Chroma/channel boot. Explicit hand-corrupted JSON diagnostics
are separate from mandatory parity (no skips/expectedFailure counted green).
Actual branch boot/loop/skills are qualified by the bookkeeping caller suite.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

from goal_bookkeeping_support import ROOT, engine, environment, receipt, run
import test_goal_views_native as views


def view_environment(directory, engine="cetta", **settings):
    env = environment(directory)
    env.update(METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine)
    env.update({k: v for k, v in settings.items() if v is not None})
    for key, value in settings.items():
        if value is None:
            env.pop(key, None)
    return env


def guest(directory, lines, engine="cetta", swi=False, boot=False, **settings):
    assert not boot, "full initialization is reviewer-owned; use real bookkeeping boot fixtures"
    program = "\n".join(lines) + "\n"
    if not swi and "goal_views_probe.python_" not in program:
        program = program.replace("!(py-call (goal_views_probe.setup 1 0))",
            "!(py-call (goal_views_probe.setup 1 0))\n"
            "!(import! &self ./tests/goal_bookkeeping_probe.py)\n"
            "!(py-call (goal_bookkeeping_probe.disable_writers))")
        program = program.replace("!(py-call (goal_views_probe.setup 1 1))",
            "!(py-call (goal_views_probe.setup 1 1))\n"
            "!(import! &self ./tests/goal_bookkeeping_probe.py)\n"
            "!(py-call (goal_bookkeeping_probe.disable_writers))")
    extra = view_environment(directory, "petta" if swi else "cetta", **settings)
    # engine() uses its own environment builder: apply only explicit settings
    # to the parent briefly, preserving its selected frozen binary.
    from unittest import mock
    with mock.patch.dict(os.environ, extra, clear=True):
        if not swi:
            from goal_bookkeeping_support import engine as native
            result = native(directory, program, extra_env={k: v for k, v in settings.items() if v is not None})
        else:
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
                source.write(program)
                source.flush()
                receipt("swi-view-program", {"program": program})
                result = run(["bash", str(Path(os.environ["PETTA_ROOT"]) / "run.sh"), source.name, "default"],
                             directory, extra_env=extra)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AssertionError" not in result.stdout and "uncaught" not in result.stderr
    return result


def main():
    views.guest, views.guest_env = guest, view_environment
    views.save_evidence = receipt
    excluded = {name for name in unittest.defaultTestLoader.getTestCaseNames(views.GoalViewsTest)
                if name.startswith("test_capability_gap_") or name == "test_direct_production_boot_order"}
    names = [name for name in unittest.defaultTestLoader.getTestCaseNames(views.GoalViewsTest) if name not in excluded]
    receipt("affected-view-scope", {"selected": names, "outside_scope": sorted(excluded),
        "reason": "hand-corrupted JSON diagnostics separate; no live-style initializer; actual branch boot in callers suite"})
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(views.GoalViewsTest(name) for name in names))
    assert not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
