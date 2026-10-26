"""Isolated, manifest-checked project05 qualification; optional pins come from env."""
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


def q(text):
    # MeTTa does not decode JSON \uXXXX string-literal escapes. JSON parsing
    # explicitly recovers scalar strings, including NUL, from an ASCII wire.
    return '(json:parse %s)' % json.dumps(json.dumps(str(text), ensure_ascii=True))


class Qualification(Checkpoint):
    def snapshot_sources(self, *names):
        hashes = {}
        for name in names:
            path = ROOT / name
            assert path.resolve().is_relative_to(ROOT) and path.is_file(), name
            raw = path.read_bytes()
            target = self.evidence / 'local-source' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        self.save('local-source-hashes.json', hashes)

    def environment(self):
        env = super().environment()
        env.pop("METTACLAW_PUBLIC_WORK_LIB", None)
        env.update(PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("tests", "src", "channels")),
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            Path(env[key]).mkdir(exist_ok=True)
        registry = self.directory / 'fixture-project-registry.json'
        if not registry.exists():
            registry.write_text('{"schema":1,"projects":[]}\n')
        env.update(METTACLAW_PROJECT_REGISTRY_PATH=str(registry),
                   METTACLAW_ITER_PROCESS_DIR=str(self.directory / 'transformations'),
                   METTACLAW_REPORTS_ROOT=str(self.directory / 'reports'),
                   METTACLAW_REPOS_DIR=str(self.directory / 'repos'))
        return env

    def record_process(self, stem, command, result, env, cwd=None):
        (self.evidence / (stem + ".stdout")).write_bytes(result.stdout)
        (self.evidence / (stem + ".stderr")).write_bytes(result.stderr)
        self.save(stem + ".json", {"command": list(map(str, command)), "cwd": str(self.directory if cwd is None else cwd),
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
        # Runtime modules use ./src imports. Fixtures remain absolute paths in
        # the disposable directory, but resolution must use the package root.
        result = subprocess.run(command, cwd=ROOT, env=env,
                                capture_output=True, timeout=90)
        self.record_process(stem, command, result, env, cwd=ROOT)
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
