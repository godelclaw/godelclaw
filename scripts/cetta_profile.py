"""Engine build profiles shared by artifact construction and qualification.

A Python-free declaration is checked against build features; it does not by
itself certify that an application has stopped depending on foreign code.
"""
import re

PROFILES = ('python', 'native-agent')


def build_flags(profile, prolog='auto', chatgpt=False, python_config=None,
                sqlite_cflags=None, sqlite_libs=None):
    if profile not in PROFILES:
        raise ValueError('unknown engine profile')
    native = profile == 'native-agent'
    if native and prolog == '1':
        raise ValueError('native-agent requires the Prolog provider disabled')
    if not native and not python_config:
        raise ValueError('the Python profile requires its SDK configuration')
    flags = ['BUILD=' + ('core' if native else 'python'), 'ENABLE_HTTP=1',
             'ENABLE_DURABLE=1', 'ENABLE_SQLITE=1', 'SQLITE_PROVIDER=system',
             'ENABLE_MANAGED_SYMBOLS=1', 'ENABLE_LIB_PROLOG=' + ('0' if native else prolog),
             'ENABLE_JSON_GSLT=1', 'ENABLE_SIWC=' + str(int(native or chatgpt))]
    if not native:
        flags.append('PYTHON_CONFIG=' + python_config)
    if sqlite_cflags is not None:
        flags.append('SQLITE_CFLAGS=' + sqlite_cflags)
    if sqlite_libs is not None:
        flags.append('SQLITE_LIBS=' + sqlite_libs)
    return flags


def manifest_python_enabled(manifest):
    features = manifest.get('features', [])
    if not isinstance(features, list) or any(not isinstance(f, str) for f in features):
        raise ValueError('invalid bundle feature declaration')
    build = [f.partition('=')[2] for f in features if f.startswith('BUILD=')]
    if len(build) != 1:
        raise ValueError('bundle must declare exactly one build mode')
    enabled = manifest.get('python_enabled', build[0] in ('python', 'main', 'full'))
    if type(enabled) is not bool:
        raise ValueError('python_enabled must be a boolean')
    actual = build[0] in ('python', 'main', 'full')
    if build[0] not in ('core', 'python', 'main', 'full', 'mork', 'pathmap') or enabled != actual:
        raise ValueError('Python declaration contradicts bundle build features')
    if enabled:
        prefix, version = manifest.get('python_prefix'), manifest.get('python_version')
        if not isinstance(prefix, str) or not prefix or not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+', version):
            raise ValueError('Python bundle lacks its interpreter contract')
    elif manifest.get('python_prefix') is not None or manifest.get('python_version') is not None:
        raise ValueError('Python-free bundle must not claim an embedded Python runtime')
    return enabled


def require_native_agent(manifest):
    if manifest_python_enabled(manifest):
        raise ValueError('native-agent qualification requires a Python-free engine')
    expected = {'BUILD=core', 'ENABLE_HTTP=1', 'ENABLE_SIWC=1',
                'ENABLE_MANAGED_SYMBOLS=1', 'ENABLE_LIB_PROLOG=0'}
    if manifest.get('runtime_profile') != 'native-agent' or not expected.issubset(manifest['features']):
        raise ValueError('bundle lacks the complete native-agent feature contract')
    # Reject contradictory repeated flags rather than trusting their order.
    for key in ('BUILD', 'ENABLE_HTTP', 'ENABLE_SIWC', 'ENABLE_MANAGED_SYMBOLS', 'ENABLE_LIB_PROLOG'):
        if sum(f.startswith(key + '=') for f in manifest['features']) != 1:
            raise ValueError('ambiguous native-agent feature: ' + key)


from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def publication_directory(prefix, atomic):
    """A native artifact becomes visible only after all checks complete."""
    prefix = Path(prefix)
    if not atomic:
        yield prefix
        return
    if prefix.is_symlink() or prefix.exists() and (not prefix.is_dir() or any(prefix.iterdir())):
        raise ValueError('native-agent bundles require a fresh destination')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + prefix.name + '-build-', dir=prefix.parent) as folder:
        staged = Path(folder) / 'bundle'
        staged.mkdir()
        yield staged
        # rename refuses a concurrently populated destination directory too.
        if prefix.is_symlink():
            raise ValueError('bundle destination changed during qualification')
        os.replace(staged, prefix)
