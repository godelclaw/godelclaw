"""An independent count of a runtime's storage, as the coding estate reports it."""
import os


def allocated_blocks(path):
    """512-byte blocks under path: each entry's own blocks (a hard link once per
    name), descending into directories but never through a symlink below the
    root; unreadable entries count as nothing."""
    try:
        total = os.lstat(path).st_blocks
    except OSError:
        return 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        status = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += status.st_blocks
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            pass
    return total
