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

Paths are relative to the workspace or absolute, and no two operations may
target the same file. Every hunk is checked and every new content is staged
beside its target before any target changes, so a failure up to that point
leaves the files as they were. The final renames are not atomic as a group: a
failure among them is reported with what was and was not applied. A hunk's
context is matched exactly, then ignoring trailing whitespace, then ignoring
surrounding whitespace, then also mapping typographic dashes, quotes and
spaces to ASCII, as Codex matches it.
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


# Codex's loosest pass: dashes and minus to "-", single and double quotes to
# ASCII quotes, and non-breaking and typographic spaces to a space.
_TYPOGRAPHIC = str.maketrans(
    {**{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"},
     **{c: "'" for c in "\u2018\u2019\u201a\u201b"},
     **{c: '"' for c in "\u201c\u201d\u201e\u201f"},
     **{c: " " for c in "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                         "\u202f\u205f\u3000"}})


def _find(lines, block, start, eof):
    """The index where block occurs at or after start, trying looser matches."""
    if not block:
        return len(lines) if eof else start
    for normal in (lambda s: s, lambda s: s.rstrip(), lambda s: s.strip(),
                   lambda s: s.strip().translate(_TYPOGRAPHIC)):
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


def _stage(path, text, created):
    """Write text beside path under a fresh name; record directories it creates."""
    missing = [parent for parent in reversed(path.parents) if not parent.exists()]
    for directory in missing:
        directory.mkdir()
        created.append(directory)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o7777)
    except BaseException:
        os.unlink(temporary)
        raise
    return temporary


def _discard(staged, created):
    for temporary in staged:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError:
            pass


def apply(patch, workdir="."):
    """Apply the patch and return a receipt; 'Patch failed: reason' means nothing
    was written, and 'Patch partly applied' names what was and was not applied."""
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
                planned.append(("write", target, "\n".join(operation[2]) + "\n", operation[1]))
                receipt.append("A " + operation[1])
            elif kind == "delete":
                if not target.is_file():
                    raise PatchError("%s does not exist" % operation[1])
                planned.append(("remove", target, None, operation[1]))
                receipt.append("D " + operation[1])
            else:
                if not target.is_file():
                    raise PatchError("%s does not exist" % operation[1])
                text = _updated(operation[1], target.read_text(encoding="utf-8"), operation[3])
                if operation[2]:
                    planned += [("write", resolve(operation[2]), text, operation[2]),
                                ("remove", target, None, operation[1])]
                    receipt.append("M %s -> %s" % (operation[1], operation[2]))
                else:
                    planned.append(("write", target, text, operation[1]))
                    receipt.append("M " + operation[1])
        seen = set()
        for action, target, text, name in planned:
            key = target.resolve()
            if key in seen:
                raise PatchError("multiple operations target %s" % name)
            seen.add(key)
        staged, created = [], []
        try:
            for action, target, text, name in planned:
                staged.append(_stage(target, text, created) if action == "write" else None)
        except BaseException:
            _discard(staged, created)
            raise
        done = []
        for (action, target, text, name), temporary in zip(planned, staged):
            try:
                if action == "write":
                    os.replace(temporary, target)
                else:
                    target.unlink()
            except OSError as error:
                _discard(staged, [])
                rest = [n for _, _, _, n in planned[len(done):]]
                return ("Patch partly applied: %s\nApplied: %s\nNot applied: %s"
                        % (error, ", ".join(done) or "nothing", ", ".join(rest)))
            done.append(name)
        return "Success. Updated the following files:\n" + "\n".join(receipt)
    except (PatchError, OSError, UnicodeError) as error:
        return "Patch failed: " + str(error)
