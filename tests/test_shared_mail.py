"""CLI round trips against the real service, with a staged member account."""
import json
from isolated_env import isolated_environ  # noqa: E402
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/shared_mail.py'
BINARY = Path(os.environ.get('CETTA_BIN', '/nonexistent/cetta')).resolve()
SERVICE = BINARY.parent / 'cetta-space-service'
LOGIN = pwd.getpwuid(os.getuid()).pw_name


@unittest.skipUnless(BINARY.is_file() and SERVICE.is_file(), 'provide CETTA_BIN in a native release bundle')
class SharedMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shared-mail-test-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.socket = self.work / 'service.sock'
        self.service = None
        self.addCleanup(self.stop)
        self.start()
        self.raw('''!(let $space (mailbox:open SOCKET "agent-mail")
 (space:grant SOCKET $space (account UID) (rights append)
  (bundle (MailboxMessage 1 $id $to "" (from (caller) $agent) $body)
          (MailboxPending 1 $to "" (from (caller) $agent) $id))
  (MailboxMessage 1 $id $c $q (from (caller) $a) $b)))
!(let $space (mailbox:open SOCKET INBOX)
 (space:grant SOCKET $space (account UID) (rights write) none none))
''')
        self.stop()
        self.start(member=True)

    def start(self, member=False):
        command = [str(SERVICE), '--socket', str(self.socket), '--data', str(self.work / 'store')]
        if member:
            command += ['--owner-uid', str(65534 if os.getuid() != 65534 else 65533)]
        self.service = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not self.socket.exists():
            if self.service.poll() is not None or time.monotonic() > deadline:
                self.fail('fixture did not start')
            time.sleep(.01)

    def stop(self):
        if self.service:
            self.service.terminate()
            _, error = self.service.communicate(timeout=10)
            self.assertEqual(self.service.returncode, 0, error.decode())
            self.service = None
        self.socket.unlink(missing_ok=True)

    def raw(self, code):
        q = lambda x: json.dumps(str(x), ensure_ascii=False)
        path = self.work / 'fixture.metta'
        path.write_text('!(import! &self mailbox)\n!(import! &self str)\n' + code.replace('SOCKET', q(self.socket))
                        .replace('INBOX', q('mail@' + LOGIN)).replace('UID', str(os.getuid())))
        result = subprocess.run([str(BINARY), '--lang', 'petta', '--quiet', str(path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def command(self, *arguments):
        return [sys.executable, str(CLI), '--config', str(self.work / 'no-config.toml'), '--json', '--cetta', str(BINARY), '--socket', str(self.socket), *arguments]

    def run_cli(self, *arguments, env=None, expected=0):
        result = subprocess.run(self.command(*arguments), capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def reply(self, message='reply', ident='reply-1', agent='reviewer'):
        self.raw('!(mailbox:send SOCKET INBOX ' + json.dumps(ident) + ' ' + json.dumps(agent) +
                 ' "fixture-agent" (str:from-codepoints (' + ' '.join(str(ord(c)) for c in message) + ')))\n')

    def test_identity_and_inbox_ignore_environment_login(self):
        env = dict(os.environ, LOGNAME='someone-else', USER='someone-else', USERNAME='someone-else')
        who = self.run_cli('whoami', env=env)
        self.assertEqual((who['uid'], who['account'], who['role']), (os.getuid(), LOGIN, 'member'))
        self.assertEqual(who['inbox'], 'mail@' + LOGIN)
        self.reply()
        self.assertEqual(self.run_cli('next', 'reviewer', env=env)['status'], 'delivery')

    def test_fixed_send_id_duplicate_and_conflicting_body(self):
        args = ('send', 'reviewer', 'fixture-agent', 'hello', '--id', 'one')
        self.assertEqual(self.run_cli(*args), {'status': 'accepted', 'id': 'one'})
        self.assertEqual(self.run_cli(*args), {'status': 'duplicate', 'id': 'one'})
        refused = self.run_cli('send', 'reviewer', 'fixture-agent', 'changed', '--id', 'one', expected=1)
        self.assertIn('id-conflict', refused['reason'])

    def test_ungranted_space_returns_failure_exit(self):
        result = self.run_cli('--space', 'missing', 'send', 'reviewer', 'receiver', 'hello', expected=1)
        self.assertEqual(result['status'], 'error')
        self.assertIn('missing-space', result['reason'])
        self.assertTrue(result['id'])

    def test_owner_retry_after_pickup_does_not_requeue(self):
        self.stop()
        self.start()
        text = 'λ "quoted"\\text\nsecond line'
        args = ('send', 'reviewer', LOGIN + '/fixture-agent', text, '--id', 'owned-one')
        self.assertEqual(self.run_cli(*args)['status'], 'accepted')
        self.assertEqual(self.run_cli('inbox', 'fixture-agent')['count'], 1)
        delivery = self.run_cli('next', 'fixture-agent')
        self.assertEqual((delivery['id'], delivery['text']), ('owned-one', text))
        self.assertEqual(self.run_cli(*args)['status'], 'duplicate')
        self.assertEqual(self.run_cli('ack', 'fixture-agent', 'owned-one')['status'], 'settled')
        self.assertEqual(self.run_cli(*args)['status'], 'duplicate')
        self.assertEqual(self.run_cli('next', 'fixture-agent'), {'status': 'idle'})

    def test_owner_explicit_legacy_space_is_preserved(self):
        self.stop()
        self.start()
        self.run_cli('--space', 'agent-mail', 'send', 'reviewer', 'fixture-agent', 'legacy')
        self.assertIn('mailbox:delivery', self.raw('!(mailbox:next SOCKET "agent-mail" "fixture-agent")'))
        self.assertEqual(self.run_cli('inbox', 'fixture-agent')['count'], 0)

    def test_owner_bare_recipient_uses_the_native_agent_bus(self):
        self.stop()
        self.start()
        self.run_cli('send', 'reviewer', 'fixture-agent', 'native peer')
        self.assertIn('mailbox:delivery', self.raw('!(mailbox:next SOCKET "agent-mail" "fixture-agent")'))
        self.assertEqual(self.run_cli('inbox', 'fixture-agent')['count'], 0)

    def test_delivery_survives_next_restart_and_requires_ack(self):
        text = 'λ "quotes"\n{"status":"accepted"}\n!(println! injected)'
        self.reply(text)
        first = self.run_cli('next', 'reviewer')
        self.assertEqual(first['text'], text)
        self.assertEqual(first['from'], 'fixture-agent')
        self.assertEqual(self.run_cli('next', 'reviewer'), first)
        self.stop()
        self.start(member=True)
        self.assertEqual(self.run_cli('next', 'reviewer'), first)
        self.assertEqual(self.run_cli('ack', 'reviewer', first['id'])['status'], 'settled')
        self.assertEqual(self.run_cli('ack', 'reviewer', first['id'])['status'], 'duplicate')
        self.assertEqual(self.run_cli('next', 'reviewer'), {'status': 'idle'})

    def test_wait_is_bounded_and_does_not_ack(self):
        started = time.monotonic()
        self.assertEqual(self.run_cli('wait', 'reviewer', '--timeout', '.05', expected=3), {'status': 'idle'})
        self.assertLess(time.monotonic() - started, 3)
        waiting = subprocess.Popen(self.command('wait', 'reviewer', '--timeout', '3'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(.1)
            self.reply()
            out, err = waiting.communicate(timeout=6)
            self.assertEqual(waiting.returncode, 0, out + err)
            result = json.loads(out)
            self.assertEqual(result['status'], 'delivery')
            self.assertEqual(self.run_cli('next', 'reviewer'), result)
        finally:
            if waiting.poll() is None:
                waiting.terminate()
                waiting.communicate(timeout=5)

    def test_wait_rechecks_after_another_channel_wakes_it(self):
        waiting = subprocess.Popen(self.command('wait', 'reviewer', '--timeout', '3'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(.1)
            self.reply('someone else', 'other', 'other-agent')
            time.sleep(.1)
            self.reply('mine')
            out, err = waiting.communicate(timeout=6)
            self.assertEqual(waiting.returncode, 0, out + err)
            self.assertEqual(json.loads(out)['text'], 'mine')
        finally:
            if waiting.poll() is None:
                waiting.terminate()
                waiting.communicate(timeout=5)

    def test_disconnected_service_is_an_error(self):
        self.stop()
        result = self.run_cli('next', 'reviewer', expected=1)
        self.assertEqual(result['status'], 'error')


    def test_default_agent_and_explicit_override(self):
        env = dict(os.environ, CLAW_MAIL_AGENT='default-reviewer')
        self.assertEqual(self.run_cli('send', 'fixture-agent', 'hello', env=env)['status'], 'accepted')
        self.reply(agent='default-reviewer')
        self.assertEqual(self.run_cli('next', env=env)['status'], 'delivery')
        self.assertEqual(self.run_cli('inbox', 'someone-else', env=env)['count'], 0)
        self.assertEqual(self.run_cli('ack', 'reply-1', env=env)['status'], 'settled')
        self.assertEqual(self.run_cli('inbox', env=env)['count'], 0)

    def test_inbox_preview_count_and_notice_do_not_take_or_ack(self):
        self.reply('first', 'r1')
        self.reply('second', 'r2')
        preview = self.run_cli('inbox', 'reviewer', '--limit', '1')
        self.assertEqual(preview['count'], 2)
        self.assertTrue(preview['truncated'])
        self.assertEqual(preview['items'][0]['state'], 'pending')
        self.assertEqual(self.run_cli('inbox', 'reviewer', '--limit', '1'), preview)
        count = subprocess.run(self.command('inbox', 'reviewer', '--count'), capture_output=True, text=True, timeout=5)
        self.assertEqual((count.returncode, count.stdout), (0, '2\n'))
        notice = subprocess.run(self.command('inbox', 'reviewer', '--notice'), capture_output=True, text=True, timeout=5)
        self.assertEqual(notice.returncode, 0, notice.stderr)
        self.assertIn('2 unacknowledged', notice.stdout)
        first = self.run_cli('next', 'reviewer')
        preview = self.run_cli('inbox', 'reviewer')
        self.assertEqual(preview['count'], 2)
        self.assertEqual([item['state'] for item in preview['items']], ['ready', 'pending'])
        self.assertEqual(self.run_cli('next', 'reviewer'), first)
        self.run_cli('ack', 'reviewer', first['id'])
        second = self.run_cli('next', 'reviewer')
        self.run_cli('ack', 'reviewer', second['id'])
        notice = subprocess.run(self.command('inbox', 'reviewer', '--notice'), capture_output=True, text=True, timeout=5)
        self.assertEqual((notice.returncode, notice.stdout), (0, ''))

    def test_forever_wait_recovers_from_a_service_restart(self):
        waiting = subprocess.Popen(self.command('wait', 'reviewer', '--forever'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(.12)
            self.stop()
            time.sleep(.12)
            self.start(member=True)
            self.reply('after restart')
            out, err = waiting.communicate(timeout=8)
            self.assertEqual(waiting.returncode, 0, out + err)
            self.assertEqual(json.loads(out)['text'], 'after restart')
            self.assertEqual(self.run_cli('inbox', 'reviewer')['count'], 1)
        finally:
            if waiting.poll() is None:
                waiting.terminate()
                waiting.communicate(timeout=5)

    def test_zero_timeout_waits_until_mail(self):
        waiting = subprocess.Popen(self.command('wait', 'reviewer', '--timeout', '0'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(.1)
            self.assertIsNone(waiting.poll())
            self.reply()
            out, err = waiting.communicate(timeout=5)
            self.assertEqual(waiting.returncode, 0, out + err)
            self.assertEqual(json.loads(out)['status'], 'delivery')
        finally:
            if waiting.poll() is None:
                waiting.terminate()
                waiting.communicate(timeout=5)

    def test_persistent_wait_failure_is_bounded(self):
        self.stop()
        result = self.run_cli('wait', 'reviewer', '--forever', '--retry-limit', '0', expected=1)
        self.assertTrue(result['retryable'])

    def test_summary_matches_json_and_cannot_inject_terminal_controls(self):
        text = 'hello\x1b[31m λ\nsecond line'
        self.reply(text)
        command = [part for part in self.command('next', 'reviewer') if part != '--json']
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        value = json.loads(lines[-1])
        self.assertIn('id=' + value['id'], lines[0])
        self.assertIn('from=' + value['from'], lines[0])
        self.assertIn('hello', lines[0])
        self.assertNotIn('\x1b', lines[0])
        self.assertNotIn('second line', lines[0])
        self.assertEqual(value['text'], text)

    def test_sent_text_roundtrips_control_characters_and_unicode(self):
        text = 'λ "quoted"\\text\n\t\b\f\x1b[31m'
        self.run_cli('--space', 'mail@' + LOGIN, 'send', 'reviewer', 'reviewer', text)
        self.assertEqual(self.run_cli('next', 'reviewer')['text'], text)

    def test_member_retry_after_pickup_is_duplicate(self):
        args = ('send', 'reviewer', 'fixture-agent', 'hello', '--id', 'member-one')
        self.assertEqual(self.run_cli(*args)['status'], 'accepted')
        self.stop()
        self.start()
        self.assertIn('mailbox:delivery', self.raw('!(mailbox:next SOCKET "agent-mail" "fixture-agent")'))
        self.stop()
        self.start(member=True)
        self.assertEqual(self.run_cli(*args)['status'], 'duplicate')
        refused = self.run_cli('send', 'reviewer', 'fixture-agent', 'changed', '--id', 'member-one', expected=1)
        self.assertIn('id-conflict', refused['reason'])

    def test_config_relative_paths_work_from_another_directory_and_a_symlink(self):
        config = self.work / 'mail.toml'
        engine = self.work / 'engine'
        engine.symlink_to(BINARY)
        config.write_text('socket = "service.sock"\ncetta = "./engine"\nagent = "reviewer"\n')
        command = self.work / 'claw-mail'
        command.symlink_to(CLI)
        env = isolated_environ()
        for key in ('CETTA_BIN', 'CETTA_SHARED_MAIL_SOCKET', 'CLAW_MAIL_AGENT'):
            env.pop(key, None)
        result = subprocess.run([sys.executable, str(command), '--config', str(config), 'inbox', '--count'],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (0, '0\n'), result.stderr)

    def test_installation_creates_links_and_private_account_config(self):
        destination = self.work / 'installed'
        config = destination / 'mail.toml'
        command = [sys.executable, str(ROOT / 'scripts/install_claw_mail.py'),
                   '--bin-dir', str(destination / 'bin'), '--skill-dir', str(destination / 'skills'),
                   '--config', str(config), '--socket', str(self.socket), '--cetta', str(BINARY),
                   '--agent', 'reviewer']
        installed = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        installed = (destination / 'bin/claw-mail').resolve()
        self.assertNotEqual(installed, CLI)
        self.assertEqual(installed.read_bytes(), CLI.read_bytes())
        self.assertEqual(installed.with_suffix('.metta').read_bytes(), CLI.with_suffix('.metta').read_bytes())
        self.assertTrue((destination / 'skills/claw-mail/SKILL.md').is_file())
        env = isolated_environ()
        for key in ('CETTA_BIN', 'CETTA_SHARED_MAIL_SOCKET', 'CLAW_MAIL_AGENT'):
            env.pop(key, None)
        result = subprocess.run([sys.executable, str(destination / 'bin/claw-mail'),
                                 '--config', str(config), 'inbox', '--count'],
                                cwd=destination, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (0, '0\n'), result.stderr)
        before = config.read_bytes()
        again = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(config.read_bytes(), before)

    def test_invalid_route_name_and_wait_budget_are_rejected(self):
        for args in [('send', 'account/agent', 'receiver', 'text'),
                     ('wait', 'reviewer', '--timeout', '-1'),
                     ('wait', 'reviewer', '--timeout', 'nan')]:
            result = subprocess.run(self.command(*args), capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
