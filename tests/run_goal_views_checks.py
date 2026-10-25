"""Project-04 qualification; isolated children, memory limit and full receipts."""
import run_context_bundle_checks as runner

runner.SUITES = {
    "native": ["-m", "unittest", "-v", "test_goal_views_native"],
    "regression": ["-m", "unittest", "-v", "test_work_state", "test_goals_kernel",
                   "test_context_sources", "test_turn_additions", "test_context_continuity"],
    "replay": ["-m", "unittest", "-v",
               "test_context_bundle_native.NativeContextBundleTest.test_exhaustive_lean_model_and_python_replay"],
    "callers": ["-m", "unittest", "-v", "test_context_bundle_native", "test_small_views_native",
                "test_pins_context_native", "test_action_window_native", "test_small_views_cold_bench"],
    "bench": ["tests/goal_views_bench.py"],
}

if __name__ == "__main__":
    raise SystemExit(runner.main())
