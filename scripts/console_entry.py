"""Entry for a console snapshot; invoke with Python -I -S -B."""
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "vendor"), str(root / "files"), str(root / "files/src")]

# Used by the installer without profiles, mail discovery or attachment.
if sys.argv[1:] == ["--check-console-imports"]:
    import prompt_toolkit
    import rich
    from ui.worklog.inlineview.adapter import EventBlocks
    from ui.worklog.console import main
    print("console imports OK")
else:
    runpy.run_path(str(root / "files/scripts/agent_console.py"), run_name="__main__")
