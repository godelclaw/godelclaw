"""Launch test fixtures; every mailbox operation runs in MeTTa/CeTTa."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
import threading

ROOT = Path(__file__).resolve().parents[1]


def qualify(root, bundle, artifacts, client="metta"):
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='instance-') as temp:
        work = Path(temp)
        socket = work / 's.sock'
        service = subprocess.Popen(
            [str(bundle / 'cetta-space-service'), '--socket', str(socket), '--data', str(work / 'store')],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        program = root / ('.native-mail-qualification-' + work.name + '.metta')
        pool = ThreadPoolExecutor(max_workers=1)
        finished = threading.Event()
        try:
            for _ in range(1000):
                if socket.exists():
                    break
                if service.poll() is not None:
                    raise AssertionError(service.stderr.read().decode())
                time.sleep(.01)
            else:
                raise AssertionError('fixture service did not start')

            def send(ident, body):
                path = work / (ident + '.metta')
                path.write_text('!(import! &self mailbox)\n!(mailbox:send ' + q(socket) +
                                ' "agent-mail" ' + q(ident) + ' "receiver" "sender" ' + q(body) + ')\n')
                result = subprocess.run([str(bundle / 'cetta'), '--lang', 'petta', '--quiet', str(path)],
                                        cwd=work, capture_output=True, text=True, timeout=15)
                assert result.returncode == 0 and 'accepted' in result.stdout, result.stdout + result.stderr

            send('incoming', 'native λ')
            signal = work / 'waiting'
            program.write_text('''!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(remove-atom &self (= (commchannel) (empty)))
!(add-atom &self (= (commchannel) telegram))
!(assert (mail:pending?))
!(assert (== (channel:sleep-until-message 0 False) "rested 0s"))
!(assert (mail:pending?))
!(tg:set last-human 1)
!(assert (== (receive) "[mail from sender; id=incoming] native λ"))
!(assert (not (lastMessageIsHuman)))
!(assert (mail:active?))
!(mail:reset)
!(assert (== (settleActivityBatch) 1))
!(assert (mail:pending?))
!(assert (== (receive) "[mail from sender; id=incoming] native λ"))
!(println! (run-command-batch-once 717171 99 (quote ((mail-send "sender" "native reply" "outgoing") (mail-receive)))))
!(assert (== (settleActivityBatch) 1))
!(assert (not (mail:active?)))
!(assert (not (mail:pending?)))
!(assert (== (receive) ""))
''' + '!(fs:update-text ' + q(signal) + ''' "write" "ready" "" "")
!(assert (== (channel:sleep-until-message 30 True) "woken by mail"))
!(assert (== (receive) "[mail from sender; id=wakeup] native wake"))
!(assert (== (settleActivityBatch) 1))
!(println! NATIVE_WAKE_OK)
!(remove-atom &self (= (mail:socket) (system:getenv-or-default "METTACLAW_MAIL_SOCKET" "")))
!(add-atom &self (= (mail:socket) "missing-native-mail-fixture.socket"))
!(let* (($before (get_time)) ($_ (mail:idle-wait 25)) ($after (get_time)))
   (progn (println! (NATIVE_PAUSE $before $after (- $after $before))) (assert (>= (- $after $before) 0.020))))
!(println! NATIVE_MAIL_INSTANCE_OK)
''')
            env = dict(os.environ, XDG_STATE_HOME=str(work / 'state'), OPENAI_API_KEY='test-only-unused',
                       METTACLAW_ENGINE='cetta', METTACLAW_ACTIVE_ENGINE='cetta', CETTA_BIN=str(bundle / 'cetta'),
                       METTACLAW_SKIP_INITIALIZE='1', METTACLAW_LLM_TRANSPORT='python',
                       METTACLAW_TELEGRAM_CLIENT=client, METTACLAW_OBSERVED_STRATEGY='0',
                       METTACLAW_MAIL_SOCKET=str(socket), METTACLAW_MAIL_NAME='receiver',
                       METTACLAW_MAIL_SPACE='agent-mail', TMPDIR=str(work), PYTHONDONTWRITEBYTECODE='1')

            def wake():
                for _ in range(3000):
                    if finished.is_set():
                        return
                    if signal.exists():
                        time.sleep(.15)
                        send('wakeup', 'native wake')
                        return
                    time.sleep(.01)
                raise AssertionError('native wait never started')

            future = pool.submit(wake)
            result = subprocess.run(['bash', './run.sh', str(program)], cwd=root, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=75)
            (artifacts / (root.name + '-qualification.log')).write_text(result.stdout + result.stderr)
            assert result.returncode == 0, result.stdout + result.stderr
            future.result(timeout=35)
            assert 'NATIVE_MAIL_INSTANCE_OK' in result.stdout, result.stdout
            assert 'MAILED sender id=outgoing' in result.stdout, result.stdout
            assert all(s not in result.stdout for s in ('COMMAND_ERROR:', 'COMMAND_EXCEPTION:', 'Assertion failed')), result.stdout
        finally:
            finished.set()
            program.unlink(missing_ok=True)
            service.terminate()
            service.communicate(timeout=10)
            pool.shutdown(wait=True)
    print('NATIVE_MAIL_INSTANCE_OK', root.name)
