#!/usr/bin/env python3
"""Build and apply the configured common runtime without copying agent data.

The existing grouped updater owns turn boundaries, checks, backups and rollback.
This development command supplies its release and receipt paths and can compare
the common selection without reading prompts, memories or credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import update_instances as updater


def selection(config, config_path):
    path = Path(config['selection'])
    if not path.is_absolute():
        path = config_path.parent / path
    value = json.loads(path.read_text())
    names = value.get('files', []) if isinstance(value, dict) else value
    removed = value.get('remove', []) if isinstance(value, dict) else []
    return path, [str(updater.relative(n)) for n in names], [str(updater.relative(n)) for n in removed]


def compare(source, config, names, removed):
    desired = {n: updater.sha(source/n) for n in names}
    if any(value is None for value in desired.values()):
        raise ValueError('a selected runtime source file is missing')
    desired.update({n: None for n in removed})
    return desired, [{
        'name': instance['name'],
        'different': [n for n, value in desired.items()
                      if updater.sha(Path(instance['root'])/n) != value],
    } for instance in config['instances']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=Path.home()/'.config/godelclaw/instances.json')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--status', action='store_true', help='compare shared code without changing anything')
    parser.add_argument('--apply', action='store_true', help='activate after the configured offline checks')
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text())
    source = args.source.resolve()
    selection_path, names, removed = selection(config, config_path)
    hashes, status = compare(source, config, names, removed)
    if args.status:
        print(json.dumps({'instances': status}, indent=2))
        return 0
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    base = Path(config.get('release_dir', config_path.parent/'releases')).expanduser()
    if not base.is_absolute():
        base = config_path.parent/base
    release = base/identity
    scripts = Path(__file__).resolve().parent
    if not release.exists():
        subprocess.run([sys.executable, str(scripts/'build_runtime_release.py'),
                        '--source', str(source), '--selection', str(selection_path),
                        '--output', str(release)], check=True)
        manifest_path = release/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source,
                                capture_output=True, text=True)
        if commit.returncode == 0:
            manifest['source_commit'] = commit.stdout.strip()
        if (source/'CETTA_REF').is_file():
            manifest['engine_revision'] = (source/'CETTA_REF').read_text().strip()
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    receipt = base/('activation-'+str(time.time_ns()))
    command = [sys.executable, str(scripts/'update_instances.py'), '--plan', str(config_path),
               '--release', str(release), '--receipt-dir', str(receipt)]
    if args.apply:
        command.append('--apply')
    subprocess.run(command, check=True)
    print(json.dumps({'receipt': str(receipt), 'applied': args.apply}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
