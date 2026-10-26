"""Project05 early capability checkpoint, not a writer or integration test.

Use the reviewer-selected CETTA_BIN and matching Python. No runtime modules are
imported in the native process, no Python bridge is used, and all files are
generated fixtures. Evidence is append-only: select a new output directory.
"""
import argparse
import base64
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time
import unicodedata
from zoneinfo import ZoneInfo

from isolated_env import isolated_environ
from effect_receipts_bundle import ENGINE_PIN, MANIFEST_PIN, assert_same_bundle, bundle_identity


LIMIT = 25165824 * 1024
LIBRARIES = ("lib_import", "fs", "str", "json", "system", "path", "math", "re")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Checkpoint:
    def __init__(self, evidence, directory):
        self.evidence = evidence
        self.directory = directory
        self.binary = Path(os.environ["CETTA_BIN"])
        assert self.binary.is_absolute() and not self.binary.is_symlink()
        self.bundle = self.binary.resolve().parent
        self.index = 0
        self.pins = {key: os.environ.get(key, "") for key in (ENGINE_PIN, MANIFEST_PIN)}
        self._phase_start = None

    def save(self, name, value):
        (self.evidence / name).write_text(
            json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    def identity(self, phase, edge):
        try:
            observed = bundle_identity(self.binary, self.pins)
        except BaseException as exc:
            self.save(phase + "-" + edge + "-rejected.json", {
                "type": type(exc).__name__, "detail": str(exc), "expected_pins": self.pins})
            raise
        observed.update({"phase": phase, "edge": edge,
            "expected_pins": self.pins, "python": sys.version, "python_executable": sys.executable,
            "rlimit_as": resource.getrlimit(resource.RLIMIT_AS),
            "provenance": "bundle-under-test hashes/resolution are observations, not independent build certification"})
        self.save(phase + "-" + edge + ".json", observed)
        print(phase, edge, len(observed["files"]), "manifest identities/membership verified", flush=True)
        return observed

    def environment(self):
        # isolated_environ first, then an allowlist, not an incomplete secrets
        # denylist. No inherited loader, CETTA, root or Python overrides survive.
        inherited = isolated_environ()
        env = {key: inherited[key] for key in ("LANG", "LC_ALL") if key in inherited}
        env.update(self.pins)
        d = self.directory
        env.update(HOME=str(d / "home"), TMPDIR=str(d),
                   XDG_STATE_HOME=str(d / "xdg-state"), XDG_CONFIG_HOME=str(d / "xdg-config"),
                   XDG_CACHE_HOME=str(d / "xdg-cache"),
                   PATH=str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                   CETTA_BIN=str(self.binary), PETTA_PY_ENV=sys.prefix,
                   PETTA_ROOT=str(d / "unused-swi-root"), PYTHONHOME=sys.prefix,
                   LD_LIBRARY_PATH=str(Path(sys.prefix) / "lib"),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", TZ="Europe/Paris",
                   METTACLAW_ENGINE="cetta", METTACLAW_ACTIVE_ENGINE="cetta",
                   METTACLAW_SKIP_INITIALIZE="1", METTACLAW_OBSERVED_STRATEGY="0",
                   METTACLAW_PUBLIC_TOOL_RECEIPTS="0")
        for name, filename in {
            "EFFECT_RECEIPT_PATH": "receipts.jsonl", "PUBLIC_WORK_DIR": "public-work",
            "PUBLIC_WORK_LIB": "unused-public-library", "STREAM_DIR": "stream",
            "HISTORY_PATH": "history.metta", "GOALS_PATH": "goals.metta",
            "PINS_PATH": "pins.txt", "STRATEGY_PATH": "strategy.sqlite3",
            "LLM_LEDGER_PATH": "ledger.db", "ENGINE_STATE_PATH": "engine-state",
            "WORKING_SET_PATH": "working.json", "RECYCLE_REQUEST_PATH": "recycle",
            "WAKE_REQUEST_PATH": "wake", "LIFECYCLE_PATH": "lifecycle.json",
            "CONTROL_PATH": "control.json", "CHANNEL_PATH": "channel.metta",
            "FUEL_PATH": "fuel", "LOG_PATH": "runtime.log",
            "TELEGRAM_LOG_PATH": "updates.jsonl", "TELEGRAM_OUTBOX_PATH": "outbox.jsonl",
            "TELEGRAM_SEQUENCE_PATH": "sequence", "TELEGRAM_HEALTH_PATH": "health.json",
            "TELEGRAM_ATTACHMENTS_DIR": "attachments",
        }.items():
            env["METTACLAW_" + name] = str(d / filename)
        return env

    def run(self, name, program):
        self.index += 1
        stem = "%02d-%s" % (self.index, name)
        source = self.directory / (stem + ".metta")
        source.write_text(program, encoding="utf-8")
        (self.evidence / (stem + ".metta")).write_bytes(source.read_bytes())
        command = [str(self.binary), "--lang", "petta", str(source)]
        result = subprocess.run(command, cwd=self.directory, env=self.environment(),
                                capture_output=True, timeout=30)
        (self.evidence / (stem + ".stdout")).write_bytes(result.stdout)
        (self.evidence / (stem + ".stderr")).write_bytes(result.stderr)
        self.save(stem + ".json", {"command": command, "cwd": str(self.directory),
                                  "environment": self.environment(),
                                  "returncode": result.returncode,
                                  "stdout_base64": base64.b64encode(result.stdout).decode(),
                                  "stderr_base64": base64.b64encode(result.stderr).decode()})
        print(stem, "actual exit", result.returncode, flush=True)
        assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
        return result.stdout.decode("utf-8")

    def resolution(self, phase, edge):
        program = "".join("!(import! &self (library %s))\n" % n for n in LIBRARIES)
        program += "".join("!(println! (FrozenLibrary (library %s)))\n" % n for n in LIBRARIES)
        output = self.run(phase + "-resolution-" + edge, program)
        paths = [p.strip('"') for p in re.findall(r"\(FrozenLibrary ([^\n]+)\)", output)]
        assert len(paths) == len(LIBRARIES), output
        for name, path in zip(LIBRARIES, paths):
            resolved = Path(path + ".metta").resolve()
            assert resolved.is_relative_to(self.bundle / "lib") and resolved.is_file(), path
            assert resolved.stem == name, (name, path)
            relative = resolved.relative_to(self.bundle).as_posix()
            assert self._phase_start is not None, "resolution must be phase-bracketed"
            assert relative in self._phase_start["library_files"], path
            assert digest(resolved) == dict(self._phase_start["files"])[relative], path
        self.save(phase + "-resolution-" + edge + ".json", paths)

    @contextmanager
    def phase(self, name):
        start = self.identity(name, "START")
        self._phase_start = start
        try:
            self.resolution(name, "START")
            yield
        finally:
            try:
                # A coherent self-manifested replacement is still a mismatch.
                # Check before launching even the final descriptor process.
                before_resolution = self.identity(name, "PRE-END-RESOLUTION")
                assert_same_bundle(start, before_resolution)
                self.resolution(name, "END")
            finally:
                end = self.identity(name, "END")
                self._phase_start = None
                assert_same_bundle(start, end)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 12, 13), sys.version
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    (args.evidence / "runner.py").write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix="effect05-capabilities-") as temp:
        checkpoint = Checkpoint(args.evidence.resolve(), Path(temp))
        with checkpoint.phase("capabilities"):
            checkpoint.save("oracle-unicode.json", {
                "python": sys.version, "unidata_version": unicodedata.unidata_version,
                "categories": {"U+%04X" % ord(c): unicodedata.category(c)
                               for c in "\n\x00\u200d\ue000\ud800\u0378\U0001fae8"}})
            # Inventory all frozen source declarations, not merely guessed APIs.
            inventory = []
            for path in sorted((checkpoint.bundle / "lib").rglob("*.metta")):
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if re.search(r"flock|fsync|general.category|unicode.category|uuid|"
                                 r"wall-time|format-time|append-text|fs:rename", line, re.I):
                        inventory.append([str(path.relative_to(checkpoint.bundle)), number, line])
            checkpoint.save("capability-inventory.json", inventory)
            imports = "".join("!(import! &self (library %s))\n" % n for n in LIBRARIES)
            # Proposed names are intentionally not wrappers/workarounds. An
            # unreduced call is negative evidence, not a successful operation.
            missing = {
                "Fsync": '(fs:fsync "fixture")',
                "Flock": '(fs:flock "fixture" exclusive)',
                "LockedAppend": '(fs:append-locked-fsync "fixture" "text")',
                "TransactionLock": '(fs:with-exclusive-lock "lock" inert-body)',
                "Category": '(str:unicode-category "͸")',
                "UnicodeVersion": '(str:unicode-version)',
                "Uuid4": '(system:uuid4)',
            }
            output = checkpoint.run("missing-required-apis", imports + "".join(
                "!(println! (Probe%s %s))\n" % (label, call) for label, call in missing.items()))
            checkpoint.save("missing-required-apis-observation.json", {
                "stdout": output,
                "limit": "proposed public names; absence corroborated by frozen source inventory, "
                         "not an assertion that guessed names exhaust hidden internals"})
            for label, call in missing.items():
                assert "(Probe%s %s)" % (label, call) in output, (label, output)
            before = time.time()
            clock = checkpoint.run("clock", imports + '''
!(println! (WallClock (system:wall-time)))
!(println! (UtcWinter (system:format-time "%Y-%m-%dT%H:%M:%S%z" 1704067200 "utc")))
!(println! (LocalWinter (system:format-time "%Y-%m-%dT%H:%M:%S%z" 1704067200 "local")))
!(println! (LocalSummer (system:format-time "%Y-%m-%dT%H:%M:%S%z" 1719792000 "local")))
''')
            after = time.time()
            observed_wall = float(re.search(r"\(WallClock ([^)]+)\)", clock)[1])
            assert before <= observed_wall <= after, (before, observed_wall, after)
            for label, stamp, zone in (
                ("UtcWinter", 1704067200, datetime.timezone.utc),
                ("LocalWinter", 1704067200, ZoneInfo("Europe/Paris")),
                ("LocalSummer", 1719792000, ZoneInfo("Europe/Paris")),
            ):
                expected = datetime.datetime.fromtimestamp(stamp, zone).strftime("%Y-%m-%dT%H:%M:%S%z")
                assert '(%s "%s")' % (label, expected) in clock, clock
            checkpoint.save("clock-observation.json", {"stdout": clock, "tz": "Europe/Paris",
                "before": before, "after": after, "wall_time_in_bracket": True,
                "fixed_utc_local_dst_cases": 3,
                "limit": "capability smoke probe, not full datetime/ISO parity"})
            # Each emitted JSON text is independently base64 encoded by the
            # transcript recorder. Fixture values never act as clock evidence.
            value = {"z": "日🙂\x00\n\"\\", "a": 1e-7, "zero": -0.0,
                     "nested": [True, None, ["shell", "fixture"]]}
            wire = json.dumps(value, ensure_ascii=False)
            expected_json = {
                "receipt": json.dumps(value, ensure_ascii=False, sort_keys=True),
                "notes": json.dumps(value, ensure_ascii=False),
                "state": json.dumps(value),
                "atlas": json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}
            checkpoint.save("json-expectations.json", expected_json)
            expression = "(json:parse %s)" % json.dumps(wire, ensure_ascii=False)
            output = checkpoint.run("json-options", imports + "".join(
                '!(println! (%s (json:stringify-python-with %s %s)))\n' % (name, expression, options)
                for name, options in (
                    ("Receipt", "((ensure-ascii False) (sort-keys True))"),
                    ("Notes", "((ensure-ascii False))"), ("State", "()"),
                    ("Atlas", '((ensure-ascii False) (sort-keys True) (separators "," ":"))'))))
            compared = {}
            for name, expected in expected_json.items():
                prefix = "(" + name.title() + " "
                line = next(line for line in output.splitlines() if line.startswith(prefix))
                actual = json.loads(line[len(prefix):-1])
                assert actual.encode("utf-8") == expected.encode("utf-8"), (actual, expected)
                compared[name] = {"equal_utf8_bytes": True, "hex": actual.encode("utf-8").hex()}
            checkpoint.save("json-byte-comparisons.json", compared)
        checkpoint.save("checkpoint.json", {
            "status": "EARLY_CAPABILITY_CHECKPOINT", "runtime_activated": False,
            "required": ["same receipt inode exclusive flock + append + fsync",
                         "root/lock held across entire worklog transaction, including all fsyncs/rename",
                         "Python Unicode " + unicodedata.unidata_version + " general-category lookup",
                         "native UUID4 occurrence generation"],
            "not_claimed": ["writer durability", "caller parity", "mixed-route interoperability",
                            "fault parity", "performance", "Lean replay"]})
    print("EARLY_CAPABILITY_CHECKPOINT: no runtime implementation or activation", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
