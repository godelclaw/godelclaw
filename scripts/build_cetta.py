#!/usr/bin/env python3
"""Build the pinned CeTTa engine and services into one reusable bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/godelclaw/CeTTa.git"


def python_config():
    version = f"python{sys.version_info.major}.{sys.version_info.minor}-config"
    for candidate in (Path(sys.executable).resolve().with_name(version),
                      Path(sys.base_prefix) / "bin" / version):
        if candidate.is_file():
            return str(candidate)
    candidate = shutil.which("python3-config")
    if candidate:
        includes = subprocess.check_output([candidate, "--includes"], text=True)
        if f"/python{sys.version_info.major}.{sys.version_info.minor}" in includes:
            return candidate
    raise RuntimeError("matching Python development headers are unavailable; install them or choose --python-config explicitly")


def run(args, cwd=None, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="existing checkout at the selected revision")
    parser.add_argument("--prefix", type=Path, default=ROOT / ".runtime/cetta")
    parser.add_argument("--ref", default=(ROOT / "CETTA_REF").read_text().strip())
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("BUILD_JOBS", "1")))
    parser.add_argument("--prolog", choices=("auto", "0", "1"), default="auto",
                        help="optional SWI-Prolog provider (auto detects the installed SDK; 0 builds without it)")
    parser.add_argument("--python-config", default=os.environ.get("PYTHON_CONFIG"))
    parser.add_argument("--sqlite-cflags", default=os.environ.get("SQLITE_CFLAGS"))
    parser.add_argument("--sqlite-libs", default=os.environ.get("SQLITE_LIBS"))
    args = parser.parse_args()
    if args.python_config is None:
        try:
            args.python_config = python_config()
        except RuntimeError as error:
            parser.error(str(error))
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.ref):
        parser.error("select a full commit ID in CETTA_REF or --ref")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    revision = args.ref.lower()
    source = (args.source or ROOT / ".build/CeTTa").resolve()
    prefix = args.prefix.resolve()
    if source == prefix or source in prefix.parents or prefix in source.parents:
        parser.error("source and installed bundle must be separate directories")
    if not (source / ".git").exists():
        if args.source:
            parser.error("--source must be a Git checkout")
        source.mkdir(parents=True, exist_ok=True)
        run(["git", "init", str(source)])
        run(["git", "remote", "add", "origin", args.repository], source)
        run(["git", "fetch", "--depth", "1", "origin", revision], source)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], source)
    head = run(["git", "rev-parse", "HEAD"], source, capture=True).strip()
    if head != revision:
        parser.error("source is at a different revision; use a matching checkout or a new build directory")
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=source).returncode:
        parser.error("source has tracked changes; commit them and select that revision before building")
    if subprocess.run(["pkg-config", "--exists", "libpcre2-8"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        parser.error("native file search requires PCRE2 development headers (libpcre2-dev)")
    python_prefix = run([args.python_config, "--prefix"], capture=True).strip()
    sdk = Path(python_prefix)
    if args.sqlite_cflags is None and (sdk / "include/sqlite3.h").is_file():
        args.sqlite_cflags = "-I" + str(sdk / "include")
    if args.sqlite_libs is None and (sdk / "lib/libsqlite3.so").is_file():
        args.sqlite_libs = "-L" + str(sdk / "lib") + " -lsqlite3"
    flags = ["BUILD=python", "ENABLE_HTTP=1", "ENABLE_DURABLE=1", "SQLITE_PROVIDER=system",
             "ENABLE_MANAGED_SYMBOLS=1", "ENABLE_LIB_PROLOG=" + args.prolog,
             "ENABLE_JSON_GSLT=1", "PYTHON_CONFIG=" + args.python_config]
    if args.sqlite_cflags is not None:
        flags.append("SQLITE_CFLAGS=" + args.sqlite_cflags)
    if args.sqlite_libs is not None:
        flags.append("SQLITE_LIBS=" + args.sqlite_libs)
    run(["make", "-j", str(args.jobs), *flags,
         "cetta", "space-service", "value-worker", "telegram-service"], source)
    # Ask Make for its resolved tagged paths; do not guess which older binary
    # in runtime/ belongs to this feature configuration.
    recipe = 'godelclaw-release-paths:;@printf "GODELCLAW_ARTIFACT=%s\\n" "$(BIN)" "$(SPACE_SERVICE_BIN)" "$(VALUE_WORKER_BIN)" "$(TELEGRAM_SERVICE_BIN)"'
    output = run(["make", "--no-print-directory", "-s", *flags,
                  "--eval", recipe, "godelclaw-release-paths"], source, capture=True)
    artifacts = [line.split("=", 1)[1] for line in output.splitlines()
                 if line.startswith("GODELCLAW_ARTIFACT=")]
    if len(artifacts) != 4:
        raise RuntimeError("Make did not report the four release artifacts")
    prefix.mkdir(parents=True, exist_ok=True)
    names = ["cetta", "cetta-space-service", "cetta-value-worker", "cetta-telegram-service"]
    for name, artifact in zip(names, artifacts):
        shutil.copy2(source / artifact, prefix / name)
    for name in ("lib", "tools"):
        shutil.copytree(source / name, prefix / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    python_prefix = run([args.python_config, "--prefix"], capture=True).strip()
    includes = run([args.python_config, "--includes"], capture=True)
    version = re.search(r"/python(\d+\.\d+)", includes)
    environment = {
        "CETTA_BIN": str(prefix / "cetta"),
        "METTACLAW_MODE_CETTA_BIN": str(prefix / "cetta"),
        "CETTA_TELEGRAM_SERVICE_BIN": str(prefix / "cetta-telegram-service"),
        "CETTA_TELEGRAM_SERVICE_ROOT": str(prefix),
    }
    (prefix / "runtime.env").write_text("".join(key + "=" + value + "\n"
                                                for key, value in environment.items()))
    manifest = {"schema": 1, "repository": args.repository, "revision": revision,
                "features": flags, "python_version": version.group(1) if version else None,
                "python_prefix": python_prefix,
                "files": {str(p.relative_to(prefix)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(prefix.rglob("*")) if p.is_file() and p.name != "build-manifest.json"}}
    (prefix / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"revision": revision, "bundle": str(prefix),
                      "cetta": str(prefix / "cetta"), "python_prefix": python_prefix}))


if __name__ == "__main__":
    main()
