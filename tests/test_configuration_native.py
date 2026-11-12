"""Native configuration rendering against the independent Python renderer."""
import hermetic_env  # noqa: F401,E402
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'requires a qualified engine')
class NativeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-config-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        shutil.copy2(ROOT / 'config/default.toml', self.root / 'config/default.toml')
        self.local = self.root / 'config/local.toml'
        self.environment = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), HOME=str(self.root),
                                USER='fixture-user', XDG_STATE_HOME=str(self.root / 'state'),
                                METTACLAW_INSTANCE='fixture-instance')

    def render(self, text, native, defaults=True, extra=None):
        self.local.write_text(text)
        default = self.root / 'config/default.toml'
        if not defaults and default.exists(): default.unlink()
        environment = dict(self.environment); environment.update(extra or {})
        if native:
            environment.update(METTACLAW_CONFIG_FILE=str(self.local), METTACLAW_CONFIG_ROOT=str(self.root))
            args = [os.environ['CETTA_BIN'], '--no-results', '--lang', 'petta', '--profile', 'extended',
                    '--import-mode', 'relative', str(ROOT / 'scripts/config_to_env_cetta.metta')]
        else:
            args = [sys.executable, str(ROOT / 'scripts/config_to_env.py'), str(self.local), str(self.root)]
        return subprocess.run(args, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)

    @staticmethod
    def body(text):
        # Only initial generator comments are ignored, never lines of values.
        lines = text.splitlines(keepends=True)
        while lines and lines[0].startswith('#'):lines.pop(0)
        return ''.join(lines)

    def parity(self, text, **kwargs):
        old = self.render(text, False, **kwargs)
        new = self.render(text, True, **kwargs)
        self.assertEqual(new.returncode, old.returncode, new.stderr + old.stderr)
        self.assertEqual(self.body(new.stdout), self.body(old.stdout), new.stderr)
        return new

    def test_defaults_and_empty_local(self):
        self.parity('')

    def test_local_overrides_do_not_erase_other_defaults(self):
        self.parity('[agent]\nsynthetic_model="fixture-model"\nllm_stream=false\n'
                    '[paths]\nstream_dir="$STATE_HOME/streams"\n'
                    '[coding]\nproject_root_markers=[".git", "project.json"]\n'
                    'project_doc_fallback_filenames=["NOTES.md", "é.txt"]\n')

    def test_missing_defaults_keeps_explicit_fallbacks(self):
        self.parity('[agent]\ninput_ack_seconds=0\n', defaults=False)

    def test_expansion_is_one_pass_and_undefined_variables_remain_literal(self):
        self.parity('[paths]\nrepos_dir="$ROOT/${INSTANCE}/$UNDEFINED/${}/$EXTRA/~"\n'
                    'public_work_dir="~/public"\n', extra={'EXTRA': '$ROOT'})

    def test_quotes_newlines_and_shell_looking_text_are_literal(self):
        value = "quote ' and \" $(touch SHOULD_NOT_EXIST) `false`\nline\rreturn λ"
        result = self.parity('[agent]\nsynthetic_model=' + json.dumps(value) + '\n')
        self.assertIn("'\"'\"'", result.stdout)
        self.assertFalse((ROOT / 'SHOULD_NOT_EXIST').exists())

    def test_sender_identities_never_enter_exports(self):
        result = self.parity('[telegram]\noperator_ids=["700001"]\n'
                             'sender_names=["700001:private-name"]\nallowed_chat_ids=["-700003"]\n')
        self.assertNotIn('700001', result.stdout)
        self.assertNotIn('private-name', result.stdout)
        self.assertNotIn('700003', result.stdout)

    def test_invalid_fields_refuse_without_partial_output_or_private_values(self):
        cases = ['llm_timeout_seconds=0', 'llm_timeout_seconds=true', 'llm_timeout_seconds="private-value"',
                 'llm_hard_seconds=1', 'llm_connect_seconds=0', 'input_ack_seconds=-1',
                 'llm_stream="private-value"', 'model_commentary=1', 'public_tool_receipts="private-value"',
                 'recycle_bytes=0.5', 'recycle_bytes=-1']
        for text in cases:
            with self.subTest(text=text):
                result = self.parity('[agent]\n' + text + '\n')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('private-value', result.stderr)

    def test_filename_and_cache_rules_keep_the_old_rejections(self):
        for text in ['[coding]\nproject_root_markers=[".."]\n',
                     '[coding]\nproject_doc_fallback_filenames=["a/b"]\n',
                     '[paths]\ncoding_guidance_dir=false\n',
                     '[context]\nanthropic_cache_ttl="private-value"\n']:
            with self.subTest(text=text):
                self.assertEqual(self.parity(text).returncode, 2)

    def test_unused_nonfinite_metadata_does_not_break_configuration(self):
        self.parity('[unused]\nmeasurement=nan\n')

    def test_temporal_values_retain_the_python_rendering(self):
        for value in ('2026-11-12', '01:02:03.123456', '2026-11-12T01:02:03Z'):
            with self.subTest(value=value):
                self.parity('[agent]\nsynthetic_model=' + value + '\n')

    def test_native_channel_templates_match_the_independent_renderer(self):
        from render_channel_config import render
        for kind in ('telegram', 'irc', 'mattermost', 'untrusted) syntax'):
            environment = dict(self.environment, CETTA_BIN=os.environ['CETTA_BIN'])
            result = subprocess.run(['bash', str(ROOT / 'scripts/render_config_native.sh'),
                                     str(self.root), 'channel', kind], env=environment,
                                    capture_output=True, text=True, timeout=30)
            if kind in ('telegram', 'irc', 'mattermost'):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, render(kind))
            else:
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')

    def publish(self, extra=None):
        environment = dict(self.environment, CETTA_BIN=os.environ['CETTA_BIN'])
        environment.update(extra or {})
        return subprocess.run(['bash', str(ROOT / 'scripts/render_config_native.sh'), str(self.root)],
                              env=environment, capture_output=True, text=True, timeout=30)

    def test_native_publication_is_private_atomic_and_keeps_secrets_separate(self):
        self.local.write_text('[agent]\nsynthetic_model="fixture-model"\n')
        secret = self.root / 'config/secrets.env'
        secret.write_text('PRIVATE_TOKEN=fixture-secret\n')
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.root / '.env'
        expected = self.render(self.local.read_text(), False)
        self.assertEqual(self.body(destination.read_text()), self.body(expected.stdout))
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('fixture-secret', destination.read_text())
        self.assertEqual(secret.read_text(), 'PRIVATE_TOKEN=fixture-secret\n')
        self.assertEqual(list(self.root.glob('.env.native.*')), [])

    def test_failed_validation_retains_the_previously_published_file(self):
        self.local.write_text('[agent]\nllm_timeout_seconds=-1\n')
        destination = self.root / '.env'
        destination.write_text('previous complete environment\n')
        result = self.publish()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(destination.read_text(), 'previous complete environment\n')
        self.assertEqual(list(self.root.glob('.env.native.*')), [])

    def test_publication_reports_configuration_overriding_the_callers_environment(self):
        self.local.write_text('')
        changed = self.publish({'METTACLAW_REASONING_EFFORT': 'high'})
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn('METTACLAW_REASONING_EFFORT', changed.stderr)
        same = self.publish({'METTACLAW_REASONING_EFFORT': 'default'})
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertNotIn('METTACLAW_REASONING_EFFORT', same.stderr)

    def test_concurrent_native_publishers_need_no_python_executable(self):
        self.local.write_text('[agent]\nsynthetic_model="fixture-model"\n')
        forbidden = self.root / 'bin'
        forbidden.mkdir()
        for name in ('python', 'python3'):
            shim = forbidden / name
            shim.write_text('#!/bin/sh\necho forbidden-python >&2\nexit 99\n')
            shim.chmod(0o755)
        environment = dict(self.environment, CETTA_BIN=os.environ['CETTA_BIN'],
                           PATH=str(forbidden) + os.pathsep + self.environment['PATH'])
        processes = [subprocess.Popen(['bash', str(ROOT / 'scripts/render_config_native.sh'), str(self.root)],
                                      env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(4)]
        for process in processes:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, err)
            self.assertNotIn('forbidden-python', out + err)
        self.assertEqual(list(self.root.glob('.env.native.*')), [])
        self.assertIn("SYNTHETIC_MODEL='fixture-model'", (self.root / '.env').read_text())


if __name__ == '__main__':unittest.main()
