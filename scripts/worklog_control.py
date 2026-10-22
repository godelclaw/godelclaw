#!/usr/bin/env python3
"""Toggle the local operator stream. Public notes have a separate policy."""
import argparse
import json
import os
from pathlib import Path
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state",choices=("on","off"))
    parser.add_argument("--dir",type=Path,default=Path(os.environ.get("METTACLAW_STREAM_DIR",
        Path.home()/".local/state/godelclaw/display")))
    args=parser.parse_args();args.dir.mkdir(parents=True,exist_ok=True)
    value={"enabled":args.state=="on","epoch":uuid.uuid4().hex}
    temporary=args.dir/(".control-"+value["epoch"])
    temporary.write_text(json.dumps(value));os.replace(temporary,args.dir/"control.json")
    print("Local stream "+args.state+". Earlier epochs will not be replayed.")


if __name__=="__main__":main()
