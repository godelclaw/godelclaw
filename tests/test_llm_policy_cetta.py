"""Replay policy/usage traces through the MeTTa client (lp:*) and the Python client
(llm_policy), each on its own native ledger server. Both clients are live; persistent
records, not just return values, must agree.

Every ledger, process and output file is a disposable fixture. No provider is contacted.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from isolated_env import isolated_environ  # noqa: E402
import llm_policy  # noqa: E402
import native_ledger  # noqa: E402

CETTA = os.environ.get("CETTA_BIN")


def quoted(value):
    return json.dumps(value, ensure_ascii=False)


def literal(value):
    if isinstance(value, str):
        return quoted(value)
    if value is None:
        return 'JsonNull'
    if value is True:
        return 'True'
    if value is False:
        return 'False'
    return repr(value)


LIVE = '<live-owner>'


def snapshot(socket, owners=()):
    """Every record of the LLM ledger at `socket`, with the live owners and process
    identities (which differ between the two client processes) named, not compared."""
    result = {}
    for kind in ('requests', 'cooldowns', 'events', 'calls'):
        rows = native_ledger.llm_records(kind, socket)
        for row in rows:
            if kind == 'requests' and (row.get('owner') == llm_policy._owner or row.get('owner') in owners):
                row['owner'] = LIVE
            if kind == 'calls':
                row['pid'], row['start'] = '<pid>', '<start>'
            if kind == 'events':
                row['detail'] = json.loads(row['detail'])
        result[kind] = sorted(rows, key=lambda r: str(r.get('sequence', r.get('id', r.get('provider')))))
    return result


def python_error(error):
    """An exception as the reference reports it: its class and its message."""
    return ['error', type(error).__name__, str(error)]


def stored_details(socket):
    """The history details exactly as stored: snapshot() compares them parsed."""
    return [(row['call_id'], row['event'], row['detail']) for row in native_ledger.llm_events(socket=socket)]


@unittest.skipUnless(CETTA, 'set CETTA_BIN to the native runtime')
class NativeLedgerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # One ledger per client: the Python client's under os.environ (self.env), the
        # MeTTa client's in the guest's environment.
        self.native_socket = native_ledger.start(self, self.root / 'native')
        self.python_socket = native_ledger.start(self, self.root / 'python')
        self.native_owners = set()
        self.env = dict(isolated_environ(), METTACLAW_INPUT_LEDGER_SOCKET=self.python_socket,
                        METTACLAW_LLM_POLICY='managed', METTACLAW_LLM_POLICY_PATH='',
                        METTACLAW_INSTANCE='fixture-agent', METTACLAW_ACTIVITY_CALL_SCOPE='work-fixture',
                        METTACLAW_ACTIVE_LOOP_MODE='godelclaw', PYTHONHOME=sys.prefix,
                        LD_LIBRARY_PATH=str(Path(sys.prefix) / 'lib'))
        self.env.pop('METTACLAW_LLM_QUOTA_COOLDOWN_SECONDS', None)
        self.hook = patch('strategy_host.provider_admitted', return_value=1)
        self.hook.start(); self.addCleanup(self.hook.stop)

    def native(self, forms, *, raw=False, module='llm_policy_cetta.metta'):
        entry = self.root / 'probe.metta'
        output = self.root / 'results.jsonl'
        output.unlink(missing_ok=True)
        lines = ['!(import! &self ' + quoted(str(ROOT / 'src' / module)) + ')',
            '(= (probe:result $value) (case $value '
            '(((LedgerAdmitted $id) (JsonArray ("admitted" $id))) '
            ' ((LedgerDenied $code $delay) (JsonArray ("denied" $code $delay))) '
            ' ((LedgerChanged $changed) (JsonArray ("changed" $changed))) '
            ' (ok (JsonArray ("ok"))) '
            ' ((LedgerError (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
            ' ((LedgerError $error) (JsonArray ("error"))) '
            ' ((LedgerInvalid (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
            ' ((LedgerInvalid $error) (JsonArray ("error"))) '
            ' ($other (JsonArray ("unexpected" (repr $other)))))))',
            '(= (probe:emit $value) (fs:append-text ' + quoted(str(output)) +
            ' (str:concat (json:stringify $value) "\\n")))']
        for form in forms:
            lines.append('!(probe:emit ' + (form if raw else '(probe:result ' + form + ')') + ')')
        # Export the process's owner as metadata, so PID identity can be
        # normalized while all actual liveness tests still run in MeTTa.
        lines.append('!(probe:emit (lp:owner))')
        entry.write_text('\n'.join(lines) + '\n')
        env = dict(self.env, METTACLAW_INPUT_LEDGER_SOCKET=self.native_socket)
        run = subprocess.run([CETTA, '--lang', 'petta', '--import-mode', 'ancestor-walk', '--quiet', str(entry)],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertTrue(output.is_file(), run.stdout[-2000:] + run.stderr[-2000:])
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(len(rows), len(forms) + 1, run.stdout[-2000:])
        self.native_owners.add(rows.pop())
        return rows

    def agree(self):
        """Both ledgers hold the same records and the same stored detail bytes."""
        self.assertEqual(snapshot(self.native_socket, self.native_owners),
                         snapshot(self.python_socket, self.native_owners))
        self.assertEqual(stored_details(self.native_socket), stored_details(self.python_socket))

    def replay(self, actions):
        forms, expected = [], []
        with patch.dict(os.environ, self.env, clear=True):
            for action in actions:
                kind, args, now = action[0], action[1:-1], action[-1]
                with patch.object(llm_policy.time, 'time', return_value=now):
                    try:
                        if kind == 'admit':
                            ticket, provider, maximum, fingerprint = args
                            forms.append('(lp:admit %s %s %s %s %s)' % tuple(map(literal, (*args, now))))
                            with patch.object(llm_policy.uuid, 'uuid4') as uuid:
                                uuid.return_value.hex = ticket
                                value = llm_policy.admit(provider, maximum, request_fingerprint=fingerprint, now=now)
                            expected.append(['admitted', value])
                        elif kind == 'describe':
                            forms.append('(lp:describe %s)' % ' '.join(map(literal, (*args, now))))
                            ticket, model, protocol, purpose, transport = args
                            expected.append(['changed', llm_policy.describe(ticket, model=model, protocol=protocol,
                                                                           purpose=purpose, transport=transport)])
                        elif kind == 'describe-origin':
                            # The origin is JSON text, so duplicate keys reach both sides as written.
                            ticket, model, protocol, purpose, transport, origin = args
                            forms.append('(lp:describe-origin %s (json:parse %s) %s)' % (
                                ' '.join(map(literal, (ticket, model, protocol, purpose, transport))),
                                quoted(origin), now))
                            expected.append(['changed', llm_policy.describe(ticket, model=model, protocol=protocol,
                                                                           purpose=purpose, transport=transport,
                                                                           origin=json.loads(origin))])
                        elif kind == 'complete':
                            ticket, usage, finish = args
                            forms.append('(lp:complete %s (json:parse %s) %s %s)' %
                                         (quoted(ticket), quoted(json.dumps(usage)), literal(finish), now))
                            expected.append(['changed', llm_policy.complete(ticket, dict(usage=usage,
                                                                   choices=[dict(finish_reason=finish)]))])
                        elif kind == 'fail':
                            forms.append('(lp:fail %s)' % ' '.join(map(literal, (*args, now))))
                            ticket, sent, status, retry = args
                            expected.append(['changed', llm_policy.fail(ticket, sent=sent, status=status,
                                                                       retry_after=retry, now=now)])
                        else:
                            self.fail('unknown replay operation ' + kind)
                    except llm_policy.AdmissionDenied as error:
                        expected.append(['denied', error.reason, error.retry_after])
                    except (ValueError, TypeError, OverflowError) as error:
                        expected.append(python_error(error))
        self.assertEqual(self.native(forms), expected)
        self.agree()

class NativePolicyTests(NativeLedgerFixture):
    def test_old_terminal_receipts_survive_fresh_admission(self):
        self.replay([
            ('admit', 'old', 'synthetic', 10, '', 1000),
            ('complete', 'old', 3, 'stop', 1001),
            ('admit', 'new', 'synthetic', 10, '', 1000 + 172800 + 1),
        ])
        for socket in (self.python_socket, self.native_socket):
            [old] = [row for row in native_ledger.llm_records('requests', socket) if row['id'] == 'old']
            self.assertEqual(old['status'], 'completed')

    def test_invalid_accounting_counters_are_unknown_without_blocking_completion(self):
        steps = []
        for i, value in enumerate(('abc', '5_0', '+5', True, 1.5, -1, {}, [], 10**30)):
            ticket = 'invalid-%d' % i
            steps.extend([('admit', ticket, 'synthetic', 100, '', 1000 + i * 2),
                          ('complete', ticket, dict(prompt_tokens=10, completion_tokens=value), 'stop', 1001 + i * 2)])
        self.replay(steps)
    def test_configurations_match_without_silently_accepting_bad_overrides(self):
        path = self.root / 'policy.json'
        self.env['METTACLAW_LLM_POLICY_PATH'] = str(path)
        self.env.pop('METTACLAW_LLM_POLICY', None)
        cases = [{}, {'profile': 'upstream'}, {'quota_cooldown_seconds': ' 12 '},
                 {'quota_cooldown_seconds': True}, {'quota_cooldown_seconds': -1},
                 {'modes': []}, {'modes': None}, {'modes': {'godelclaw': []}},
                 {'modes': {'godelclaw': None}}, {'profile': 'invalid'}, [], None,
                 {'modes': {'godelclaw': {'profile': 'upstream', 'quota_cooldown_seconds': 2}}}]
        forms, expected = [], []
        for config in cases:
            text = json.dumps(config)
            path.write_text(text)
            with patch.dict(os.environ, self.env, clear=True):
                try:
                    result = llm_policy.configuration()
                    expected.append(['config', result['profile'], result['quota_cooldown_seconds']])
                except (ValueError, TypeError, AttributeError) as error:
                    expected.append(python_error(error))
            forms.append('(let $written (fs:write %s %s) '
                '(case (lp:configuration) (((LedgerConfig $profile $delay) (JsonArray ("config" $profile $delay))) '
                '((LedgerError (PythonException $class $message)) (JsonArray ("error" (str:symbol->string $class) $message))) '
                '($error (JsonArray ("error"))))))' % (quoted(str(path)), quoted(text)))
        self.assertEqual(self.native(forms, raw=True), expected)

    def test_admission_completion_duplicate_failure_and_cooldown_rows_agree(self):
        usage = dict(prompt_tokens=12, prompt_tokens_details=dict(cached_tokens=4), completion_tokens=3)
        self.replay([
            ('admit', 'a', 'synthetic', 100, 'fingerprint', 1000),
            ('describe', 'a', 'model \' quoted', 'chat-completions', 'coding', 'native', 1001),
            ('complete', 'a', usage, 'stop', 1002),
            ('complete', 'a', dict(prompt_tokens=900, completion_tokens=700), 'stop', 1003),
            ('admit', 'b', 'synthetic', 100, '', 1010),
            ('fail', 'b', True, 429, 2, 1011),
            ('admit', 'denied', 'synthetic', 100, '', 1012),
            ('admit', 'c', 'synthetic', 100, '', 1013),
            ('fail', 'c', False, None, None, 1014),
        ])

    def test_request_origin_omitted_empty_and_null_layout_agree(self):
        # Omitted and empty origins differ, as do an absent and a null cache_layout.
        origins = ['null', '{}', '{"cache_layout": null}', '{"trigger": null, "turn_id": null}',
                   '{"trigger": "user", "turn_id": "t-1", "cache_layout": {"prefix": 3}}',
                   '{"cache_layout": "x", "cache_layout": "y"}']
        tickets = ['omitted'] + ['origin-%d' % i for i in range(len(origins))]
        steps = [('admit', ticket, 'synthetic', 100, '', 1000 + i) for i, ticket in enumerate(tickets)]
        steps.append(('describe', 'omitted', 'model', 'responses', 'turn', 'native', 1010))
        steps += [('describe-origin', 'origin-%d' % i, 'model', 'responses', 'turn', 'native', origin, 1011 + i)
                  for i, origin in enumerate(origins)]
        self.replay(steps)

    def test_integer_coercion_is_cpythons_int(self):
        values = [True, False, 0, 7, -7, 7.75, -7.75, -0.5, 1e30, 2.0 ** 63, '7', ' 7 ', '+7', '-7', '-0',
                  '007', '1_000', '1__0', '_1', '1_', '+_1', '1_2_3', '٣', '７', '１_２',
                  '\U0001d7ce\U0001d7ed', ' 7 ', '\x1c7\x1f', ' 7 ', '᠎7', '7.0',
                  '', ' ', '+', '- 5', '1 2', '0x10', '1e3', 'abc', '7\x00', None, [], {}, '9' * 4300, '9' * 4301]
        forms, expected = [], []
        for value in values:
            forms.append('(let $integer (lp:integer (json:parse %s)) (if (== $integer InvalidInteger) '
                         '(JsonArray ("error")) (JsonArray ("ok" (str:number->string $integer)))))'
                         % quoted(json.dumps(value)))
            try:
                expected.append(['ok', str(int(value))])
            except (ValueError, TypeError, OverflowError):
                expected.append(['error'])
        self.assertEqual(self.native(forms, raw=True), expected)

    def test_integer_and_float_exceptions_are_pythons(self):
        # Where int() or float() raises, the reference reports the class and
        # the message exactly as CPython 3.12 formats them.
        nan, inf = float('nan'), float('inf')
        ints = [True, 7.75, -0.0, 1e30, nan, inf, -inf, 2 ** 63, '7', ' +١_٢ ', '1__2', '\x1c7', '7\x1f',
                '\U0001d7ce\U0001d7ed', 'é' * 300, "it's", '"q"', '', None, [], {}, '9' * 4301,
                '9_' * 4300 + '9', '9' * 4301 + 'x']
        floats = [True, False, 0, -7, 2 ** 53 + 1, 10 ** 30, 2 ** 1024 - 2 ** 970 - 1, 2 ** 1024 - 2 ** 970,
                  -2 ** 1024, 7.75, -0.0, nan, inf, -inf, '7', ' 7.5 ', '+.5', '5.', '.', '', ' ',
                  '1_000.5', '1__0', '_1', '1_', '1_.5', '1._5', '1e1_0', '1_e5', '1e_5', '1E5', '1e+5',
                  '1e-5', '1e', 'e5', '-', 'inf', '-Infinity', 'iNfInItY', 'nan', '-nan', 'nan(1)', 'infinit',
                  '0x1p3', '1e400', '-1e-400', '00012.50', '٣.٥', '１_２', ' +١_٢.٥e-1 ', '\x1c7', '7\x1f',
                  ' 7', '\xa07　', '7\x85', 'é', None, [], {}]
        forms, expected = [], []
        report = ('(((PythonException $class $message) (JsonArray ("error" (str:symbol->string $class) $message))) '
                  '($value (JsonArray ("ok" (json:stringify-python $value)))))')
        for name, convert, values in (('int', int, ints), ('float', float, floats)):
            for value in values:
                forms.append('(case (lp:python-%s (json:parse-python %s)) %s)'
                             % (name, quoted(json.dumps(value)), report))
                try:
                    expected.append(['ok', json.dumps(convert(value))])
                except (ValueError, TypeError, OverflowError) as error:
                    expected.append(python_error(error))
        self.assertEqual(self.native(forms, raw=True), expected)

    def test_retry_after_is_read_only_where_python_reads_it(self):
        # max(0, float(retry_after)) runs only for a managed 402 or 429, after
        # the pending check; its exception rolls the whole failure back.
        nan, inf = float('nan'), float('inf')
        cases = [(429, True), (429, False), (429, ' +١_٢.٥e-1 '), (429, 'inf'), (429, 'nan'), (429, {}),
                 (429, 'bad'), (429, '\x1c7'), (429, -5), (429, 0), (429, None), (429, 2.5), (429, '1e400'),
                 (429, []), (402, 7), (429.0, '3'), (500, {}), (True, {}), (None, 'bad'), (429, nan), (429, -inf)]
        forms, expected = [], []
        with patch.dict(os.environ, self.env, clear=True):
            for i, (status, retry) in enumerate(cases):
                ticket, provider, now = 'retry-%d' % i, 'provider-%d' % i, 1000 + 2 * i
                forms.append('(lp:admit %s %s 10 "" %s)' % (quoted(ticket), quoted(provider), now))
                with patch.object(llm_policy.time, 'time', return_value=now), \
                        patch.object(llm_policy.uuid, 'uuid4') as uuid:
                    uuid.return_value.hex = ticket
                    expected.append(['admitted', llm_policy.admit(provider, 10, now=now)])
                forms.append('(lp:fail %s True (json:parse-python %s) (json:parse-python %s) %s)'
                             % (quoted(ticket), quoted(json.dumps(status)), quoted(json.dumps(retry)), now + 1))
                try:
                    expected.append(['changed', llm_policy.fail(ticket, sent=True, status=status,
                                                                retry_after=retry, now=now + 1)])
                except (ValueError, TypeError, OverflowError) as error:
                    expected.append(python_error(error))
        self.assertEqual(self.native(forms), expected)
        self.agree()

    def test_history_details_are_pythons_json(self):
        # json.dumps(detail, sort_keys=True, separators=(",", ":")) escapes
        # non-ASCII text and sorts keys; the stored detail is those bytes.
        self.replay([
            ('admit', 'a', 'synthetic', 100, '', 1000),
            ('describe', 'a', 'modèle ✓', 'chat-completions', 'coding', 'native', 1001),
            ('complete', 'a', dict(prompt_tokens=2, completion_tokens=1), 'é' * 70 + '\U0001f600', 1002),
        ])
        self.agree()

    def test_native_caller_omits_an_empty_origin_as_the_host_does(self):
        # native_llm_host's Python route describes with `request_origin(plan) or None`.
        cases = [{}, {'origin': None}, {'origin': {}}, {'origin': {'trigger': 'user'}},
                 {'origin': {'cache_layout': None}}]
        forms = ['(account:request-origin (json:parse %s))' % quoted(json.dumps(case)) for case in cases]
        self.assertEqual(self.native(forms, raw=True, module='llm_accounting_cetta.metta'),
                         [case.get('origin') or None for case in cases])

    def test_late_usage_records_cost_without_committing_the_failed_turn(self):
        self.replay([
            ('admit', 'a', 'synthetic', 100, '', 1000),
            ('describe', 'a', 'model', 'chat-completions', 'compaction', 'native', 1001),
            ('fail', 'a', True, None, None, 1002),
            ('complete', 'a', dict(prompt_tokens=12, completion_tokens=3), 'stop', 1003),
            ('describe', 'missing', 'model', 'responses', 'turn', 'native', 1004),
        ])

    def test_raw_usage_census_matches_the_python_oracle(self):
        values = [None, False, True, -1, 0, 1, 2.0, '7', {}, [], 10**15, 10**30]
        cases = [{}, [], None]
        for field in ('prompt_tokens', 'input_tokens', 'output_tokens', 'completion_tokens',
                      'cache_read_input_tokens', 'cache_creation_input_tokens', 'prompt_cache_hit_tokens'):
            cases += [{field: value} for value in values]
        for incoming in values:
            for cached in values:
                cases.append(dict(input_tokens=incoming, cache_read_input_tokens=cached,
                                  cache_creation_input_tokens=3, output_tokens=4))
                cases.append(dict(prompt_tokens=incoming, prompt_tokens_details=dict(cached_tokens=cached),
                                  completion_tokens=4))
        for detail in ('input_tokens_details', 'prompt_tokens_details', 'output_tokens_details', 'completion_tokens_details'):
            cases += [{detail: value} for value in values]
            cases += [{detail: {key: value}} for key in ('cached_tokens', 'reasoning_tokens', 'audio_tokens',
                      'accepted_prediction_tokens', 'rejected_prediction_tokens') for value in values]
        forms = ['(ue:evidence (json:parse %s))' % quoted(json.dumps(case)) for case in cases]
        actual = self.native(forms, raw=True)
        expected = [llm_policy.usage_evidence(case) for case in cases]
        mismatches = [(cases[i], want, got) for i, (want, got) in enumerate(zip(expected, actual)) if want != got]
        self.assertEqual(mismatches[:5], [])

    def test_upstream_profile_bypasses_only_the_configured_cooldown(self):
        self.env['METTACLAW_LLM_POLICY'] = 'upstream'
        self.replay([('admit', 'a', 'synthetic', 100, '', 1000),
                     ('fail', 'a', True, 429, 20, 1001),
                     ('admit', 'b', 'synthetic', 100, '', 1002)])

    def test_old_admission_rows_expire_but_usage_and_uncertain_rows_survive(self):
        self.replay([('admit', 'a', 'synthetic', 100, '', 1000),
                     ('complete', 'a', dict(prompt_tokens=12, completion_tokens=3), 'stop', 1001),
                     ('admit', 'pending', 'synthetic', 100, '', 1002),
                     ('admit', 'fresh', 'synthetic', 100, '', 300000)])


if __name__ == '__main__':
    unittest.main()
