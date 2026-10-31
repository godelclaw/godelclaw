"""Local fault-injection tool. It accesses only test-supplied paths."""

import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time


value = json.load(sys.stdin)
mode = sys.argv[1]
if mode == "echo":
    print(json.dumps({"input": value, "arguments": sys.argv[2:]}))
elif mode == "identity":
    # A synthetic test token's digest witnesses binding without disclosing it.
    print(json.dumps({"digest": hashlib.sha256(
        os.environ["TEST_AGENT_TOKEN"].encode()).hexdigest()}))
elif mode == "delay":
    time.sleep(value["seconds"])
    print(json.dumps({"input": value}))
elif mode == "crash":
    os.kill(os.getpid(), signal.SIGKILL)
elif mode == "invalid-json":
    print("not JSON")
elif mode == "large":
    print("x" * value["bytes"])
elif mode == "large-json":
    print(json.dumps({"payload": "x" * value["bytes"]}))
elif mode == "write":
    with Path(value["path"]).open("a") as stream:
        stream.write("effect\n")
        stream.flush()
        os.fsync(stream.fileno())
    if value.get("delay"):
        time.sleep(value["delay"])
    if value.get("fail"):
        sys.exit(2)
    print(json.dumps({"written": True}))
else:
    raise ValueError("unknown test mode")
