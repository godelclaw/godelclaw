"""Python-free qualification must not depend on a Python SDK or a label alone."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import cetta_profile
import test_native as runner
import build_cetta


class NativeBundleProfileTests(unittest.TestCase):
    def manifest(self, profile='native-agent'):
        return dict(schema=1, revision='a' * 40, runtime_profile=profile,
                    features=cetta_profile.build_flags(profile, python_config='/unused/python-config'),
                    python_enabled=profile == 'python',
                    python_prefix=sys.prefix if profile == 'python' else None,
                    python_version='%d.%d' % sys.version_info[:2] if profile == 'python' else None,
                    files={})

    def test_core_profile_needs_no_sdk_and_preserves_native_auth_and_storage(self):
        flags = cetta_profile.build_flags('native-agent')
        self.assertIn('BUILD=core', flags)
        self.assertIn('ENABLE_SIWC=1', flags)
        self.assertIn('ENABLE_HTTP=1', flags)
        self.assertIn('ENABLE_LIB_PROLOG=0', flags)
        self.assertFalse(any(f.startswith('PYTHON_CONFIG=') for f in flags))
        with self.assertRaisesRegex(ValueError, 'Prolog'):
            cetta_profile.build_flags('native-agent', prolog='1')

    def test_legacy_python_manifests_remain_valid(self):
        manifest = self.manifest('python')
        del manifest['runtime_profile'], manifest['python_enabled']
        self.assertTrue(cetta_profile.manifest_python_enabled(manifest))

    def test_native_label_cannot_mask_python_features_or_missing_native_auth(self):
        for mutate in [lambda m: m.update(features=['BUILD=python']),
                       lambda m: m.update(python_prefix='/unwanted/sdk'),
                       lambda m: m['features'].remove('ENABLE_SIWC=1'),
                       lambda m: m['features'].append('ENABLE_HTTP=0')]:
            manifest = self.manifest(); mutate(manifest)
            with self.assertRaises(ValueError):
                cetta_profile.require_native_agent(manifest)

    def test_native_runner_keeps_independent_test_python_and_checks_real_file_hashes(self):
        with tempfile.TemporaryDirectory(prefix='native-profile-') as directory:
            root = Path(directory); manifest = self.manifest()
            for name in ('cetta', 'cetta-value-worker', 'cetta-space-service', 'cetta-telegram-service'):
                path = root / name; path.write_bytes(b'#!/bin/sh\nexit 0\n'); path.chmod(0o755)
                manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            (root / 'build-manifest.json').write_text(json.dumps(manifest))
            with patch.dict(os.environ, {'CETTA_SOURCE': ''}, clear=True), \
                 patch.object(os, 'execv', side_effect=AssertionError('must not re-exec into a nonexistent SDK')):
                environment, _ = runner.bundle_environment(root / 'cetta', require_core=True)
            self.assertNotIn('PETTA_PY_ENV', environment)
            self.assertEqual(environment['CETTA_BIN'], str(root / 'cetta'))
            (root / 'cetta-value-worker').write_bytes(b'changed artifact')
            with self.assertRaisesRegex(ValueError, 'differs from its manifest'):
                runner.bundle_environment(root / 'cetta', require_core=True)

    def test_native_builder_never_queries_python_config(self):
        with tempfile.TemporaryDirectory(prefix='native-build-') as directory:
            root = Path(directory); source = root / 'source'; source.mkdir(); (source / '.git').mkdir()
            prefix = root / 'bundle'; prefix.mkdir(); (source / 'lib').mkdir(); (source / 'tools').mkdir()
            (source / 'docs/siwc/licenses').mkdir(parents=True)
            commands = []
            def command(argv, cwd=None, capture=False):
                commands.append(argv)
                if argv[:3] == ['git', 'rev-parse', 'HEAD']: return 'a' * 40 + '\n'
                if argv[0] == 'make': return ''
                raise AssertionError('unexpected tool, possibly a Python SDK query: ' + repr(argv))
            def installed(_source, destination, _artifacts):
                for name in build_cetta.RUNTIME_NAMES:
                    (destination / name).write_bytes(b'qualified fixture')
            arguments = ['build_cetta', '--profile', 'native-agent', '--source', str(source),
                         '--prefix', str(prefix), '--ref', 'a' * 40]
            with patch.object(sys, 'argv', arguments), \
                 patch.object(build_cetta, 'python_config', side_effect=AssertionError('SDK query')), \
                 patch.object(build_cetta, 'run', side_effect=command), \
                 patch.object(build_cetta.subprocess, 'run') as external, \
                 patch.object(build_cetta, 'install_runtime_artifacts', side_effect=installed), \
                 patch.object(build_cetta, 'verify_sqlite_feature'), \
                 patch.object(build_cetta, 'verify_durable_feature'):
                external.return_value.returncode = 0
                build_cetta.main()
            manifest = json.loads((prefix / 'build-manifest.json').read_text())
            cetta_profile.require_native_agent(manifest)
            self.assertIsNone(manifest['python_prefix'])
            self.assertTrue(all(not any('python-config' in str(a) for a in cmd) for cmd in commands))

    def test_failed_native_qualification_never_publishes_a_partial_bundle(self):
        with tempfile.TemporaryDirectory(prefix='native-publish-') as directory:
            root = Path(directory); destination = root / 'release'
            with self.assertRaisesRegex(RuntimeError, 'qualification'):
                with cetta_profile.publication_directory(destination, True) as staged:
                    (staged / 'cetta').write_bytes(b'not yet qualified')
                    self.assertFalse(destination.exists())
                    raise RuntimeError('qualification failed')
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_existing_or_concurrently_published_artifact_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix='native-publish-') as directory:
            root = Path(directory); destination = root / 'release'; destination.mkdir()
            marker = destination / 'cetta'; marker.write_bytes(b'previous release')
            with self.assertRaisesRegex(ValueError, 'fresh destination'):
                with cetta_profile.publication_directory(destination, True):
                    self.fail('must not enter an existing artifact')
            self.assertEqual(marker.read_bytes(), b'previous release')
            other = root / 'concurrent'
            with self.assertRaises(OSError):
                with cetta_profile.publication_directory(other, True) as staged:
                    (staged / 'cetta').write_bytes(b'candidate')
                    other.mkdir(); (other / 'cetta').write_bytes(b'concurrent release')
            self.assertEqual((other / 'cetta').read_bytes(), b'concurrent release')


if __name__ == '__main__':
    unittest.main()
