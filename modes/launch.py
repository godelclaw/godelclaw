"""Launch pinned cognitive loops at a terminal or in a supervised service."""
import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def seed(source, destination):
    destination = Path(destination)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def identity(repo):
    path = Path(os.environ.get("METTACLAW_PROMPT_PATH", str(repo / "memory/soul.md")))
    if not path.exists():
        path = repo / "identity/default-soul.md"
    return path.read_text() if path.exists() else ""


def check_runtime_profile(runtime, standalone):
    ledger = runtime / "adapter-ledger.json"
    if ledger.exists():
        previous = json.loads(ledger.read_text()).get("profile", "supervised")
        expected = "standalone" if standalone else "supervised"
        if previous != expected:
            raise RuntimeError("mode state belongs to a different launch profile; choose another --state-dir")


def adapter_ledger(mode, durable_channel):
    common = {
        "profile": "supervised",
        "channel": "existing durable CWP service" if durable_channel else "independent durable channel required",
        "identity": "agent prompt appended; identity and mode state kept separate",
        "routing": "full Telegram origin metadata and explicit chat/thread channel names; default pinned to operator",
        "delivery": "durable keyed intent queue submitted by control responder; acceptance is not delivery",
        "lifecycle": "independent command responder; operator latch and supervised cognition recycle"}
    if mode == "iter":
        return {**common, "loop": "qualified pinned Iter control port",
            "tool_process": "CeTTa lib/proc; pinned upstream subprocess helper on PeTTa",
            "booleans": "explicit MeTTa atoms at Python predicate boundaries",
            "provider": "existing owned HTTP transport; native OpenAI or Anthropic tool calls",
            "anthropic_policy": "auto tool choice; core retries no-tool replies; default thinking; opaque signed reasoning preserved",
            "memory": "mode-private upstream experience, files and transformations"}
    return {**common,
        "input_annotations": "migrated to current PeTTa annotations",
        "case": "current engine semantics; no duplicate-evaluation compatibility shim",
        "host_values": "explicit Boolean atoms and file predicates; deterministic text concatenation",
        "helper_module": "runtime alias omega_helper avoids host helper name collisions",
        "loop": "pinned Omega loop unchanged",
        "provider": "existing configured command-text adapter",
        "memory": "mode-private history and text recall; embedding add-on disabled",
        "startup": "embedding, NAL/PLN, plugin and optional security-policy startup add-ons disabled; OS permissions retained",
        "tools": "advertise only connected tools; nop added"}


