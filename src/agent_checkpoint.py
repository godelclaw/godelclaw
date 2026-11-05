"""Access an existing agent checkpoint without restoring its live decisions."""
import os
from pathlib import Path
import observed_strategy


def state_dir():
    """The agent's own state directory: the parent of its engine selection file
    (run.sh: <XDG_STATE_HOME or ~/.local/state>/<instance>), where the checkpoint,
    the modes and the native input ledger live."""
    state = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
    if not state:
        raise RuntimeError("agent checkpoint is not configured")
    return Path(state).parent


def path():
    path = os.environ.get("METTACLAW_STRATEGY_PATH")
    if not path:
        path = str(state_dir() / "observed-strategy.sqlite3")
    return Path(path)


def ledger(*, initialize=True, timeout=10):
    return observed_strategy.Ledger(path(), initialize=initialize, timeout=timeout)
