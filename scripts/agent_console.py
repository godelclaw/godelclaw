#!/usr/bin/env python3
"""Attach to an existing agent's terminal console."""
from pathlib import Path
import runpy
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "ui/worklog/console.py"), run_name="__main__")
