"""Select a standalone cognitive loop in the existing agent service."""
import argparse
import fcntl
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
    path = Path(os.environ.get("METTACLAW_PROMPT_PATH", str(repo / "memory/prompt.txt")))
    if not path.exists():
        path = repo / "identity/default-prompt.txt"
    return path.read_text() if path.exists() else ""


def adapter_ledger(mode, durable_channel):
    common = {
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
        "loop": "pinned Omega loop unchanged",
        "provider": "existing configured command-text adapter",
        "memory": "mode-private history and text recall; embedding add-on disabled",
        "startup": "embedding, NAL/PLN, plugin and optional security-policy startup add-ons disabled; OS permissions retained",
        "tools": "advertise only connected tools; nop added"}


def prepare_iter(runtime, repo):
    runtime.mkdir(parents=True, exist_ok=True)
    for directory in ("tools", "channels", "memory", "transformations"):
        (runtime / directory).mkdir(exist_ok=True)
    for path in (HERE / "iter/upstream/tools").glob("*.py"):
        seed(path, runtime / "tools" / path.name)
    seed(HERE / "iter/upstream/reprogramming.txt", runtime / "reprogramming.txt")
    prompt = runtime / "prompt.txt"
    if not prompt.exists():
        prompt.write_text((HERE / "iter/upstream/prompt.txt").read_text() +
            "\n\nAgent identity and standing instructions:\n" + identity(repo) +
            "\nThis mode's Telegram channel is named telegram. Keep your own identity.\n")
    (runtime / "channels/telegram.py").write_text(
        'import runtime_host\ndef receive(): return runtime_host.receive()\n'
        'def send(content): return runtime_host.send(content)\n')
    entry = runtime / "run.metta"
    entry.write_text(f'!(import! &self "{HERE}/iter/core.metta")\n'
                     f'!(import! &self "{HERE}/runtime_host.py")\n'
                     '!(py-call (runtime_host.install_iter))\n!(iter:start)\n')
    (runtime / "adapter-ledger.json").write_text(json.dumps(
        adapter_ledger("iter", bool(os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"))), indent=2))
    return entry, [str(HERE), str(HERE / "iter")]


def prepare_omega(runtime, repo):
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
    prompt = checkout / "memory/prompt.txt"
    if not (runtime / "identity-seeded").exists():
        prompt.write_text(prompt.read_text() + "\n\nAgent identity and standing instructions:\n" + identity(repo))
        (runtime / "identity-seeded").touch()
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
    skills += ['(= (getStaticSkills) ("- Send to the private operator: send string" "- Send to an explicit channel from CHANNEL ROUTES: send-channel channel string" "- Execute shell: shell string" "- Read a file: read-file filename" "- Write a file: write-file filename string" "- Append a file: append-file filename string" "- Remember text: remember string" "- Search remembered text: query string" "- Recall history around a timestamp: episodes time_string" "- Pin text: pin string" "- Execute MeTTa: metta string" "- Finish this action without sending: nop" "- Version: version"))',
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
!(import! &self (library Omega ./src/helper.py))
!(py-call (helper.add_llm_command "nop"))
!(py-call (helper.add_llm_command "send-channel"))
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
        adapter_ledger("omega", bool(os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"))), indent=2))
    return entry, [str(runtime), str(checkout / "src"), str(HERE)]


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
(= (initConfig) ())
(= (initLogger) ())
(= (applySecurityPolicy) ())
(= (initPlugins) ())
(= (log $level $module $message) ())
(= (llmProviderStart $provider) True)
(= (llmProviderChat $prompt $max $reasoning)
   (let* (($context (py-call (runtime_host.provider_prompt $prompt)))
          ($model (py-call (runtime_host.provider_model))))
     (llm:command $model $max $reasoning $context)))
(= (get_time) (py-call (time.time)))
(= (get_time_as_string) (py-call (time.strftime "%Y-%m-%d %H:%M:%S")))
(= (joinPath $parts) (py-call (helper.joinPath $parts)))
(= (sleep $seconds) (py-call (runtime_host.omega_sleep $seconds)))
(= (string-replace $text $separators $replacement) (py-call (runtime_host.string_replace $text $separators $replacement)))
(= (exists-file $path) (py-call (runtime_host.path_exists $path "file")))
(= (exists-directory $path) (py-call (runtime_host.path_exists $path "directory")))
'''


def engine_command(engine, entry, environment):
    if engine == "cetta":
        binary = environment.get("METTACLAW_MODE_CETTA_BIN") or environment.get("CETTA_BIN") or str(Path(environment["CETTA_ROOT"]) / "cetta")
        if not (Path(binary).resolve().parent / "lib/petta/lib_import.metta").exists():
            raise RuntimeError("mode engine bundle is missing its lib directory")
        return [binary, "--lang", "petta", "--import-mode", "ancestor-walk", str(entry)]
    # Upstream run.sh need not have a shebang. Python's exec does not provide
    # the ENOEXEC shell fallback that an interactive shell does.
    return ["bash", str(Path(environment["PETTA_ROOT"]) / "run.sh"), str(entry), "--silent"]


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
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--mode", choices=("iter", "omega"), required=True)
    parser.add_argument("--engine", choices=("petta", "cetta"), required=True)
    args = parser.parse_args()
    state = Path(os.environ.get("METTACLAW_MODE_STATE_ROOT",
        str(Path(os.environ["METTACLAW_ENGINE_STATE_PATH"]).parent / "modes")))
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / "cognition.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runtime = state / args.mode
    entry, paths = (prepare_iter if args.mode == "iter" else prepare_omega)(runtime, args.repo)
    environment = os.environ.copy()
    environment.update(METTACLAW_MODE_RUNTIME=str(runtime), METTACLAW_MODE_STATE_ROOT=str(state),
                       METTACLAW_AGENT_ROOT=str(args.repo), METTACLAW_ACTIVE_LOOP_MODE=args.mode,
                       METTACLAW_ENGINE=args.engine, METTACLAW_ACTIVE_ENGINE=args.engine,
                       PYTHONDONTWRITEBYTECODE="1")
    # The loop's CWD is mode-private; selection remains agent-wide.
    environment["METTACLAW_LOOP_MODE_PATH"] = os.path.abspath(
        environment.get("METTACLAW_LOOP_MODE_PATH", str(args.repo / "memory/loop_mode.json")))
    environment["PYTHONPATH"] = os.pathsep.join(paths + [str(args.repo / "src"),
        str(args.repo / "channels"), str(args.repo / "repos/petta_lib_chromadb")])
    if not environment.get("METTACLAW_TELEGRAM_SERVICE_SOCKET"):
        raise RuntimeError("Iter/Omega require an independently supervised Telegram channel and control responder")
    return run_child(engine_command(args.engine, entry, environment), runtime, environment)


if __name__ == "__main__":
    sys.exit(main())
