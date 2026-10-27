"""Offline configuration-only reference; run in an explicitly isolated child."""
import importlib.util
import json
from pathlib import Path
import sys

source, mode = sys.argv[1:]
spec = importlib.util.spec_from_file_location("offline_policy", Path(source))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
try:
    result = policy.configuration(mode)
except (ValueError, TypeError, AttributeError, OSError) as error:
    print(json.dumps({"refused": type(error).__name__}))
else:
    print(json.dumps(result))
