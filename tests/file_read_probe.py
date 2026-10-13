"""Test-only digest and proof that CeTTa never falls back to Python reads."""
import hashlib
import fsops


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check(text, expected):
    if not isinstance(text, str):
        print("file read returned", type(text).__name__, repr(text)[:200], flush=True)
        raise AssertionError("file read did not return text")
    actual = digest(text)
    if actual != expected:
        print("file result digest", actual, "expected", expected,
              "prefix", repr(text[:100]), flush=True)
        raise AssertionError(f"file result digest {actual}, expected {expected}")
    return True


def forbid_python_reads():
    def forbidden(*args):
        raise AssertionError("native read-file called Python's reader")
    fsops.read_file = fsops.read_text_result = forbidden
    return True
