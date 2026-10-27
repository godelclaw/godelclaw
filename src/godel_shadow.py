"""Full-path Gödel qualification against a disposable effect world.

Each model response is parsed by the selected live evaluator and executed by
the same ``run-command-batch-once`` predicate as a live cognitive turn.  Only
the final effect provider is replaced.  This makes historical-failure replay
useful as an agent test rather than merely a test of a parallel JSON
controller.  Set ``GODEL_SHADOW_ENGINE=cetta`` to cross the real
``cetta --lang petta`` boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from typing import Any

import task_phase
from tmux_qualification import IsolatedTmux


ROOT = Path(__file__).resolve().parents[1]
LAUNCH_COMMAND = (
    "python3 -u -c \"input('TRUST_PROMPT>'); "
    "print('SERVER_SELECTION>'); input(); print('CLAUDE_IDLE')\""
)


class FullShadow:
    """One isolated world plus the real MeTTa parse/dispatch boundary."""

    def __init__(self, auto_stop_after_first_effect: bool = False,
                 backend_selector: str | None = "tmux-shadow",
                 prompt_timeout: float = 5.0,
                 engine: str | None = None,
                 channel: str = "telegram"):
        self.engine = str(
            engine or os.environ.get("GODEL_SHADOW_ENGINE", "petta")
        ).strip().lower()
        if self.engine not in {"petta", "cetta"}:
            raise ValueError("shadow engine must be petta or cetta")
        self.channel = str(channel).strip().lower()
        if self.channel not in {"telegram", "irc", "mattermost"}:
            raise ValueError("unsupported shadow channel")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="godel-full-shadow-"
        )
        self.directory = Path(self.temporary.name)
        self.state_path = self.directory / "state.json"
        self.lifecycle_path = self.directory / "lifecycle.json"
        self.task_phase_path = self.directory / "task-phase.json"
        self.channel_config_path = self.directory / "channel.metta"
        self.socket_name = "godel-full-shadow-%s" % uuid.uuid4().hex[:12]
        self.world = IsolatedTmux(self.socket_name)
        self.auto_stop = auto_stop_after_first_effect
        self.backend_selector = backend_selector
        self.prompt_timeout = float(prompt_timeout)
        self.started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.stop()

    def start(self) -> None:
        if self.started:
            return
        self.world.start("shadow")
        self.world.run("rename-window", "-t", "shadow:left", "code-log")
        self.world.create_shell_window_after("shadow:code-log", "pleatta")
        self._write_state({
            "version": 1,
            "socket_name": self.socket_name,
            "room_name": "claude-room",
            "launch_command": LAUNCH_COMMAND,
            "phase": "need-create",
            "serial": 0,
            "receipts": {},
            "trace": [],
            "effects": 0,
            "finished": False,
            "operator_epoch": 0,
            "initial_operator_epoch": 0,
            "auto_stop_after_first_effect": self.auto_stop,
            "prompt_timeout": self.prompt_timeout,
        })
        # The shadow's own operator latch grants cognition, so no host latch
        # decides whether its commands run.
        self.lifecycle_path.write_text(
            json.dumps({"schema": 1, "state": "running"}) + "\n",
            encoding="utf-8",
        )
        # Qualify the same literal specialization renderer as initialize.sh,
        # while keeping the disposable shadow independent of operator config.
        rendered_channel = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_channel_config.py"),
             self.channel],
            check=True,
            capture_output=True,
            text=True,
        )
        self.channel_config_path.write_text(
            rendered_channel.stdout, encoding="utf-8"
        )
        task_phase.initialize(
            "tmux-room-qualification",
            ("need-create", "need-launch", "need-trust", "need-server",
             "goal-satisfied"),
            evidence_revision="turn:0:effects:0",
            path=self.task_phase_path,
        )
        task_phase.transition(
            "need-create", "active", "turn:0:effects:0",
            path=self.task_phase_path,
        )
        self.started = True

    def stop(self) -> None:
        if self.started:
            self.world.stop()
            self.started = False
        self.temporary.cleanup()

    def _write_state(self, value: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def environment(self) -> dict[str, str]:
        # No METTACLAW_* setting of the caller reaches the shadow (a run from
        # an agent's shell would otherwise act on that agent's latch, ledger
        # and stores), and every default state path falls inside the shadow.
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith("METTACLAW_")
        }
        python_paths = [str(ROOT / "src"), str(ROOT / "channels")]
        library = ROOT / "repos" / "petta_lib_chromadb"
        if library.is_dir():
            python_paths.append(str(library))
        environment.update({
            "PYTHONPATH": os.pathsep.join(python_paths),
            "XDG_STATE_HOME": str(self.directory / "state"),
            "METTACLAW_LIFECYCLE_PATH": str(self.lifecycle_path),
            "METTACLAW_SKIP_INITIALIZE": "1",
            "METTACLAW_ENGINE": self.engine,
            "METTACLAW_EFFECT_BACKEND_STATE": str(self.state_path),
            "METTACLAW_ENGINE_STATE_PATH": str(
                self.directory / "engine-selection"
            ),
            "METTACLAW_HISTORY_PATH": str(self.directory / "history.metta"),
            "METTACLAW_WORKING_SET_PATH": str(
                self.directory / "working-set.json"
            ),
            "METTACLAW_LOOP_MODE_PATH": str(self.directory / "loop-mode.json"),
            "METTACLAW_FUEL_MODE_PATH": str(self.directory / "fuel-mode.json"),
            "METTACLAW_ENERGY_PATH": str(self.directory / "energy.json"),
            "METTACLAW_PINS_PATH": str(self.directory / "pins.txt"),
            "METTACLAW_GOALS_PATH": str(self.directory / "goals.metta"),
            "METTACLAW_TELEGRAM_OFFSET_PATH": str(
                self.directory / "telegram-offset"
            ),
            "METTACLAW_TELEGRAM_LOG_PATH": str(
                self.directory / "telegram-updates.jsonl"
            ),
            "METTACLAW_EFFECT_RECEIPT_PATH": str(
                self.directory / "effect-receipts.jsonl"
            ),
            "METTACLAW_STIMULUS_FRONTIER_PATH": str(
                self.directory / "stimulus-frontier"
            ),
            "METTACLAW_TASK_PHASE_PATH": str(self.task_phase_path),
        })
        if self.backend_selector is None:
            environment.pop("METTACLAW_EFFECT_BACKEND", None)
        else:
            environment["METTACLAW_EFFECT_BACKEND"] = self.backend_selector
        if self.engine == "cetta":
            environment["CETTA_BIN"] = os.environ.get(
                "CETTA_BIN",
                str(Path.home() / "repos" / "CeTTa-runtime" / "cetta"),
            )
        python_environment = Path(os.environ.get(
            "PETTA_PY_ENV", Path.home() / "miniforge3" / "envs" / "petta"
        ))
        if python_environment.is_dir():
            environment["PATH"] = os.pathsep.join((
                str(python_environment / "bin"),
                environment.get("PATH", ""),
            ))
            environment["LD_LIBRARY_PATH"] = os.pathsep.join((
                str(python_environment / "lib"),
                environment.get("LD_LIBRARY_PATH", ""),
            ))
            environment["PYTHONHOME"] = str(python_environment)
            environment["PYTHONNOUSERSITE"] = "1"
        return environment

    def _probe_source(self, response: str, turn: int,
                      batch_limit: int, begin_frontier: bool = True) -> str:
        # This is the live parser expression from cognitiveTurn, with only
        # logging/history removed. The same explicit quote crosses into the
        # same deterministic Prolog dispatcher.
        literal = json.dumps(str(response), ensure_ascii=False)
        frontier = (
            "!(println! (addition-effect-turn-begin %d))\n" % turn
            if begin_frontier else ""
        )
        return (
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./src/utils)\n"
            "!(import! &self ./src/skills)\n"
            "!(import! &self ./src/command_pipeline)\n"
            "!(import! &self ./src/turn_additions)\n"
            "%s"
            "!(let* ("
            "($respi %s) "
            "($resp (addition-balance-response $respi)) "
            "($response (addition-envelope-response $resp)) "
            "($parsed (addition-read-response $response)) "
            "($sexpr (addition-command-sequence $parsed)) "
            "($records (addition-effect-broker %d %d (quote $sexpr)))) "
            "(println! (FULL_SHADOW_RECORDS: $records)))\n"
        ) % (frontier, literal, turn, batch_limit)

    def execute_response(self, response: str, turn: int,
                         batch_limit: int = 5,
                         begin_frontier: bool = True
                         ) -> subprocess.CompletedProcess:
        probe = self.directory / ("turn-%04d.metta" % turn)
        probe.write_text(
            self._probe_source(
                response, turn, batch_limit, begin_frontier=begin_frontier
            ),
            encoding="utf-8",
        )
        if self.engine == "cetta":
            cetta = Path(self.environment()["CETTA_BIN"])
            if not cetta.is_file():
                raise FileNotFoundError("CeTTa executable is unavailable")
            # Match run.sh's live CeTTa launch: the bootstrap imports the
            # whole program, as run.metta does, and the probe takes the place
            # of cetta_run.metta.  Listing the program's modules as well
            # would load every equation twice.  The shadow's own channel
            # configuration follows the program's.
            sources = [
                ROOT / "cetta_bootstrap.metta",
                self.channel_config_path,
                probe,
            ]
            command = [
                str(cetta), "--lang", "petta", "--import-mode",
                "ancestor-walk", *(str(source) for source in sources),
            ]
        else:
            petta_root = Path(os.environ.get(
                "PETTA_ROOT", Path.home() / "repos" / "PeTTa"
            ))
            if not (petta_root / "run.sh").is_file():
                raise FileNotFoundError("PeTTa run.sh is unavailable")
            command = ["bash", str(petta_root / "run.sh"), str(probe)]
        return subprocess.run(
            command,
            cwd=ROOT,
            env=self.environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def world_state_projection(self) -> str:
        state = self.state()
        stop = (
            int(state.get("operator_epoch", 0))
            != int(state.get("initial_operator_epoch", 0))
        )
        return (
            "phase=%s effects=%s operator_stop=%s finished=%s"
            % (state.get("phase"), state.get("effects"),
               str(stop).lower(), str(bool(state.get("finished"))).lower())
        )
