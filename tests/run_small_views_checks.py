"""Project-03 isolated qualification; retain full outputs and real exit codes.

Run with the matching Python and CETTA_BIN, --evidence outside this checkout.
The inherited runner strips credentials, uses isolated_environ, blocks tmux,
and enforces ulimit -v 25165824. Never run the tmux shadow here.
"""
import run_context_bundle_checks as runner

runner.SUITES = {
    "native": ["-m", "unittest", "-v", "test_small_views_native"],
    "regression": ["-m", "unittest", "-v", "test_project_capabilities", "test_project_evidence",
                   "test_commitment_projection", "test_task_phase", "test_context_sources",
                   "test_turn_additions", "test_pins_context_native", "test_action_window_native"],
    "bundle": ["-m", "unittest", "-v", "test_context_bundle_native"],
    "bench": ["tests/small_views_bench.py"],
}

if __name__ == "__main__":
    raise SystemExit(runner.main())
