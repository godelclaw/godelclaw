"""Exercise real loop processes through production provider/CWP adapters."""
import http.server
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def accelerated_engine(state, engine, environment):
    """Replace only Omega's host sleep, keeping its loop and evaluator real."""
    directory = state / 'test-engine'
    directory.mkdir(exist_ok=True)
    clock = directory / 'mode_test_clock.py'
    clock.write_text('import runtime_host,time\n'
        'def install():\n'
        '    def sleep(seconds):\n'
        '        runtime_host.gate()\n'
        '        time.sleep(.001)\n'
        '        return True\n'
        '    runtime_host.sleep = sleep\n'
        '    return True\n')
    prefix = ([environment['MODE_TEST_CETTA_BIN']] if engine == 'cetta' else
              ['/bin/bash', str(Path(environment['MODE_TEST_PETTA_ROOT']) / 'run.sh')])
    runner = directory / 'cetta'
    runner.write_text(f'#!{sys.executable}\n'
        'import os,sys\nfrom pathlib import Path\n'
        f'command = {prefix!r} + sys.argv[1:]\n'
        'for index, argument in enumerate(command):\n'
        '    if not argument.endswith(".metta") or not Path(argument).is_file(): continue\n'
        '    entry = Path(argument)\n'
        '    source = entry.read_text()\n'
        '    if "!(omega)" not in source: continue\n'
        f'    replacement = {f"!(import! &self {json.dumps(str(clock))})"!r}\n'
        '    replacement += "\\n!(py-call (mode_test_clock.install))\\n!(omega)"\n'
        '    target = entry.with_name("test-clock.metta")\n'
        '    target.write_text(source.replace("!(omega)", replacement))\n'
        '    command[index] = str(target)\n'
        'os.execv(command[0], command)\n')
    runner.chmod(0o755)
    if engine == 'cetta':
        (directory / 'lib').symlink_to(Path(prefix[0]).resolve().parent / 'lib')
        environment['METTACLAW_MODE_CETTA_BIN'] = str(runner)
    else:
        (directory / 'run.sh').write_text('exec ' + shlex.join([sys.executable, str(runner)]) + ' "$@"\n')
        environment['PETTA_ROOT'] = str(directory)


