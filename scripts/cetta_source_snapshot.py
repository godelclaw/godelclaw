"""Reproducible build inputs from a commit, independent of checkout edits."""
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile


def tree(repository, revision):
    result = {}
    raw = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', revision], cwd=repository)
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t', 1)
        mode, kind, identity = header.decode('ascii').split()
        name = name.decode('utf-8')
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError('unsafe source entry')
        if kind != 'blob' or mode not in ('100644', '100755', '120000'):
            raise ValueError('source snapshot requires materialized files, not submodules')
        result[name] = (mode, identity)
    return result


def verify(repository, revision, destination):
    """Check every committed input; generated build outputs may be additional."""
    destination = Path(destination)
    if (destination / '.git').exists():
        raise ValueError('source snapshot must not be a Git checkout')
    for name, (mode, expected) in tree(repository, revision).items():
        path = destination / name
        # A generated parent symlink must never redirect an input read.
        if any(parent.is_symlink() for parent in path.parents if parent != destination.parent):
            raise ValueError('redirected source entry: ' + name)
        if mode == '120000':
            if not path.is_symlink():
                raise ValueError('source symlink changed: ' + name)
            data = str(path.readlink()).encode('utf-8')
        else:
            if path.is_symlink() or not path.is_file():
                raise ValueError('source file missing or redirected: ' + name)
            data = path.read_bytes()
            if bool(path.stat().st_mode & 0o111) != (mode == '100755'):
                raise ValueError('source executable mode changed: ' + name)
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if actual != expected:
            raise ValueError('source input changed: ' + name)


def materialize(repository, revision, destination):
    """Freeze the named commit without reading or changing working files."""
    repository, destination = Path(repository).resolve(), Path(destination).absolute()
    if destination.is_symlink():
        raise ValueError('source snapshot destination must not be a symlink')
    if repository == destination or repository in destination.parents or destination in repository.parents:
        raise ValueError('source snapshot must be separate from the checkout')
    expected = tree(repository, revision)
    if not expected:
        raise ValueError('empty source snapshot')
    if destination.exists():
        verify(repository, revision, destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=destination.name + '-inputs-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        archive, staged = temporary / 'source.tar', temporary / 'source'
        staged.mkdir()
        subprocess.run(['git', 'archive', '--format=tar', '--output', str(archive), revision],
                       cwd=repository, check=True)
        with tarfile.open(archive) as tar:
            tar.extractall(staged, filter='data')
        verify(repository, revision, staged)
        # Exclusive creation refuses even an existing empty directory. Other
        # callers can only reuse it after all committed inputs verify.
        destination.mkdir()
        try:
            for entry in staged.iterdir():
                entry.rename(destination / entry.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination
