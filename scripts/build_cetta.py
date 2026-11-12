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
import tempfile

from cetta_profile import PROFILES, build_flags, publication_directory

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/godelclaw/CeTTa.git"
RUNTIME_NAMES = ("cetta", "cetta-space-service", "cetta-value-worker", "cetta-telegram-service")


def install_runtime_artifacts(source, prefix, artifacts):
    """Install the resolved executables and each executable's process helper."""
    if len(artifacts) != len(RUNTIME_NAMES) + 1:
        raise RuntimeError("Make must report four executables and their process-scope helper")
    paths = [source / artifact for artifact in artifacts]
    for path in paths:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("release artifact is missing or not executable: " + str(path))
    prefix.mkdir(parents=True, exist_ok=True)
    for name, artifact in zip(RUNTIME_NAMES, paths[:-1]):
        shutil.copy2(artifact, prefix / name)
        # The runtime resolves /proc/self/exe + '.proc-scope'. Services can
        # execute MeTTa too, so their relocated names need companions as well.
        shutil.copy2(paths[-1], prefix / (name + ".proc-scope"))


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


def verify_sqlite_feature(binary):
    """Check the installed API, including the disabled-but-unreduced case."""
    result = subprocess.run([str(binary), "--lang", "petta",
        "-e", "!(import! &self (library sqlite))",
        "-e", '!(sqlite:query ":memory:" "SELECT 7 AS value" () 0)'],
        text=True, capture_output=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"})
    if result.returncode or result.stdout.splitlines() != ["true", '(sqlite:result ("value") ((7)) 0 0)']:
        raise RuntimeError("installed engine lacks the required native SQLite query API")


def verify_durable_feature(prefix):
    """Verify native durable Space storage and (when bundled) (library db)."""
    binary = prefix / "cetta"
    worker = prefix / "cetta-value-worker"
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise RuntimeError("installed bundle lacks executable cetta-value-worker")
    with tempfile.TemporaryDirectory(prefix="cetta-durable-verify-") as root:
        os.chmod(root, 0o700)
        store_dir = Path(root) / "probe.db"
        store_dir.mkdir(mode=0o700)
        os.chmod(store_dir, 0o700)
        exprs = [
            "-e", "!(import! &self (library durable_space))",
            "-e", f'!(case (store:open "{store_dir}" "create") (((StoreOpened $o) (store:close $o)) ($e $e)))',
        ]
        expected = ["true", "StoreClosed"]
        if (prefix / "lib/petta/db.metta").is_file():
            db_dir = Path(root) / "ergonomic.db"
            exprs += [
                "-e", "!(import! &self (library db))",
                "-e", f'!(case (db:open "{db_dir}") (($c (db:close $c))))',
            ]
            expected += ["true", "true"]
        result = subprocess.run([str(binary), "--lang", "petta", *exprs],
            text=True, capture_output=True, timeout=30,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"})
        if result.returncode or result.stdout.splitlines() != expected:
            raise RuntimeError("installed engine lacks the required native durable Space API: "
                               + (result.stderr or result.stdout))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="python",
                        help="native-agent builds without a Python SDK and includes native ChatGPT")
    parser.add_argument("--source", type=Path, help="existing checkout at the selected revision")
    parser.add_argument("--prefix", type=Path, default=ROOT / ".runtime/cetta")
    parser.add_argument("--ref", default=(ROOT / "CETTA_REF").read_text().strip())
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("BUILD_JOBS", "1")))
    parser.add_argument("--prolog", choices=("auto", "0", "1"), default="auto",
                        help="optional SWI-Prolog provider (auto detects the installed SDK; 0 builds without it)")
    parser.add_argument("--chatgpt", action="store_true",
                        help="build native ChatGPT subscription sign-in (requires cJOSE >= 0.8.1, Jansson and OpenSSL)")
    parser.add_argument("--python-config", default=os.environ.get("PYTHON_CONFIG"))
    parser.add_argument("--sqlite-cflags", default=os.environ.get("SQLITE_CFLAGS"))
    parser.add_argument("--sqlite-libs", default=os.environ.get("SQLITE_LIBS"))
    args = parser.parse_args()
    if args.profile == "native-agent" and args.prolog == "1":
        parser.error("native-agent requires --prolog 0 or auto")
    if args.profile == "python" and args.python_config is None:
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
    if args.profile == "native-agent" and args.prefix.is_symlink():
        parser.error("native-agent destination must not be a symbolic link")
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
    python_prefix = None
    version = None
    if args.profile == "python":
        python_prefix = run([args.python_config, "--prefix"], capture=True).strip()
        includes = run([args.python_config, "--includes"], capture=True)
        version = re.search(r"/python(\d+\.\d+)", includes)
        if version is None:
            parser.error("Python SDK did not identify its interpreter version")
        sdk = Path(python_prefix)
        if args.sqlite_cflags is None and (sdk / "include/sqlite3.h").is_file():
            args.sqlite_cflags = "-I" + str(sdk / "include")
        if args.sqlite_libs is None and (sdk / "lib/libsqlite3.so").is_file():
            args.sqlite_libs = "-L" + str(sdk / "lib") + " -lsqlite3"
    flags = build_flags(args.profile, args.prolog, args.chatgpt, args.python_config,
                        args.sqlite_cflags, args.sqlite_libs)
    run(["make", "-j", str(args.jobs), *flags,
         "cetta", "space-service", "value-worker", "telegram-service"], source)
    # Ask Make for its resolved tagged paths; do not guess which older binary
    # in runtime/ belongs to this feature configuration.
    recipe = 'godelclaw-release-paths:;@printf "GODELCLAW_ARTIFACT=%s\\n" "$(BIN)" "$(SPACE_SERVICE_BIN)" "$(VALUE_WORKER_BIN)" "$(TELEGRAM_SERVICE_BIN)" "$(BIN).proc-scope"'
    output = run(["make", "--no-print-directory", "-s", *flags,
                  "--eval", recipe, "godelclaw-release-paths"], source, capture=True)
    artifacts = [line.split("=", 1)[1] for line in output.splitlines()
                 if line.startswith("GODELCLAW_ARTIFACT=")]
    if (run(["git", "rev-parse", "HEAD"], source, capture=True).strip() != revision or
            subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=source).returncode):
        parser.error("source changed during build; no artifact was installed")
    with publication_directory(prefix, args.profile == "native-agent") as bundle:
        install_runtime_artifacts(source, bundle, artifacts)
        for name in ("lib", "tools"):
            shutil.copytree(source / name, bundle / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        verify_sqlite_feature(bundle / "cetta")
        verify_durable_feature(bundle)
        for directory, notice in (("secp256k1", "COPYING"), ("tomlc17", "LICENSE")):
            license_file = source / "vendor" / directory / notice
            if license_file.is_file():
                destination = bundle / "licenses" / directory
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(license_file, destination / notice)
        if args.chatgpt or args.profile == "native-agent":
            shutil.copytree(source / "docs/siwc/licenses", bundle / "licenses/siwc", dirs_exist_ok=True)
        environment = {
            "CETTA_BIN": str(prefix / "cetta"),
            "METTACLAW_MODE_CETTA_BIN": str(prefix / "cetta"),
            "CETTA_VALUE_WORKER_BIN": str(prefix / "cetta-value-worker"),
            "CETTA_TELEGRAM_SERVICE_BIN": str(prefix / "cetta-telegram-service"),
            "CETTA_TELEGRAM_SERVICE_ROOT": str(prefix),
        }
        (bundle / "runtime.env").write_text("".join(key + "=" + value + "\n"
                                                    for key, value in environment.items()))
        manifest = {"schema": 1, "repository": args.repository, "revision": revision,
                    "features": flags, "runtime_profile": args.profile,
                    "python_enabled": args.profile == "python", "python_version": version.group(1) if version else None,
                    "python_prefix": python_prefix,
                    "files": {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(bundle.rglob("*")) if p.is_file() and p.name != "build-manifest.json"}}
        (bundle / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"revision": revision, "bundle": str(prefix),
                      "cetta": str(prefix / "cetta"), "python_prefix": python_prefix}))


if __name__ == "__main__":
    main()
