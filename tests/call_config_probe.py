"""Count the host facts captured for each native call, including through the loader."""
import sys

_captures = 0


def _profile(frame, event, arg):
    global _captures
    if event != 'call' or not frame.f_code.co_filename.endswith('/native_llm_host.py'):
        return
    if frame.f_code.co_name == 'native_call_seed':
        _captures += 1


def start():
    global _captures
    _captures = 0
    sys.setprofile(_profile)
    return 1


def count():
    sys.setprofile(None)
    return _captures
