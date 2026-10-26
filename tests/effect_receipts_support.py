"""Isolated, identity-bracketed project05 qualification on reviewer bundle 42e89820."""
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

from effect_receipts_capability_checkpoint import Checkpoint, LIMIT


ROOT = Path(__file__).resolve().parents[1]
EXE_SHA = "c8d9513628e102c90bf1043e177988332af6af1522c80af961abc8f153ff2ba1"
MANIFEST_SHA = "504ead82db62dd2da78a8e69b4b17daad19bf9b5865826a3125b50fcd2bd71e9"


def q(text):
    # MeTTa does not decode JSON \uXXXX string-literal escapes. JSON parsing
    # explicitly recovers scalar strings, including NUL, from an ASCII wire.
    return '(json:parse %s)' % json.dumps(json.dumps(str(text), ensure_ascii=True))


class Qualification(Checkpoint):
    def identity(self, phase, edge):
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = self.bundle / "SHA256SUMS"
        assert sha(manifest) == MANIFEST_SHA, "new frozen manifest mismatch"
        assert sha(self.binary) == EXE_SHA, "new frozen executable mismatch"
        rows = [line.split("  ", 1) for line in manifest.read_text().splitlines()]
        assert len(rows) == len({name for _, name in rows}) == 341
        observed = {"cetta"} | {str(p.relative_to(self.bundle))
                                for p in (self.bundle / "lib").rglob("*") if p.is_file()}
        assert observed == {name for _, name in rows}, "frozen library membership mismatch"
        assert not any(p.is_symlink() for p in (self.bundle / "lib").rglob("*"))
        for expected, name in rows:
            path = self.bundle / name
            assert not path.is_symlink() and path.resolve().is_relative_to(self.bundle)
            assert sha(path) == expected, name
        self.save(phase + "-" + edge + ".json", {
            "phase": phase, "edge": edge, "binary": str(self.binary),
            "executable_sha256": EXE_SHA, "manifest_sha256": MANIFEST_SHA,
            "files": rows, "python": sys.version, "python_executable": sys.executable,
            "rlimit_as": resource.getrlimit(resource.RLIMIT_AS),
            "provenance": "reviewer-supplied port-05-primitives bf79d674+42e89820 on 818d6360; "
                          "source/build and reviewer strace are attributed, not independently certified"})
        print(phase, edge, "341 new frozen identities/membership match", flush=True)

    def environment(self):
        env = super().environment()
        env.pop("METTACLAW_PUBLIC_WORK_LIB", None)
        env.update(PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("tests", "src", "channels")),
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            Path(env[key]).mkdir(exist_ok=True)
        return env

    def record_process(self, stem, command, result, env):
        (self.evidence / (stem + ".stdout")).write_bytes(result.stdout)
        (self.evidence / (stem + ".stderr")).write_bytes(result.stderr)
        self.save(stem + ".json", {"command": list(map(str, command)), "cwd": str(self.directory),
                                  "environment": env, "returncode": result.returncode,
                                  "stdout_base64": base64.b64encode(result.stdout).decode(),
                                  "stderr_base64": base64.b64encode(result.stderr).decode()})
        print(stem, "actual exit", result.returncode, flush=True)

    def run(self, name, program, settings=None, check=True):
        self.index += 1
        stem = "%03d-%s" % (self.index, name)
        for part in ("src", "tests", "verification"):
            program = program.replace("./" + part + "/", str(ROOT / part) + "/")
        source = self.directory / (stem + ".metta")
        source.write_text(program, encoding="utf-8")
        (self.evidence / (stem + ".metta")).write_bytes(source.read_bytes())
        command = [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk", str(source)]
        env = self.environment()
        env.update(settings or {})
        result = subprocess.run(command, cwd=self.directory, env=env,
                                capture_output=True, timeout=90)
        self.record_process(stem, command, result, env)
        if check:
            assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
        return result.stdout.decode("utf-8", errors="backslashreplace")


IMPORTS = "".join("!(import! &self (library %s))\n" % name
                  for name in ("lib_import", "fs", "str", "json", "system", "path", "math"))


def limit():
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))


def rows(output):
    """JSON-wire test records; engine's own top-level true answers are ignored."""
    prefix = "(Wire "
    return [json.loads(json.loads(line[len(prefix):-1]))
            for line in output.splitlines() if line.startswith(prefix)]


def emit(expression):
    return '!(println! (Wire (json:stringify-python-with %s ((ensure-ascii True)))))\n' % expression
