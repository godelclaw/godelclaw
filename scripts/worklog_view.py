#!/usr/bin/env python3
"""Portable entrypoint for the local agent/coding work display."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "ui/worklog/viewer.py"),
                  run_name="__main__")
