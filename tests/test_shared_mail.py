"""CLI round trips against the real service, with a staged member account."""
import json
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
        path.write_text('!(import! &self mailbox)\n' + code.replace('SOCKET', q(self.socket))
                        .replace('INBOX', q('mail@' + LOGIN)).replace('UID', str(os.getuid())))
        result = subprocess.run([str(BINARY), '--lang', 'petta', '--quiet', str(path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def command(self, *arguments):
        return [sys.executable, str(CLI), '--cetta', str(BINARY), '--socket', str(self.socket), *arguments]

    def run_cli(self, *arguments, env=None, expected=0):
        result = subprocess.run(self.command(*arguments), capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def reply(self, message='reply', ident='reply-1', agent='reviewer'):
        self.raw('!(mailbox:send SOCKET INBOX ' + json.dumps(ident) + ' ' + json.dumps(agent) +
                 ' "fixture-agent" ' + json.dumps(message, ensure_ascii=False) + ')\n')

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
        args = ('send', 'reviewer', 'fixture-agent', 'hello', '--id', 'owned-one')
        self.assertEqual(self.run_cli(*args)['status'], 'accepted')
        self.assertIn('mailbox:delivery', self.raw('!(mailbox:next SOCKET "agent-mail" "fixture-agent")'))
        self.assertEqual(self.run_cli(*args)['status'], 'duplicate')
        self.assertIn('settled', self.raw('!(mailbox:ack SOCKET "agent-mail" "fixture-agent" "owned-one")'))
        self.assertIn('(mailbox:idle)', self.raw('!(mailbox:next SOCKET "agent-mail" "fixture-agent")'))

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

    def test_invalid_route_name_and_wait_budget_are_rejected(self):
        for args in [('send', 'account/agent', 'receiver', 'text'),
                     ('wait', 'reviewer', '--timeout', '-1'),
                     ('wait', 'reviewer', '--timeout', 'nan')]:
            result = subprocess.run(self.command(*args), capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