class Provider(http.server.BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        pass
    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(value)
        if value.get('tools'):
            message = {'role': 'assistant', 'tool_calls': [
                {'id': 'fixture-nop', 'type': 'function',
                 'function': {'name': 'nop', 'arguments': '{}'}}]}
        else:
            message = {'role': 'assistant', 'content': 'pin smoke\nnop'}
        body = json.dumps({'choices': [{'message': message, 'finish_reason': 'stop'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ChannelServer:
    def __init__(self, path):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.server.bind(str(path))
        self.server.listen()
        self.server.settimeout(.2)
        self.pending = {'fixture-input': json.dumps(['input', '1.0', 'message', 'operator',
            {'update_id': 1, 'message': {'chat': {'id': 1}, 'from': {'first_name': 'Fixture'},
                                       'text': 'remember this fixture'}}])}
        self.acks = []
        self.sends = []
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
    def run(self):
        while self.running:
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            with connection:
                packet = connection.recv(66000)
                code, n = packet[4:6]
                name, body = packet[6:6+n].decode(), packet[6+n:].decode()
                reply_code, reply_name, reply_body = 64, '', ''
                if code == 1 and not name and self.pending:
                    reply_code = 65
                    reply_name, reply_body = next(iter(self.pending.items()))
                elif code == 2:
                    reply_code = 66
                    self.pending.pop(name, None)
                    self.acks.append(name)
                elif code == 4:
                    reply_code = 66
                    self.sends.append(json.loads(body))
                connection.sendall(b'CWP1' + bytes([reply_code, len(reply_name)]) +
                                   reply_name.encode() + reply_body.encode())
    def close(self):
        self.running = False
        self.thread.join(1)
        self.server.close()


class ModeRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('MODE_TEST_CETTA_BIN'), 'set MODE_TEST_CETTA_BIN for real CeTTa checks')
    def test_cetta_modes_checkpoint_restart_and_switch(self):
        self.check_modes('cetta')

    @unittest.skipUnless(os.environ.get('MODE_TEST_CETTA_BIN'), 'set MODE_TEST_CETTA_BIN for real CeTTa checks')
    def test_cetta_native_modes_checkpoint_restart_and_switch(self):
        self.check_modes('cetta', 'native')

    @unittest.skipUnless(os.environ.get('MODE_TEST_PETTA_ROOT'), 'set MODE_TEST_PETTA_ROOT for real PeTTa checks')
    def test_petta_modes_checkpoint_restart_and_switch(self):
        self.check_modes('petta')

    def check_modes(self, engine, transport='python'):
        Provider.requests = []
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            agent = state/'agent'
            (agent/'memory').mkdir(parents=True)
            (agent/'src').mkdir()
            for source in (ROOT/'src').iterdir():
                if source.name != '__pycache__':
                    (agent/'src'/source.name).symlink_to(source, target_is_directory=source.is_dir())
            (agent/'channels').symlink_to(ROOT/'channels', target_is_directory=True)
            (state/'identity.txt').write_text('You are Fixture, a separate test identity.')
            (state/'lifecycle.json').write_text('{"schema":1,"state":"running"}')
            # Upstream imports the SDK before install_iter replaces its client
            # with our fixture provider. Neither transport may call that SDK.
            (agent/'src/openai.py').write_text('class OpenAI:\n'
                '    def __init__(self, *args, **kwargs):\n'
                '        raise AssertionError("process fixture invoked provider SDK")\n')
            (agent/'src/requests.py').write_text('def request(*args, **kwargs):\n'
                '    raise AssertionError("process fixture bypassed durable channel")\n'
                'get = post = request\n')
            provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
            threading.Thread(target=provider.serve_forever, daemon=True).start()
            channel = ChannelServer(state/'channel.sock')
            try:
                fixture_environment = os.environ.copy()
                accelerated_engine(state, engine, fixture_environment)
                for mode in ('iter', 'omega'):
                    (agent/'memory/loop_mode.json').write_text(json.dumps({'mode': mode}))
                    environment = fixture_environment.copy()
                    environment.update({
                        'PYTHONPATH': str(agent/'src'),
                        'PYTHONHOME': sys.prefix,
                        'LD_LIBRARY_PATH': str(Path(sys.prefix)/'lib'),
                        'PYTHONDONTWRITEBYTECODE': '1',
                        'ITER_PYTHON': sys.executable,
                        'METTACLAW_MODE_STATE_ROOT': str(state/'modes'),
                        'METTACLAW_ENGINE_STATE_PATH': str(state/'engine'),
                        'METTACLAW_LIFECYCLE_PATH': str(state/'lifecycle.json'),
                        'METTACLAW_COGNITIVE_HEALTH_PATH': str(state/'health.json'),
                        'METTACLAW_MODEL_STATE_PATH': str(state/'model.metta'),
                        'METTACLAW_RECYCLE_REQUEST_PATH': str(state/'recycle'),
                        'METTACLAW_TELEGRAM_SERVICE_SOCKET': str(state/'channel.sock'),
                        'METTACLAW_TELEGRAM_PRIMARY_CHAT_ID': '1',
                        'METTACLAW_PROMPT_PATH': str(state/'identity.txt'),
                        'SYNTHETIC_MODEL': 'fixture-model',
                        'SYNTHETIC_BASE_URL': f'http://127.0.0.1:{provider.server_port}/v1',
                        'SYNTHETIC_API_KEY': 'fixture-only',
                        'METTACLAW_LLM_TRANSPORT': transport,
                        'METTACLAW_LLM_LEDGER_PATH': str(state/'ledger.db'),
                        'METTACLAW_LLM_POLICY': 'upstream',
                    })
                    environment.pop('METTACLAW_LOOP_MODE_PATH', None)
                    command = [sys.executable, str(ROOT/'modes/launch.py'), '--repo', str(agent),
                               '--mode', mode, '--engine', engine]
                    for restart in range(2):
                        trace = state/'modes'/mode/'runtime.jsonl'
                        count_before = trace.read_text().count('"kind": "boundary"') if trace.exists() else 0
                        with (state/f'{mode}-{restart}.log').open('w') as log:
                            process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
                            try:
                                deadline = time.monotonic()+45
                                while time.monotonic() < deadline:
                                    self.assertIsNone(process.poll(), (state/f'{mode}-{restart}.log').read_text())
                                    required = 100 if mode == 'omega' else 2
                                    if trace.exists() and trace.read_text().count('"kind": "boundary"') >= count_before+required:
                                        break
                                    time.sleep(.1)
                                else:
                                    self.fail((state/f'{mode}-{restart}.log').read_text()+'\n'+(trace.read_text() if trace.exists() else 'no trace'))
                                (state/'recycle').touch()
                                self.assertEqual(process.wait(timeout=8), 0)
                            finally:
                                if process.poll() is None:
                                    process.terminate()
                                    process.wait(timeout=15)
                                (state/'recycle').unlink(missing_ok=True)
                        active = json.loads((state/'active-loop.json').read_text())
                        self.assertEqual(active['mode'], mode)
                        self.assertEqual(Path(active['state_directory']), state/'modes'/mode)
                    if mode == 'iter':
                        checkpoint = state/'modes/iter/experience.json'
                        self.assertTrue(checkpoint.exists(), 'no Iter checkpoint; ' +
                            (state/'iter-1.log').read_text()[-4000:])
                        history = json.loads(checkpoint.read_text())
                        tools = [m['content'] for m in history if m.get('role') == 'tool']
                        self.assertTrue(tools, 'no tool result was checkpointed')
                        self.assertTrue(all(m.endswith(': SUCCESS') for m in tools), tools)
                        self.assertTrue(any('remember this fixture' in m.get('content','') for m in history))
                        self.assertIn('fixture-input', channel.acks)
                        channel.pending['omega-input'] = json.dumps(['input','1.0','message','operator',
                            {'message': {'chat': {'id':1},'text':'omega checkpoint fixture'}}])
                    else:
                        history = (state/'modes/omega/repos/Omega/memory/history.metta').read_text()
                        self.assertIn('omega checkpoint fixture', history)
                        self.assertIn('omega-input', channel.acks)
                native = next(r for r in Provider.requests if r.get('tools'))
                self.assertEqual(native['tool_choice'], 'required')
                self.assertTrue(any('Fixture' in m['content'] for m in native['messages']))
                self.assertTrue(any(not r.get('tools') for r in Provider.requests))
            finally:
                channel.close()
                provider.shutdown()
                provider.server_close()


if __name__ == '__main__':
    unittest.main()
