"""Project06 read-only capability checkpoint, NOT runtime qualification.

Select CETTA_BIN and the matching Python explicitly. Optional immutable pins:
MODE_STATE_ENGINE_SHA256 and MODE_STATE_MANIFEST_SHA256. Evidence directories
are append-only. No implementation, activation, Python fallback or home resolver
is installed by this checkpoint.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, ROOT, limit, q
from mode_state_support import Qualification


def oracle(c, name, source, *arguments):
    path = c.directory / (name + ".py")
    path.write_text(source, encoding="utf-8")
    (c.evidence / path.name).write_bytes(path.read_bytes())
    env = c.environment()
    command = [sys.executable, str(path), *map(str, arguments)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            timeout=30, preexec_fn=limit)
    c.record_process(name, command, result, env, cwd=ROOT)
    assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
    return json.loads(result.stdout)


def checkpoint(c, args):
    c.snapshot_sources("tests/mode_state_capability_checkpoint.py", "tests/mode_state_support.py",
                       "tests/test_mode_state_support.py",
                       "tests/mode_state_bootstrap_probe.metta", "tests/effect_receipts_bundle.py",
                       "tests/effect_receipts_capability_checkpoint.py", "tests/effect_receipts_support.py",
                       "tests/isolated_env.py", "src/engine_modes.py", "src/loop_modes.py",
                       "src/lifecycle.py", "src/attention.py", "src/fuel_modes.py", "src/fuel_cetta.metta")
    d = c.directory
    with c.phase("required-primitives"):
        (d / "invalid").write_bytes(b"\xff\r\ncetta\r")
        (d / "newline").write_bytes(b"cetta\rpetta\r\nomega\n")
        program = IMPORTS + f'''
!(println! (WriteSequence
 (let* (($owner (fs:open-write {q(d / 'temporary')} ("create" "exclusive") 384))
        ($write (fs:write-all $owner "hello\\n")) ($sync (fs:sync $owner))
        ($mode (fs:set-mode $owner 416)) ($close (fs:close $owner))
        ($rename (fs:rename {q(d / 'temporary')} {q(d / 'published')})))
   ($write $sync $mode $close $rename))))
!(println! (Access (fs:access {q(d / 'published')} ("read"))))
!(println! (ProbeZero (system:signal-probe 0)))
!(println! (ProbeCurrent (system:signal-probe (system:pid))))
!(println! (ProbeOverflow (catch (system:signal-probe 2147483648))))
!(println! (PermissionErrno (system:errno-value "EPERM")))
!(println! (MissingErrno (system:errno-value "NOT_AN_ERRNO")))
!(println! (InvalidUtf8 (str:utf8-error (fs:read-text {q(d / 'invalid')}))))
!(println! (RawNewlines (fs:read-text {q(d / 'newline')})))
!(println! (Clock (system:wall-time)))
!(println! (Fixed (str:format-fixed 123.4567895 6)))
!(println! (Indented (json:stringify-python-with
 (json:parse-python "{{\\"z\\": 1, \\"a\\": 2}}") ((ensure-ascii False) (indent 2)))))
'''
        before = time.time()
        output = c.run("supported-apis", program)
        after = time.time()
        assert "(WriteSequence ((fs:written 6) fs:synced fs:mode-set fs:closed ()))" in output, output
        assert "(Access true)" in output and "(ProbeZero true)" in output, output
        assert "(ProbeCurrent true)" in output and "expected a process ID within pid_t" in output, output
        assert "(MissingErrno ())" in output and "(InvalidUtf8 (StrUtf8ErrorV1 " in output, output
        clock = float(re.search(r"\(Clock ([^)]+)\)", output)[1])
        assert before <= clock <= after, (before, clock, after)
        fixed = json.loads(re.search(r"\(Fixed (.+)\)", output)[1])
        assert fixed == "%.6f" % 123.4567895, fixed
        # PeTTa's display quotes a string but leaves its LF characters literal.
        # This decoder is for that test transcript, NOT a runtime JSON loader.
        indent = json.loads(re.search(r"\(Indented (\"(?:\\.|[^\"\\])*\")\)", output, re.S)[1], strict=False)
        assert indent == json.dumps({"z": 1, "a": 2}, ensure_ascii=False, indent=2), indent
        path = d / "published"
        effect = {"hex": path.read_bytes().hex(), "mode": oct(path.stat().st_mode & 0o777),
                  "temporary_exists": (d / "temporary").exists(), "clock_bracket": [before, clock, after]}
        c.save("primitive-effects.json", effect)
        assert effect["hex"] == b"hello\n".hex() and effect["mode"] == "0o640", effect
        assert not effect["temporary_exists"], effect
        output = c.run("remove-api", IMPORTS + f'''
!(println! (Remove (fs:remove {q(path)})))
!(println! (RemoveMissing (fs:remove {q(path)})))
''')
        assert "(Remove ())" in output and "(RemoveMissing (fs:failed fs:missing remove " in output, output
        assert not path.exists()
        c.save("remove-effects.json", {"published_exists": path.exists()})

    with c.phase("home-dependency"):
        inventory = []
        for path in sorted((c.bundle / "lib").rglob("*.metta")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"(?i)\bhome\b|expand.?user|getpwuid|getuid|passwd|home-directory|user-home", line):
                    inventory.append([str(path.relative_to(c.bundle)), number, line])
        c.save("native-home-api-inventory.json", inventory)
        # This deliberate HOME-unset process calls ONLY the library primitives
        # below, never a runtime initializer, state reader, or writer. All other
        # settings remain generated paths. No account home is used as a path.
        c.unset_environment = ("HOME",)
        try:
            c.resolution("home-dependency", "HOME-UNSET")
            output = c.run("home-native-probe", IMPORTS + '''
!(println! (HomeEnvironment (system:getenv-or-default "HOME" "ABSENT_FIXTURE_SENTINEL")))
!(println! (HomeCandidate (system:home-directory)))
!(println! (ExpandCandidate (path:expanduser "~")))
!(println! (NormalizeTilde (path:normalize "~/.local/state")))
''')
        finally:
            c.unset_environment = ()
        assert '(HomeEnvironment "ABSENT_FIXTURE_SENTINEL")' in output, output
        assert "(HomeCandidate (system:home-directory))" in output, output
        assert '(ExpandCandidate (path:expanduser "~"))' in output, output
        assert '(NormalizeTilde "~/.local/state")' in output, output
        # The negative observations above are NOT capability success. The
        # declaration inventory corroborates only that these native libraries
        # expose no named account-home resolver, not theoretical inexpressibility
        # through arbitrary new foreign predicates or a future engine API.
        account = d / "fixture-account-home"
        value = oracle(c, "oracle-home-unset-fixture", '''import json, os, sys
from types import SimpleNamespace
from unittest.mock import patch
import engine_modes, loop_modes, lifecycle
account = sys.argv[1]
# NSS/account lookup is an EXPLICIT fixture injection, NOT an OS witness.
# No function here reads or writes state. All computed paths are disposable.
for key in ("HOME", "XDG_STATE_HOME", "PETTA_ROOT", "CETTA_ROOT", "CETTA_BIN",
            "METTACLAW_ENGINE_STATE_PATH", "METTACLAW_LIFECYCLE_PATH", "METTACLAW_INSTANCE"):
    os.environ.pop(key, None)
with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=account)) as lookup:
    value = {"home": os.path.expanduser("~"), "petta": engine_modes.engine_command("swi"),
             "cetta": engine_modes.engine_command("cetta"), "engine": engine_modes._state_path(),
             "active_loop": loop_modes._active_path(), "lifecycle": str(lifecycle._path())}
    value["account_lookups"] = lookup.call_count
print(json.dumps(value, sort_keys=True))
''', account)
        expected = {"home": str(account), "petta": [str(account / "repos/PeTTa/run.sh")],
                    "cetta": [str(account / "repos/CeTTa-runtime/cetta")],
                    "engine": str(account / ".local/state/default/engine"),
                    "active_loop": str(account / ".local/state/default/active-loop.json"),
                    "lifecycle": str(account / ".local/state/pettaclaw-godel/lifecycle.json")}
        assert value.pop("account_lookups") > 0
        assert value == expected, (value, expected)
        c.save("home-dependency-result.json", {"oracle": value, "expected": expected,
            "account_lookup": "explicit generated NSS fixture, not native account lookup parity",
            "native_candidates": "unreduced; NOT a successful gate",
            "request": "native expanduser/account-home semantics or explicit HOME-present domain decision"})

    with c.phase("bootstrap-probe"):
        program = '!(import! &self ./tests/mode_state_bootstrap_probe.metta)\n!(println! (Lane (mode-bootstrap:cetta?)))\n'
        output = c.run("bootstrap-native", program)
        assert "(Lane true)" in output, output
        if args.swipl and args.swi_root:
            source = d / "swi-bootstrap.metta"
            source.write_text(program.replace("./tests/", str(ROOT / "tests") + "/"))
            env = c.environment()
            env["METTACLAW_ACTIVE_ENGINE"] = "petta"
            command = [str(args.swipl), "--stack_limit=1g", "-q", "-s",
                       str(args.swi_root / "src/main.pl"), "--", str(source)]
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    timeout=30, preexec_fn=limit)
            c.record_process("swi-bootstrap", command, result, env, cwd=ROOT)
            assert result.returncode == 0 and b"(Lane false)" in result.stdout, result.stderr
        else:
            c.save("swi-not-run.json", {"reason": "both --swipl and --swi-root are required"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--swipl", type=Path)
    parser.add_argument("--swi-root", type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="mode06-checkpoint-") as temporary:
        c = Qualification(args.evidence.resolve(), Path(temporary))
        checkpoint(c, args)
        c.save("checkpoint.json", {"status": "BLOCKED_NATIVE_HOME_CAPABILITY_DECISION",
            "runtime_changed": False, "python_oracles_changed": False,
            "not_qualified": ["mode/fuel/lifecycle implementation", "caller replacement", "mixed routes",
                              "fault parity", "Lean compilation/replay", "turn costs", "prior runtime regressions"],
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print("BLOCKED_NATIVE_HOME_CAPABILITY_DECISION: checkpoint only, no runtime activation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
