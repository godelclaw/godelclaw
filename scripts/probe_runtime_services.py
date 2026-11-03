#!/usr/bin/env python3
"""Check live transport replies and gateway readiness without consuming input."""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from execution_gateway.client import Client


def telegram_ready(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as peer:
        peer.settimeout(5)
        peer.connect(str(path))
        # NEXT only observes the oldest task. It never claims or acknowledges it.
        peer.sendall(b"CWP1\x01\x00")
        # Receive only the header; MSG_TRUNC reports the complete packet size
        # without copying message contents or imposing a payload size limit.
        data = bytearray(6)
        size, _, _, _ = peer.recvmsg_into([data], 0, socket.MSG_TRUNC)
    if (size < 6 or data[:4] != b"CWP1" or
            size < 6 + data[5] or data[4] not in (64, 65)):
        raise RuntimeError("transport has not returned a valid task/idle response")
    return {"ready": True, "pending": data[4] == 65}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telegram", type=Path, action="append", default=[])
    parser.add_argument("--gateway", nargs=2, action="append", default=[], metavar=("SOCKET", "AGENT"))
    args = parser.parse_args()
    result = {"telegram": [telegram_ready(p) for p in args.telegram], "gateways": []}
    for path, agent in args.gateway:
        tools = Client(path, agent, timeout=5).tools()
        if not isinstance(tools, dict):
            raise RuntimeError("gateway tool discovery returned an invalid response")
        result["gateways"].append({"ready": True, "tools": len(tools)})
    print(json.dumps(result))


if __name__ == "__main__":
    main()
