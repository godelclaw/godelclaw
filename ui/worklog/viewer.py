"""Local working display over typed observations; never imports the live agent."""
import argparse
import json
import os
import re
from pathlib import Path
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stream_sink import Projection, control


def clean(text):
    # Rich handles layout; provider/tool text never controls the terminal.
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)", "", str(text))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]?", "", text)
    # A progress carriage return replaces that display line. Raw bytes stay in
    # the execution artifact; they cannot emit cursor motion to this terminal.
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    return "".join(c if c in "\n\t" or not unicodedata.category(c).startswith("C") else " " for c in text)


def headline(text, width=120):
    text=clean(text).replace("\n"," ")
    return text if len(text)<=width else text[:width-1]+"…"


class Reader:
    def __init__(self, root):
        self.root = Path(root)
        self.offsets = {}

    def read(self):
        output = []
        enabled, epoch = control(self.root)
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                stat = path.stat()
                inode, offset = self.offsets.get(path.name, (stat.st_ino, 0))
                if inode != stat.st_ino or offset > stat.st_size:
                    offset = 0
                if offset == stat.st_size:
                    continue
                with path.open("rb") as stream:
                    stream.seek(offset)
                    for _ in range(128):
                        line = stream.readline(16777217)
                        if not line or not line.endswith(b"\n"):
                            break
                        offset = stream.tell()
                        try:
                            event = json.loads(line)
                            if isinstance(event, dict) and enabled and event.get("epoch", "initial")==epoch:
                                output.append(event)
                        except (ValueError, UnicodeError):
                            pass
                self.offsets[path.name] = (stat.st_ino, offset)
            except OSError:
                continue
        # Preserve each producer's sequence on reconnect, including its reserved
        # terminal slots. Wall time only orders independent producers for display.
        return sorted(output, key=lambda e: (e.get("time", 0), e.get("producer", ""), e.get("sequence", 0)))


def lines(projection, selected=None, full=False):
    result = []
    sessions = sorted(projection.sessions.items(), key=lambda p: p[1]["updated"])
    for identity, session in sessions:
        if selected and identity != selected and session.get("parent") != selected:
            continue
        result += [(clean(session["title"]) if full else headline(session["title"])) + "  [" + session["status"] + "]" +
                   (" · " + clean(session["model"]) if session.get("model") else "")]
        if session.get("parent"):
            result.append("  Parent: " + clean(session["parent"]))
        if session.get("plan"):
            try:
                plan = json.loads(session["plan"])
                result.extend("  " + clean(p["step"]) + " [" + p["status"] + "]" for p in plan.get("plan", []))
            except (ValueError, KeyError, TypeError):
                result.append("  Plan: " + clean(session["plan"]))
        for key, item in list(session["items"].items())[-18:]:
            kind = item["kind"]
            if kind in ("attempt.open", "attempt.finished"):
                continue
            channel = item.get("channel")
            title = ("Reasoning summary" if channel == "reasoning-summary" else
                     session.get("instance", "Agent") if channel == "commentary" else
                     "$ " + headline(item["command"]) if item.get("command") else
                     item.get("name") or kind.replace(".", " "))
            status = item.get("status", "")
            result.append("  " + title + ("  [" + status + "]" if status else ""))
            text = clean(item.get("text", ""))
            visible = text.splitlines() if full else text.splitlines()[-12:]
            if len(text.splitlines()) > 12:
                result.append("    … earlier output remains in the session artifacts")
            result.extend("    " + line for line in visible)
            if item.get("artifact"):
                result.append("    Output: " + clean(item["artifact"]))
        if session.get("gap"):
            result.append("  Display missed updates; final records remain authoritative.")
        if session.get("report"):
            result.append("  Report: " + clean(session["report"]))
        result.append("")
    return result or ["Waiting for agent or coding-session output…"]


