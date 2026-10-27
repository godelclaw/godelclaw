# Terminal channel of standalone Omega launches (runtime_host.terminal).
# From patham9/iter f4064d97849ecaccac7939315a3f1a68de15c3ef channels/terminal.py
# (MIT, Copyright (c) 2026 Patrick Hammer; see iter/upstream/LICENSE).
import select
import sys

def receive():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip()
    return ""

def send(content):
    print(content)
