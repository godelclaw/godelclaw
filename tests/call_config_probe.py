"""Observe actual bridge entries, including functions captured by the loader."""
import sys

_captures = 0
_forbidden = {'base', 'headers', 'request', 'kind', 'encoding', 'timeout',
              'cap', 'connect', 'first', 'idle'}


def _profile(frame, event, arg):
    global _captures
    if event != 'call' or not frame.f_code.co_filename.endswith('/native_llm_host.py'):
        return
    if frame.f_code.co_name in ('transport_config', 'native_command_seed'):
        _captures += 1
    if frame.f_code.co_name == 'field' and str(frame.f_locals.get('name')) in _forbidden:
        print('CALL_CONFIG_FORBIDDEN_FIELD:' + str(frame.f_locals.get('name')), file=sys.stderr)
        raise AssertionError('immutable metadata crossed the Python bridge separately')


def start():
    global _captures
    _captures = 0
    sys.setprofile(_profile)
    return 1


def count():
    sys.setprofile(None)
    return _captures