def render_items(session, items, cache):
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.text import Text
    rendered=[]
    for key,item in items:
        if item["kind"] in ("attempt.open","attempt.finished"): continue
        channel=item.get("channel","")
        title=("Reasoning summary" if channel=="reasoning-summary" else session.get("instance","Agent")
               if channel=="commentary" else "$ "+headline(item["command"]) if item.get("command")
               else item.get("name") or item["kind"].replace("."," "))
        text=clean(item.get("text",""))
        stamp=(key,item.get("updated"),item.get("status"),text)
        if stamp not in cache:
            heading=Text(title,style="bold cyan" if channel else "bold")
            heading.append("  "+item.get("status",""),style="dim")
            body=Markdown(text) if channel else Text("\n".join(text.splitlines()[-10:]))
            cache[stamp]=Group(heading,body,Text(""))
            while len(cache)>128:del cache[next(iter(cache))]
        rendered.append(cache[stamp])
    return Group(*rendered)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path(os.environ.get(
        "METTACLAW_STREAM_DIR", Path.home()/".local/state/godelclaw/display")))
    parser.add_argument("--session")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true", help="append typed events")
    parser.add_argument("--interval", type=float, default=.15)
    parser.add_argument("--full", action="store_true", help="show complete retained item text")
    args = parser.parse_args(argv)
    reader, projection = Reader(args.dir), Projection(tail_chars=16777216 if args.full else 12000)
    previous_epoch = control(args.dir)
    plain = not sys.stdout.isatty() and not args.once and not args.json
    plain_key = None
    def update():
        nonlocal projection, previous_epoch, plain_key
        epoch = control(args.dir)
        epoch_changed = epoch != previous_epoch
        if epoch_changed:
            projection = Projection(tail_chars=16777216 if args.full else 12000)
            previous_epoch = epoch
        records = reader.read()
        for record in records:
            projection.apply(record)
            if args.json:
                print(json.dumps(record, ensure_ascii=False), flush=True)
            elif plain and (not args.session or record.get("session")==args.session or record.get("parent_session")==args.session):
                key=(record.get("session"),record.get("item"),record.get("channel"))
                if key!=plain_key:
                    print("\n"+clean(record.get("session",""))+" · "+clean(record.get("channel") or record.get("name") or record["kind"]),flush=True)
                    plain_key=key
                if record["kind"] in ("text.delta","tool.output"):
                    print(clean(record.get("text","")),end="",flush=True)
                elif record["kind"] in ("tool.finished","session.finished","gap"):
                    print("\n"+clean(record.get("status",""))+" "+clean(record.get("text","")),flush=True)
        return projection.refresh_owners() or bool(records) or epoch_changed
    update()
    if args.once:
        if not args.json: print("\n".join(lines(projection, args.session, args.full)))
        return 0
    if args.json or not sys.stdout.isatty():
        while True:
            update()
            time.sleep(max(.05, args.interval))
    from rich.console import Console, Group
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    console = Console()
    cache={}
    def render():
        sessions=[(sid,s) for sid,s in projection.sessions.items() if not args.session or sid==args.session or s.get("parent")==args.session]
        sessions.sort(key=lambda pair:pair[1]["updated"])
        groups=[]
        for sid,session in sessions[-3:]:
            header=Text(headline(session["title"])+"  ["+session["status"]+"]",style="bold green")
            if session.get("model"): header.append(" · "+clean(session["model"]),style="dim")
            plans=[]
            try:
                plans=[Text(clean(p["step"])+" ["+p["status"]+"]",style="dim") for p in json.loads(session.get("plan","{}" )).get("plan",[])]
            except (ValueError,TypeError,KeyError):pass
            items=list(session["items"].items())[-6:]
            groups.append(Group(header,*plans,render_items(session,items,cache)))
        return Panel(Group(*groups) if groups else Text("Waiting for agent or coding-session output…"),
                     title="Agent work",subtitle="Reasoning summaries · commentary · tool evidence")
    last_size=console.size
    with Live(render(), console=console, auto_refresh=False, vertical_overflow="crop") as live:
        while True:
            changed=update()
            if changed or console.size!=last_size:
                live.update(render(), refresh=True)
                last_size=console.size
            time.sleep(max(.05, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
