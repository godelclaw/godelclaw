"""Read-only bundle validation; fixture tests never execute their mock bundles."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


ENGINE_PIN = "EFFECT_RECEIPTS_ENGINE_SHA256"
MANIFEST_PIN = "EFFECT_RECEIPTS_MANIFEST_SHA256"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, ("duplicate manifest key", key)
        result[key] = value
    return result


def bundle_manifest(root):
    """Prefer the bundle's SHA256SUMS, else its build-manifest.json files map."""
    path = root / "SHA256SUMS"
    if path.exists() or path.is_symlink():
        assert not path.is_symlink(), "manifest symlink"
        raw = path.read_bytes()
        pairs = [line.split(maxsplit=1) for line in raw.decode("ascii").splitlines()]
        assert all(len(pair) == 2 for pair in pairs), "malformed SHA256SUMS"
        listed = unique_object((name, expected) for expected, name in pairs)
    else:
        path = root / "build-manifest.json"
        assert not path.is_symlink(), "manifest symlink"
        raw = path.read_bytes()
        listed = json.loads(raw, object_pairs_hook=unique_object)["files"]
    assert isinstance(listed, dict) and listed, "empty/nonmapping manifest"
    return path.name, raw, listed


def bundle_identity(binary, pins):
    """Validate every listed file and exact library membership; pins are optional."""
    binary = Path(binary)
    assert binary.is_absolute() and not binary.is_symlink(), "executable path/symlink"
    root = binary.resolve(strict=True).parent
    assert binary.is_file(), "executable is not a file"
    manifest_name, raw, listed = bundle_manifest(root)
    manifest_sha = hashlib.sha256(raw).hexdigest()
    executable_sha = digest(binary)
    for key, observed in ((ENGINE_PIN, executable_sha), (MANIFEST_PIN, manifest_sha)):
        expected = pins.get(key, "")
        assert not expected or re.fullmatch(r"[0-9a-fA-F]{64}", expected), ("invalid pin", key)
        assert not expected or observed == expected.lower(), ("pin mismatch", key, expected, observed)
    rows = []
    for name, expected in sorted(listed.items()):
        assert isinstance(name, str) and name and "\\" not in name and "\x00" not in name
        relative = PurePosixPath(name)
        assert not relative.is_absolute() and relative.as_posix() == name, ("unsafe manifest path", name)
        assert not any(part in (".", "..") for part in relative.parts), ("unsafe manifest path", name)
        assert isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected), name
        path = root / name
        # Check every ancestor, not just the final file (a symlinked directory
        # can otherwise redirect a whole listed subtree).
        assert not any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)), name
        assert path.resolve(strict=True).is_relative_to(root) and path.is_file(), name
        observed = digest(path)
        assert observed == expected.lower(), (name, expected, observed)
        rows.append([name, observed])
    assert listed.get(binary.name, "").lower() == executable_sha, "executable missing/mismatched in manifest"
    library = root / "lib"
    assert library.is_dir() and not library.is_symlink(), "missing/symlinked library directory"
    members = list(library.rglob("*"))
    assert not any(p.is_symlink() for p in members), "library symlink"
    observed_library = {p.relative_to(root).as_posix() for p in members if p.is_file()}
    listed_library = {name for name in listed if name.startswith("lib/")}
    assert observed_library == listed_library, ("library membership mismatch", observed_library - listed_library,
                                               listed_library - observed_library)
    return {"binary": str(binary), "manifest_name": manifest_name,
            "manifest_sha256": manifest_sha, "executable_sha256": executable_sha,
            "files": rows, "library_files": sorted(observed_library)}


def assert_same_bundle(start, end):
    for key in ("binary", "manifest_name", "manifest_sha256", "executable_sha256", "files", "library_files"):
        assert start[key] == end[key], ("engine bundle changed during phase", key)
