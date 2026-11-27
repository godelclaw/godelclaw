"""Reject the Python operations that the native helper ports replace."""
import os
import helper


def forbid_python_helpers():
    def forbidden(*args, **kwargs):
        raise AssertionError("native helper called its Python backend")
    os.replace = forbidden
    helper.decode_safe_text = forbidden
    return True
