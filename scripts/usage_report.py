#!/usr/bin/env python3
"""Export measured provider usage without opening a ledger for writing."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import usage_history


def timestamp(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("include a timezone, for example 2026-10-24T00:00:00Z")
    return value.timestamp()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--since", type=timestamp, default=0)
    parser.add_argument("--until", type=timestamp)
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args()
    print(json.dumps(usage_history.report(args.ledger, since=args.since,
          until=args.until, zone=args.timezone), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
