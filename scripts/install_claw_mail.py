#!/usr/bin/env python3
"""Install the mail CLI and Claude skill for this account, using this checkout."""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install_link(source, destination, force):
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    if destination.exists() or destination.is_symlink():
        if not force or not destination.is_symlink():
            raise ValueError(f'{destination} exists; keep it or choose another destination')
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source.resolve(), target_is_directory=source.is_dir())
    print(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
    parser.add_argument('--skill-dir', type=Path, default=Path.home() / '.claude/skills')
    parser.add_argument('--no-skill', action='store_true', help='install only the CLI')
    parser.add_argument('--config', type=Path,
                        default=Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'godelclaw/mail.toml')
    parser.add_argument('--socket', help='write this socket to a new per-account config')
    parser.add_argument('--cetta', help='write this engine to a new per-account config')
    parser.add_argument('--agent', help='optional default sender name')
    parser.add_argument('--force-links', action='store_true', help='replace existing symlinks, preserving regular files')
    args = parser.parse_args()
    try:
        config = {key: value for key, value in vars(args).items() if key in ('socket', 'cetta', 'agent') and value}
        if config and args.config.expanduser().exists():
            raise ValueError(f'{args.config} already exists; edit it to update its settings')
        install_link(ROOT / 'scripts/shared_mail.py', args.bin_dir.expanduser() / 'claw-mail', args.force_links)
        if not args.no_skill:
            install_link(ROOT / '.claude/skills/claw-mail', args.skill_dir.expanduser() / 'claw-mail', args.force_links)
        if config:
            path = args.config.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                for key, value in config.items():
                    stream.write(f'{key} = {json.dumps(value, ensure_ascii=False)}\n')
            print(path)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
