"""The expressly retained FAIR snapshot boundary; never a goal-file loader."""
import json

import strategy_host


def fair_suffix():
    suffix = ""
    if strategy_host.fair_enabled():
        suffix = "\nFAIR_GOALS " + json.dumps(
            strategy_host.goal_snapshot(), ensure_ascii=False, sort_keys=True)
    # Explicit JSON transport preserves NUL and Unicode across py-call.
    return json.dumps(suffix, ensure_ascii=True)
