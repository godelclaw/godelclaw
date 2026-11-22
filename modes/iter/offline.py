"""Prepare isolated Iter state; no account settings or live channels are read."""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent


def prepare(root, scenario):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.json").write_text(json.dumps(scenario))
    shutil.copy2(HERE / "upstream/prompt.txt", root)
    tools = root / "tools"
    tools.mkdir()
    shutil.copy2(HERE / "upstream/tools/nop.py", tools)
    (tools / "echo.py").write_text('DESCRIPTION = "Fixture echo"\ndef run(text): return text\n')
    (tools / "raise_error.py").write_text('DESCRIPTION = "Fixture failure"\ndef run(): raise ValueError("fixture tool failure")\n')
    (root / "memory").mkdir()
    (root / "skills").mkdir()
    (root / "transformations").mkdir()
    for name, text in scenario.get("files", {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if "experience" in scenario:
        (root / "experience.json").write_text(json.dumps(scenario["experience"]))
    core = (HERE / "core.metta").read_text()
    core = core.replace("./host_cetta.metta", str(HERE / "fixture_host_cetta.metta"))
    core = core.replace("../../src/native_llm.metta", str(HERE.parents[1] / "src/native_llm.metta"))
    (root / "core-offline.metta").write_text(core)
    entry = root / "run.metta"
    entry.write_text(f'!(import! &self "{root}/core-offline.metta")\n!(iter:start)\n')
    return entry
