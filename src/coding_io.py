"""Append-only session files for coding mode: one line per record, no backups."""


def append_line(path, text):
    with open(str(path), "a", encoding="utf-8") as stream:
        stream.write(str(text) + "\n")
    return 1
