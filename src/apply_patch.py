"""Codex-format patches for coding mode.

A patch is

    *** Begin Patch
    *** Add File: path          then lines starting with +
    *** Delete File: path
    *** Update File: path       optionally *** Move to: path, then hunks:
    @@ optional context line
     context line
    -removed line
    +added line
    *** End of File             optional: the hunk ends at the end of the file
    *** End Patch

Paths are relative to the workspace or absolute. Every file is checked before
any is written, so a patch applies whole or not at all. A hunk's context is
matched exactly, then ignoring trailing whitespace, then ignoring surrounding
whitespace, as Codex matches it.
"""
import os
from pathlib import Path
import tempfile


class PatchError(ValueError):
    pass


def _parse(text):
    lines = str(text).replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PatchError("a patch starts with *** Begin Patch")
    if lines[-1].strip() != "*** End Patch":
        raise PatchError("a patch ends with *** End Patch")
    body, operations, index = lines[1:-1], [], 0
    while index < len(body):
        line = body[index]
        if line.startswith("*** Add File: "):
            path, index, added = line[len("*** Add File: "):].strip(), index + 1, []
            while index < len(body) and not body[index].startswith("*** "):
                if not body[index].startswith("+"):
                    raise PatchError("line %d: an added file's lines start with +" % (index + 2))
                added.append(body[index][1:])
                index += 1
            operations.append(("add", path, added))
        elif line.startswith("*** Delete File: "):
            operations.append(("delete", line[len("*** Delete File: "):].strip()))
            index += 1
        elif line.startswith("*** Update File: "):
            path, index, move, hunks = line[len("*** Update File: "):].strip(), index + 1, None, []
            if index < len(body) and body[index].startswith("*** Move to: "):
                move, index = body[index][len("*** Move to: "):].strip(), index + 1
            while index < len(body) and (not body[index].startswith("*** ")
                                         or body[index] == "*** End of File"):
                row = body[index]
                if row.startswith("@@"):
                    hunks.append({"context": row[2:].strip(), "lines": [], "eof": False})
                elif row == "*** End of File":
                    if not hunks:
                        raise PatchError("line %d: *** End of File outside a hunk" % (index + 2))
                    hunks[-1]["eof"] = True
                else:
                    if not hunks:
                        hunks.append({"context": "", "lines": [], "eof": False})
                    if row and row[0] not in " +-":
                        raise PatchError("line %d: hunk lines start with space, + or -" % (index + 2))
                    hunks[-1]["lines"].append(row if row else " ")
                index += 1
            if not hunks and move is None:
                raise PatchError("update of %s has no hunks" % path)
            operations.append(("update", path, move, hunks))
        else:
            raise PatchError("line %d: unexpected %r" % (index + 2, line[:80]))
    if not operations:
        raise PatchError("the patch changes nothing")
    return operations


def _find(lines, block, start, eof):
    """The index where block occurs at or after start, trying looser matches."""
    if not block:
        return len(lines) if eof else start
    for normal in (lambda s: s, lambda s: s.rstrip(), lambda s: s.strip()):
        wanted = [normal(x) for x in block]
        last = len(lines) - len(block)
        candidates = [last] if eof else range(start, last + 1)
        for at in candidates:
            if at >= start and [normal(x) for x in lines[at:at + len(block)]] == wanted:
                return at
    return None


def _updated(path, text, hunks):
    trailing = text.endswith("\n")
    lines = text.split("\n")
    if trailing:
        lines.pop()
    position = 0
    for number, hunk in enumerate(hunks, 1):
        if hunk["context"]:
            anchor = _find(lines, [hunk["context"]], position, False)
            if anchor is None:
                raise PatchError("%s hunk %d: context %r not found" % (path, number, hunk["context"]))
            position = anchor
        old = [row[1:] for row in hunk["lines"] if row[0] in " -"]
        at = _find(lines, old, position, hunk["eof"])
        if at is None:
            raise PatchError("%s hunk %d: the lines to change were not found" % (path, number))
        # Context lines keep the file's own text when the match was loose.
        new, original = [], at
        for row in hunk["lines"]:
            if row[0] == " ":
                new.append(lines[original]); original += 1
            elif row[0] == "-":
                original += 1
            else:
                new.append(row[1:])
        lines[at:at + len(old)] = new
        position = at + len(new)
    return "\n".join(lines) + ("\n" if trailing or not lines else "")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    if path.exists():
        os.chmod(temporary, path.stat().st_mode & 0o7777)
    os.replace(temporary, path)


def apply(patch, workdir="."):
    """Apply the patch; return a receipt, or 'Patch failed: reason' with nothing written."""
    try:
        base = Path(str(workdir)).expanduser()
        resolve = lambda name: (Path(name).expanduser() if os.path.isabs(os.path.expanduser(name))
                                else base / name)
        planned, receipt = [], []
        for operation in _parse(patch):
            kind, target = operation[0], resolve(operation[1])
            if kind == "add":
                if target.exists():
                    raise PatchError("%s already exists" % operation[1])
                planned.append(("write", target, "\n".join(operation[2]) + "\n"))
                receipt.append("A " + operation[1])
            elif kind == "delete":
                if not target.is_file():
                    raise PatchError("%s does not exist" % operation[1])
                planned.append(("remove", target, None))
                receipt.append("D " + operation[1])
            else:
                if not target.is_file():
                    raise PatchError("%s does not exist" % operation[1])
                text = _updated(operation[1], target.read_text(encoding="utf-8"), operation[3])
                if operation[2]:
                    planned += [("write", resolve(operation[2]), text), ("remove", target, None)]
                    receipt.append("M %s -> %s" % (operation[1], operation[2]))
                else:
                    planned.append(("write", target, text))
                    receipt.append("M " + operation[1])
        for action, target, text in planned:
            if action == "write":
                _write(target, text)
            else:
                target.unlink()
        return "Success. Updated the following files:\n" + "\n".join(receipt)
    except (PatchError, OSError, UnicodeDecodeError) as error:
        return "Patch failed: " + str(error)