def prepare_iter(runtime, repo, standalone=False, prompt_path=None):
    check_runtime_profile(runtime, standalone)
    runtime.mkdir(parents=True, exist_ok=True)
    for directory in ("tools", "channels", "memory", "transformations"):
        (runtime / directory).mkdir(exist_ok=True)
    for path in (HERE / "iter/upstream/tools").glob("*.py"):
        seed(path, runtime / "tools" / path.name)
    seed(HERE / "iter/upstream/reprogramming.txt", runtime / "reprogramming.txt")
    prompt = runtime / "prompt.txt"
    if not prompt.exists():
        text = (HERE / "iter/upstream/prompt.txt").read_text()
        if prompt_path:
            text += "\n\n" + prompt_path.read_text()
        elif not standalone:
            text += "\n\nAgent identity and standing instructions:\n" + identity(repo)
            text += "\nThis mode's Telegram channel is named telegram. Keep your own identity.\n"
        prompt.write_text(text)
    if standalone:
        for path in (HERE / "iter/upstream/channels").glob("*.py"):
            seed(path, runtime / "channels" / path.name)
        # Upstream tool workers suppress stdout. Forward terminal sends to
        # the foreground process without changing their SUCCESS result.
        terminal = runtime / "channels/terminal.py"
        upstream_terminal = (HERE / "iter/upstream/channels/terminal.py").read_text()
        if terminal.read_text() == upstream_terminal:
            terminal.write_text(upstream_terminal.replace(
                "    print(content)",
                "    import runtime_host\n    runtime_host.terminal_enqueue(content)"))
    else:
        (runtime / "channels/telegram.py").write_text(
            'import runtime_host\ndef receive(): return runtime_host.receive()\n'
            'def send(content): return runtime_host.send(content)\n')
    entry = runtime / "run.metta"
    entry.write_text(f'!(import! &self "{HERE}/iter/core.metta")\n' +
                     ("" if standalone else f'!(import! &self "{HERE}/runtime_host.py")\n'
                      '!(py-call (runtime_host.install_iter))\n') + '!(iter:start)\n')
    (runtime / "adapter-ledger.json").write_text(json.dumps(
        standalone_ledger("iter", prompt_path) if standalone else
        adapter_ledger("iter", bool(os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"))), indent=2))
    return entry, [str(HERE), str(HERE / "iter")]


def prepare_omega(runtime, repo, standalone=False, prompt_path=None):
    check_runtime_profile(runtime, standalone)
    # Runtime modules can be regenerated; memory is never replaced at boot.
    sys.path.insert(0, str(HERE / "omega"))
    from offline import forms, LEGACY_INPUT_DECLARATIONS
    vendor = HERE / "omega/upstream"
    checkout = runtime / "repos/Omega"
    checkout.mkdir(parents=True, exist_ok=True)
    for path in vendor.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(vendor)
        destination = checkout / relative
        if relative.parts[0] == "memory":
            seed(path, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    # SWI-PeTTa caches Python imports by name. The host and Omega both ship
    # helper.py, so the runtime binds Omega's helper under its own name.
    shutil.copy2(checkout / "src/helper.py", checkout / "src/omega_helper.py")
    for relative in ("src/utils.metta", "src/memory.metta", "src/skills.metta", "src/loop.metta"):
        path = checkout / relative
        path.write_text(path.read_text().replace("helper.", "omega_helper."))
    prompt = checkout / "memory/prompt.txt"
    if not (runtime / "identity-seeded").exists():
        if prompt_path:
            prompt.write_text(prompt.read_text() + "\n\n" + prompt_path.read_text())
        elif not standalone:
            prompt.write_text(prompt.read_text() + "\n\nAgent identity and standing instructions:\n" + identity(repo))
        (runtime / "identity-seeded").touch()
    if standalone:
        seed(HERE / "iter/upstream/channels/terminal.py", runtime / "terminal.py")
    for relative, (before, after) in LEGACY_INPUT_DECLARATIONS.items():
        path = checkout / relative
        path.write_text(path.read_text().replace(before, after))
    utilities = [form for form in forms((checkout / "src/utils.metta").read_text())
                 if form != "!(import_prolog_function get_time)" and
                 not any(form.startswith("(= (" + name + " ") or
                         form.startswith("(= (" + name + ")")
                         for name in ("sleep", "get_time_as_string", "joinPath", "string-replace",
                                      "exists-file", "exists-directory"))]
    (runtime / "utils.metta").write_text("\n".join(utilities) + "\n")
    memory = [form for form in forms((checkout / "src/memory.metta").read_text())
              if not any(form.startswith("(= (" + name + " ") or
                         form.startswith("(= (" + name + ")")
                         for name in ("getPrompt", "getHistory", "appendToHistory", "remember", "query"))]
    memory += ['(= (getPrompt $p) (py-call (runtime_host.get_prompt $p)))',
               '(= (getHistory) (py-call (runtime_host.get_history (maxHistory))))',
               '(= (appendToHistory $addition) (py-call (runtime_host.append_history (swrite $addition))))',
               '(= (remember $text) (py-call (runtime_host.remember $text)))',
               '(= (query $text) (py-call (runtime_host.query $text)))']
    (runtime / "memory.metta").write_text("\n".join(memory) + "\n")
    skills = [form for form in forms((checkout / "src/skills.metta").read_text())
              if not any(form.startswith("(= (" + name + " ") or form.startswith("(= (" + name + ")")
                         for name in ("getStaticSkills", "read-file", "write-file", "append-file", "delete-file", "get-io-policy", "write-file-b64"))]
    communication = ('"- Send to the terminal: send string" ' if standalone else
        '"- Send to the private operator: send string" "- Send to an explicit channel from CHANNEL ROUTES: send-channel channel string" ')
    skills += ['(= (getStaticSkills) (' + communication + '"- Execute shell: shell string" "- Read a file: read-file filename" "- Write a file: write-file filename string" "- Append a file: append-file filename string" "- Remember text: remember string" "- Search remembered text: query string" "- Recall history around a timestamp: episodes time_string" "- Pin text: pin string" "- Execute MeTTa: metta string" "- Finish this action without sending: nop" "- Version: version"))',
               '(= (read-file $p) (py-call (runtime_host.read_file $p)))',
               '(= (write-file $p $text) (py-call (runtime_host.write_file $p $text)))',
               '(= (append-file $p $text) (py-call (runtime_host.write_file $p $text True)))',
               '(= (send-channel $destination $message) (py-call (runtime_host.send $message $destination)))',
               '(= (nop) NOP)']
    (runtime / "skills.metta").write_text("\n".join(skills) + "\n")
    (runtime / "rag.py").write_text('def init_knowledge(provider): return "Embedding add-on disabled"\n')
    (runtime / "channels.py").write_text('import runtime_host\ndef commChannelStart(channel): return True\ndef commChannelReceive(): return runtime_host.receive()\ndef commChannelSend(message): return runtime_host.send(message)\n')
    (runtime / "host.metta").write_text(OMEGA_HOST)
    entry = runtime / "run.metta"
    entry.write_text(f'''!(import! &self (library lib_import))
!(git-import! "https://github.com/singnet/Omega.git" "" "{runtime}/repos")
!(import! &self "{HERE}/runtime_host.py")
!(import! &self "{HERE.parent}/src/native_llm.metta")
!(import! &self "{runtime}/rag.py")
!(import! &self "{runtime}/channels.py")
!(import! &self (library Omega ./src/omega_helper.py))
!(py-call (omega_helper.add_llm_command "nop"))
!(py-call (omega_helper.add_llm_command "send-channel"))
!(import! &self "{runtime}/host.metta")
!(import! &self "{runtime}/utils.metta")
!(import! &self "{runtime}/skills.metta")
!(import! &self (library Omega ./src/channels.metta))
!(import! &self "{runtime}/memory.metta")
!(import! &self (library Omega ./src/loop.metta))
!(py-call (runtime_host.boot))
!(omega)
''')
    (runtime / "adapter-ledger.json").write_text(json.dumps(
        standalone_ledger("omega", prompt_path) if standalone else
        adapter_ledger("omega", bool(os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"))), indent=2))
    return entry, [str(runtime), str(checkout), str(checkout / "src"), str(HERE)]


OMEGA_HOST = r'''
(= (py-str-helper $L $outp)
   (if (== $L ()) $outp
       (let* (($head (car-atom $L)) ($tail (cdr-atom $L))
              ($outp2 (py-call (operator.add $outp (py-call (str $head))))))
         (py-str-helper $tail $outp2))))
(= (py-str $L) (py-str-helper $L ""))
(= (configure $key $default)
   (let $value (py-call (runtime_host.config $key $default))
     (add-atom &self (= ($key) $value))))
(= (initConfig) (py-call (runtime_host.init_config)))
(= (initLogger) ())
(= (applySecurityPolicy) ())
(= (initPlugins) ())
(= (log $level $module $message) ())
(= (llmProviderStart $provider) (py-call (runtime_host.start_provider $provider)))
(= (llmProviderChat $prompt $max $reasoning)
   (if (py-call (runtime_host.standalone_flag))
     (py-call (runtime_host.provider $prompt $max $reasoning))
     (let* (($context (py-call (runtime_host.provider_prompt $prompt)))
          ($model (py-call (runtime_host.provider_model))))
       (llm:command $model $max $reasoning $context))))
(= (get_time) (py-call (time.time)))
(= (get_time_as_string) (py-call (time.strftime "%Y-%m-%d %H:%M:%S")))
(= (joinPath $parts) (py-call (runtime_host.join_path $parts)))
(= (sleep $seconds) (py-call (runtime_host.omega_sleep $seconds)))
(= (string-replace $text $separators $replacement) (py-call (runtime_host.string_replace $text $separators $replacement)))
(= (exists-file $path) (py-call (runtime_host.path_exists $path "file")))
(= (exists-directory $path) (py-call (runtime_host.path_exists $path "directory")))
'''


def engine_command(engine, entry, environment):
    if engine == "cetta":
        binary = environment.get("METTACLAW_MODE_CETTA_BIN") or environment.get("CETTA_BIN")
        if not binary:
            binary = str(Path(environment["CETTA_ROOT"]) / "cetta") if environment.get("CETTA_ROOT") else shutil.which("cetta")
        if not binary:
            raise RuntimeError("set CETTA_BIN to an installed CeTTa bundle, or put cetta on PATH")
        binary = str(Path(binary).resolve())
        if not (Path(binary).parent / "lib/petta/lib_import.metta").exists():
            raise RuntimeError("mode engine bundle is missing its lib directory")
        return [binary, "--lang", "petta", "--import-mode", "ancestor-walk", str(entry)]
    # Upstream run.sh need not have a shebang. Python's exec does not provide
    # the ENOEXEC shell fallback that an interactive shell does.
    if not environment.get("PETTA_ROOT"):
        raise RuntimeError("set PETTA_ROOT to a SWI-PeTTa checkout")
    return ["bash", str(Path(environment["PETTA_ROOT"]).resolve() / "run.sh"), str(entry), "--silent"]


def standalone_ledger(mode, prompt_path):
    ledger = {"profile": "standalone", "channel": "terminal",
        "identity": "explicit prompt addition" if prompt_path else "pinned upstream prompt only",
        "provider": "pinned upstream SDK and environment variables",
        "lifecycle": "foreground process; Ctrl-C stops the launch",
        "loop": "pinned Iter control port" if mode == "iter" else "pinned Omega core loop",
        "memory": "upstream experience/files" if mode == "iter" else "history and text recall; no embeddings"}
    if mode == "iter":
        ledger["terminal_output"] = "isolated worker sends forwarded at the foreground checkpoint"
    if mode == "omega":
        ledger["startup"] = "core only; embedding, NAL/PLN and optional plugins are not loaded"
        ledger["helper_module"] = "runtime alias omega_helper avoids host helper name collisions"
    return ledger


def standalone_environment(mode, state, runtime, environment):
    # An explicit terminal launch owns its state, even inside a service shell.
    # Never inherit that instance's model selection, pause latch or inbox.
    result = {key: value for key, value in environment.items()
              if not key.startswith("METTACLAW_") and not key.startswith("CETTA_IO_AUTH_")}
    for key in ("CETTA_BIN", "CETTA_ROOT", "PETTA_ROOT", "METTACLAW_MODE_CETTA_BIN"):
        if key in environment:
            result[key] = environment[key]
    result.update(METTACLAW_MODE_PROFILE="standalone",
                  METTACLAW_MODE_STATE_ROOT=str(state),
                  METTACLAW_MODE_RUNTIME=str(runtime),
                  METTACLAW_LOOP_MODE_PATH=str(state / "selection.json"),
                  METTACLAW_ENGINE_STATE_PATH=str(state / "engine"),
                  METTACLAW_MODEL_STATE_PATH=str(state / "model.metta"),
                  METTACLAW_LIFECYCLE_PATH=str(state / "lifecycle.json"),
                  METTACLAW_COGNITIVE_HEALTH_PATH=str(runtime / "health.json"),
                  METTACLAW_LLM_LEDGER_PATH=str(runtime / "llm-budget.sqlite3"),
                  METTACLAW_LLM_POLICY="upstream", METTACLAW_LLM_TRANSPORT="python",
                  METTACLAW_OBSERVED_STRATEGY="0", METTACLAW_FAIR_GOALS="0",
                  ITER_PYTHON=sys.executable,
                  PATH=os.pathsep.join([str(Path(sys.executable).parent), result.get("PATH", "")]),
                  PYTHONNOUSERSITE="1",
                  LD_LIBRARY_PATH=os.pathsep.join(filter(None, [str(Path(sys.base_prefix) / "lib"),
                                                                result.get("LD_LIBRARY_PATH")])))
    # CPython discovers pyvenv.cfg from the host executable on PATH. Setting
    # PYTHONHOME to a venv instead would hide the base standard library.
    result.pop("PYTHONHOME", None)
    return result


def run_child(command, runtime, environment):
    # Spawn before entering cleanup: a failed exec has no child to terminate.
    child = subprocess.Popen(command, cwd=runtime, env=environment, start_new_session=True)
    stopped = False
    stop_deadline = None

    def terminate(signum, frame):
        nonlocal stopped, stop_deadline
        stopped = True
        if stop_deadline is None:
            stop_deadline = time.monotonic() + 10
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    handlers = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        while True:
            try:
                status = child.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if stop_deadline is not None and time.monotonic() >= stop_deadline:
                    os.killpg(child.pid, signal.SIGKILL)
    finally:
        # PeTTa's runner spawns SWI-Prolog; terminate the entire process group,
        # including workers, before releasing the single-cognition lock.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return 0 if stopped else (128 - status if status < 0 else status)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=HERE.parent)
    parser.add_argument("--mode", choices=("iter", "omega"), required=True)
    parser.add_argument("--engine", choices=("petta", "cetta"), default="cetta")
    parser.add_argument("--profile", choices=("auto", "standalone", "supervised"), default="auto")
    parser.add_argument("--state-dir", type=Path, help="directory for this launch's persistent mode state")
    parser.add_argument("--prompt", type=Path, help="append this prompt when seeding a new standalone mode")
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    standalone = args.profile == "standalone" or (args.profile == "auto" and
                                                  not os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"))
    if args.prompt and not args.prompt.is_file():
        parser.error("--prompt must name a readable file")
    if args.prompt and not standalone:
        parser.error("--prompt is for standalone launches; supervised instances use their configured prompt")
    if not standalone and not os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"):
        parser.error("supervised mode requires an independent Telegram channel service")
    if standalone:
        needed = ("openai",) if args.mode == "iter" else ("openai", "yaml")
        if any(importlib.util.find_spec(module) is None for module in needed):
            parser.error(f"install the selected mode's dependencies: python3 -m pip install -r modes/{args.mode}/requirements.txt")
    if args.state_dir:
        state = args.state_dir.resolve()
    elif standalone:
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "mettaclaw" / args.repo.resolve().name / "standalone"
    else:
        explicit = os.environ.get("METTACLAW_MODE_STATE_ROOT")
        engine_state = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
        if not explicit and not engine_state:
            parser.error("supervised mode requires METTACLAW_MODE_STATE_ROOT or METTACLAW_ENGINE_STATE_PATH")
        state = Path(explicit) if explicit else Path(engine_state).parent / "modes"
    state = state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / "cognition.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runtime = state / args.mode
    environment = (standalone_environment(args.mode, state, runtime, os.environ) if standalone
                   else os.environ.copy())
    environment.update(METTACLAW_MODE_STATE_ROOT=str(state),
                       METTACLAW_AGENT_ROOT=str(args.repo), METTACLAW_ACTIVE_LOOP_MODE=args.mode,
                       METTACLAW_ENGINE=args.engine, METTACLAW_ACTIVE_ENGINE=args.engine,
                       PYTHONDONTWRITEBYTECODE="1")
    if not standalone:
        environment["METTACLAW_MODE_RUNTIME"] = str(runtime)
    # Diagnose engine setup before writing a runtime or seeding its prompt.
    command = engine_command(args.engine, runtime / "run.metta", environment)
    if standalone and args.engine == "cetta":
        version = subprocess.run([command[0], "--version"], env=environment,
                                 capture_output=True, text=True, timeout=10)
        if version.returncode or not any(f"({build})" in version.stdout for build in ("python", "main", "full")):
            parser.error("standalone modes need a CeTTa build with Python support (BUILD=python)")
    entry, paths = (prepare_iter if args.mode == "iter" else prepare_omega)(
        runtime, args.repo, standalone=standalone, prompt_path=args.prompt)
    # The loop's CWD is mode-private; selection remains agent-wide.
    environment["METTACLAW_LOOP_MODE_PATH"] = os.path.abspath(
        environment.get("METTACLAW_LOOP_MODE_PATH", str(args.repo / "memory/loop_mode.json")))
    environment["PYTHONPATH"] = os.pathsep.join(paths + [str(args.repo / "src"),
        str(args.repo / "channels"), str(args.repo / "repos/petta_lib_chromadb")])
    print(f"Starting {args.mode} ({'standalone terminal' if standalone else 'supervised'}); state: {runtime}", flush=True)
    if standalone and args.mode == "omega":
        print("Omega core: history/text recall; embeddings and optional plugins are not loaded.", flush=True)
    return run_child(command, runtime, environment)


if __name__ == "__main__":
    sys.exit(main())
