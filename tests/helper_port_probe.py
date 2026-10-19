"""Reject the Python operations that the native helper ports replace."""
import os
import file_tools
import helper
import tmux_bridge


def forbid_python_helpers():
    def forbidden(*args, **kwargs):
        raise AssertionError("native helper called its Python backend")
    os.replace = forbidden
    file_tools.update_result = forbidden
    helper.decode_safe_text = forbidden
    tmux_bridge.windows = tmux_bridge.peek = tmux_bridge.send = forbidden
    return True
