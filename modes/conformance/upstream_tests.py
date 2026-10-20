"""Run only the pinned core's existing parsing and utility/skill tests."""
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from verify import MODES, module


def parsing_tests():
    root = MODES / "omega/upstream"
    sys.path.insert(0, str(root))
    # This particular upstream test file uses only @pytest.fixture, with plain
    # assertion functions accepting one helper. Execute those unchanged bodies
    # directly; installing/upgrading pytest is unnecessary for this core check.
    class Mark:
        @staticmethod
        def parametrize(names, values):
            names = [name.strip() for name in names.split(",")]
            def decorate(function):
                function.__upstream_parameters__ = (names, list(values))
                return function
            return decorate

    existing = sys.modules.get("pytest")
    sys.modules["pytest"] = SimpleNamespace(
        fixture=lambda function: function, mark=Mark())
    try:
        tests = module("omega_upstream_parsing_tests", root / "Autotests/unit/test_helper_parsing.py")
        count = 0
        for name, function in inspect.getmembers(tests, inspect.isfunction):
            if name.startswith("test_"):
                parameters = list(inspect.signature(function).parameters)
                assert parameters and parameters[0] == "helper"
                cases = getattr(function, "__upstream_parameters__", None)
                if cases is None:
                    assert parameters == ["helper"]
                    function(tests.helper())
                    count += 1
                    continue
                names, values = cases
                assert parameters == ["helper", *names]
                for value in values:
                    arguments = value if isinstance(value, tuple) else (value,)
                    function(tests.helper(), *arguments)
                    count += 1
        return count
    finally:
        if existing is None:
            del sys.modules["pytest"]
        else:
            sys.modules["pytest"] = existing
        sys.path.remove(str(root))


def core_tests(args):
    results = []
    for test in ("src_utils", "src_skills", "legacy_input_demand"):
        for engine in ("reference", "cetta"):
            root = Path(tempfile.mkdtemp(prefix="omega-core-test-", dir=args.output))
            module("omega_offline_tests", MODES / "omega/offline.py").prepare(root, {
                "iterations": 1, "events": [], "replies": [], "config": {}},
                native_fs=engine == "cetta")
            runtime = root / "repos/Omega"
            if test == "legacy_input_demand":
                (runtime / "tests/legacy_input_demand.metta").write_bytes(
                    (MODES / "conformance/runtime-repros/legacy-input-demand.metta").read_bytes())
            # Existing skill test's Telegram prompt fixture is a host file.
            (runtime / "memory/tg_prompt.txt").write_text("TELEGRAM ROUTING: fixture")
            entry = root / "test.metta"
            entry.write_text(f'''!(import! &self (library lib_import))
!(git-import! "https://github.com/singnet/Omega.git" "" "{root}/repos")
!(import! &self "{root}/fixture_host.py")
!(import! &self "{root}/host.metta")
!(import! &self "{runtime}/tests/{test}.metta")
''')
            env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "PYTHONHOME": str(args.python.parent.parent),
                   "LD_LIBRARY_PATH": str(args.python.parent.parent / "lib"),
                   "PYTHONPATH": str(root), "OMEGA_FIXTURE_ROOT": str(root),
                   "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
            cmd = ([str(args.cetta), "--lang", "petta", str(entry)] if engine == "cetta" else
                   ["sh", str(args.petta / "run.sh"), str(entry), "--silent"])
            result = subprocess.run(cmd, cwd=runtime, env=env, text=True, capture_output=True, timeout=30)
            (root / "stdout.log").write_text(result.stdout)
            (root / "stderr.log").write_text(result.stderr)
            results.append({"test": test, "engine": engine, "passed": result.returncode == 0 and "❌" not in result.stdout,
                            "directory": str(root), "exit": result.returncode,
                            "legacy_semantics_adapter": engine == "cetta",
                            "upstream_test": test != "legacy_input_demand"})
        # Original test files are unchanged; adapted source forms are recorded
        # individually in legacy-semantics-adapter.json. Also compare
        # their complete observable output, so a silent missing result is not
        # accepted merely because both processes exited zero.
        left, right = results[-2:]
        a = (Path(left["directory"]) / "stdout.log").read_text().replace(left["directory"], "$ROOT")
        b = (Path(right["directory"]) / "stdout.log").read_text().replace(right["directory"], "$ROOT")
        left["paired_output_equal"] = right["paired_output_equal"] = a == b
        left["passed"] &= a == b
        right["passed"] &= a == b
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    for field in ("cetta", "petta", "python", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"parsing_tests_passed": parsing_tests(), "core_tests": core_tests(args)}
    (args.output / "upstream-tests.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    sys.exit(any(not row["passed"] for row in result["core_tests"]))
