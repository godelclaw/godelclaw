#!/usr/bin/env python3
"""Send mail, inspect an inbox, or wait for a reply through a CeTTa shared socket."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import unicodedata
import uuid


def agent_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("use an agent name with letters, digits, _, . or -")
    return value


def recipient_name(value):
    parts = value.split('/')
    if len(parts) not in (1, 2):
        raise argparse.ArgumentTypeError('use AGENT or ACCOUNT/AGENT')
    for part in parts:
        agent_name(part)
    return value


def duration(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("timeout must be a finite nonnegative number of seconds")
    return number


def metta_text(value):
    text = json.dumps(value, ensure_ascii=False)
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        # The JSON codec handles controls that the PeTTa string reader does not.
        return '(json-string-value (json-parse ' + json.dumps(text, ensure_ascii=False) + '))'
    return text


def load_config(path):
    if not path.is_file():
        return {}
    with path.open('rb') as stream:
        config = tomllib.load(stream)
    unknown = config.keys() - {'socket', 'cetta', 'agent', 'space'}
    if unknown or any(not isinstance(value, str) for value in config.values()):
        raise ValueError('mail config accepts only string socket, cetta, agent and space fields')
    for key in ('socket', 'cetta'):
        if key in config:
            value = os.path.expandvars(os.path.expanduser(config[key]))
            if key == 'socket' or '/' in value:
                value = str(path.parent / value) if not Path(value).is_absolute() else value
            config[key] = value
    return config


def invoke(args, call, timeout=30):
    source = Path(__file__).resolve().with_suffix('.metta').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='shared-mail-') as directory:
        program = Path(directory) / 'mail.metta'
        program.write_text(source + '\n!(shared-mail:print ' + call + ')\n', encoding='utf-8')
        result = subprocess.run([args.cetta, '--lang', 'petta', '--quiet', str(program)],
                                text=True, capture_output=True, timeout=timeout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        raise RuntimeError(f'cetta exited {result.returncode}: {result.stdout.strip()}')
    records = []
    for line in result.stdout.splitlines():
        if line.startswith(('{', '"{')):
            value = json.loads(line)
            records.append(json.loads(value) if isinstance(value, str) else value)
    if len(records) != 1 or not isinstance(records[0], dict) or 'status' not in records[0]:
        raise RuntimeError('missing or ambiguous mailbox result: ' + result.stdout.strip())
    return records[0]


def clean(value):
    return ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in str(value))


def summary(item):
    return f"MAIL id={clean(item['id'])} from={clean(item['from'])}: {clean(item['text'].splitlines()[0] if item['text'] else '')[:200]}"


def emit(args, result):
    if args.command == 'inbox' and args.count and result['status'] == 'inbox':
        print(result['count'])
    elif args.command == 'inbox' and args.notice:
        if result['status'] == 'inbox' and result['count']:
            print(f"MAIL: {result['count']} unacknowledged message(s) for {args.agent}; use claw-mail next.")
        elif result['status'] != 'inbox':
            print(result.get('reason', 'mail check failed'), file=sys.stderr)
    else:
        if not args.json:
            if result['status'] == 'delivery':
                print(summary(result))
            elif result['status'] == 'inbox':
                print(f"INBOX {result['count']} unacknowledged message(s); showing {len(result['items'])}")
                for item in result['items']:
                    print(summary(item))
        print(json.dumps(result, ensure_ascii=False))


def resolve_agent(parser, args, default):
    if args.command in ('send', 'ack'):
        count = 2 if args.command == 'send' else 1
        if len(args.fields) == count + 1:
            args.agent, *args.fields = args.fields
        elif len(args.fields) == count and default:
            args.agent = default
        else:
            parser.error(f'{args.command} needs AGENT or a configured CLAW_MAIL_AGENT')
        if args.command == 'send':
            args.to, args.text = args.fields
            recipient_name(args.to)
        else:
            args.id = args.fields[0]
    elif args.command != 'whoami':
        args.agent = args.agent or default
        if not args.agent:
            parser.error('choose AGENT or set CLAW_MAIL_AGENT')
    if args.command != 'whoami':
        agent_name(args.agent)


def main(argv=None):
    bootstrap = argparse.ArgumentParser(add_help=False)
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    bootstrap.add_argument('--config', type=Path,
                           default=os.environ.get('CLAW_MAIL_CONFIG') or config_home / 'godelclaw/mail.toml')
    known, _ = bootstrap.parse_known_args(argv)
    parser = argparse.ArgumentParser(description=__doc__, parents=[bootstrap])
    try:
        config = load_config(known.config.expanduser().resolve())
    except (OSError, ValueError) as error:
        parser.error(str(error))
    parser.add_argument('--socket', default=os.environ.get('CETTA_SHARED_MAIL_SOCKET', config.get('socket')))
    parser.add_argument('--cetta', default=os.environ.get('CETTA_BIN', config.get('cetta')))
    parser.add_argument('--space', default=config.get('space', 'agent-mail'),
                        help='send namespace (default agent-mail); an explicit empty value selects the owner inbox')
    parser.add_argument('--json', action='store_true', help='JSON only, without delivery summaries')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('whoami', help='show the identity authenticated by the service')
    send = commands.add_parser('send', help='send [AGENT] TO TEXT; acceptance is not a reply')
    send.add_argument('fields', nargs='+', metavar='ARG')
    send.add_argument('--id', help='retry the same occurrence with identical content')
    commands.add_parser('next', help='read without acknowledging').add_argument('agent', nargs='?')
    inbox = commands.add_parser('inbox', help='inspect pending and ready mail without taking it')
    inbox.add_argument('agent', nargs='?')
    inbox.add_argument('--limit', type=int, choices=range(1, 101), default=20, metavar='1..100')
    mode = inbox.add_mutually_exclusive_group()
    mode.add_argument('--count', action='store_true', help='print only the total number')
    mode.add_argument('--notice', action='store_true', help='print a notice only when mail is waiting')
    wait = commands.add_parser('wait', help='wait without acknowledging')
    wait.add_argument('agent', nargs='?')
    budget = wait.add_mutually_exclusive_group()
    budget.add_argument('--timeout', type=duration, default=120, help='seconds; 0 means forever (default 120)')
    budget.add_argument('--forever', action='store_true', help='wait until mail or persistent failure')
    wait.add_argument('--retry-limit', type=int, default=8, help='consecutive transient failures before exit (default 8)')
    ack = commands.add_parser('ack', help='ack [AGENT] ID after handling a delivery')
    ack.add_argument('fields', nargs='+', metavar='ARG')
    for command in commands.choices.values():
        command.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                             help=argparse.SUPPRESS)
        command.add_argument('--space', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.socket or not args.cetta:
        parser.error('choose socket and cetta in arguments, environment, or mail.toml')
    try:
        resolve_agent(parser, args, os.environ.get('CLAW_MAIL_AGENT', config.get('agent')))
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.command == 'wait' and args.retry_limit < 0:
        parser.error('--retry-limit must be nonnegative')
    q = metta_text
    message_id = None
    try:
        if args.command == 'whoami':
            result = invoke(args, f'(shared-mail:identity {q(args.socket)})')
        elif args.command == 'send':
            message_id = args.id if args.id is not None else uuid.uuid4().hex
            if not message_id or '\0' in message_id:
                parser.error('message ID must be nonempty and contain no NUL')
            print('message-id=' + json.dumps(message_id), file=sys.stderr, flush=True)
            result = invoke(args, f'(shared-mail:post {q(args.socket)} {q(args.space)} {q(message_id)} '
                                  f'{q(args.to)} {q(args.agent)} {q(args.text)})')
        elif args.command == 'ack':
            result = invoke(args, f'(shared-mail:ack {q(args.socket)} {q(args.agent)} {q(args.id)})')
        elif args.command == 'inbox':
            limit = 0 if args.count or args.notice else args.limit
            for _ in range(3):
                result = invoke(args, f'(shared-mail:inbox {q(args.socket)} {q(args.agent)} {limit})')
                if not result.get('retryable'):
                    break
        else:
            forever = args.command == 'wait' and (args.forever or args.timeout == 0)
            deadline = math.inf if forever else time.monotonic() + (args.timeout if args.command == 'wait' else 0)
            failures = 0
            while True:
                milliseconds = int(min(30000, max(0, (deadline - time.monotonic()) * 1000)))
                try:
                    result = invoke(args, f'(shared-mail:next {q(args.socket)} {q(args.agent)} {milliseconds})',
                                    timeout=milliseconds / 1000 + 30)
                except subprocess.TimeoutExpired as error:
                    result = {'status': 'error', 'reason': str(error), 'retryable': True}
                if result.get('retryable') and args.command == 'wait':
                    failures += 1
                    if failures > args.retry_limit or time.monotonic() >= deadline:
                        break
                    delay = min(8, .5 * 2 ** min(failures - 1, 4), max(0, deadline - time.monotonic()))
                    print(f'mail temporarily unavailable; retry {failures}/{args.retry_limit} in {delay:g}s', file=sys.stderr)
                    time.sleep(delay)
                    continue
                failures = 0
                if result['status'] != 'idle' or time.monotonic() >= deadline:
                    break
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result = {'status': 'error', 'reason': str(error)}
    if message_id:
        result.setdefault('id', message_id)
    emit(args, result)
    expected = {'whoami': {'identity'}, 'send': {'accepted', 'duplicate'}, 'inbox': {'inbox'},
                'ack': {'settled', 'duplicate'}, 'next': {'delivery', 'idle'}, 'wait': {'delivery'}}
    if result['status'] in expected[args.command]:
        return 0
    return 3 if args.command == 'wait' and result['status'] == 'idle' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
