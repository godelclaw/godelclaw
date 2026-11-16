"""Build a Unicode-15 casefold delta over CeTTa's existing scalar lowercase."""
import json
from pathlib import Path
import sys
import unicodedata

VERSION = "15.0.0"

def main():
    if unicodedata.unidata_version != VERSION:
        raise SystemExit("generator requires Unicode " + VERSION)
    destination = Path(sys.argv[1])
    rows = []
    for point in range(0x110000):
        text = chr(point)
        if text.casefold() != text.lower():
            rows.append("(UnicodeFoldDeltaV1 %d %s)" %
                        (point, json.dumps(text.casefold(), ensure_ascii=True)))
    destination.write_text(
        "; Generated Unicode %s full-casefold differences from scalar lowercase.\n" % VERSION
        + "\n".join(rows) + "\n", encoding="utf-8")
    print("%d casefold differences; %d bytes" %
          (len(rows), destination.stat().st_size))

if __name__ == "__main__":
    main()
