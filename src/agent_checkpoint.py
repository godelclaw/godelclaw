"""Access an existing agent checkpoint without restoring its live decisions."""
import os
from pathlib import Path
import observed_strategy


def path():
    path = os.environ.get("METTACLAW_STRATEGY_PATH")
    if not path:
        state = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
        if not state:
            raise RuntimeError("agent checkpoint is not configured")
        path = str(Path(state).parent / "observed-strategy.sqlite3")
    return Path(path)


def ledger():
    return observed_strategy.Ledger(path())
