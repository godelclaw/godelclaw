"""Agent-adjustable context targets, calibrated with provider token usage."""
import json
import os
import math
from pathlib import Path


def _path():
    state = os.environ.get('METTACLAW_ENGINE_STATE_PATH')
    if not state:
        state = os.environ.get('METTACLAW_STRATEGY_PATH')
    return Path(state).parent / 'context-policy.json' if state else None


def policy(target=None):
    path = _path()
    value = {'task_tokens': int(os.environ.get('METTACLAW_TASK_CONTEXT_TOKENS', '32768'))}
    if path and path.exists():
        value.update(json.loads(path.read_text()))
    if target is not None:
        value['task_tokens'] = max(1, int(target))
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value))
            temporary.replace(path)
    return value


def _model():
    import synthetic_llm
    return str(synthetic_llm.current_model())

def _ratio():
    path = _path()
    if path:
        calibration = path.with_name('context-token-calibration.json')
        try:
            entry = json.loads(calibration.read_text()).get(_model(), {})
            ratio = float(entry['bytes_per_token'])
            if math.isfinite(ratio) and ratio > 0:
                return ratio
        except (OSError, ValueError, KeyError):
            pass
    # A labelled estimate until the selected provider reports its usage.
    return 2.5


def tokens(text):
    import math
    return math.ceil(len(str(text).encode()) / _ratio())


def available(prefix, capacity):
    target = policy()['task_tokens']
    return target if capacity is None else max(0, min(target, capacity - tokens(prefix) - 128))


def observe(prompt, usage):
    from provider_protocol import cache_observation
    observation = cache_observation(usage)
    count = observation.get('input_tokens') or usage.get('prompt_tokens') or usage.get('input_tokens')
    if not isinstance(count, int) or count <= 0:
        return
    path = _path()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path = path.with_name('context-token-calibration.json')
        temporary = path.with_suffix('.tmp')
        try:
            values = json.loads(path.read_text())
        except (OSError, ValueError):
            values = {}
        ratio = len(str(prompt).encode()) / count
        if ratio <= 0:
            return
        values[_model()] = {'bytes_per_token': ratio, 'input_tokens': count}
        temporary.write_text(json.dumps(values))
        temporary.replace(path)
