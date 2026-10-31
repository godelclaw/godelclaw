"""Retention and the bounded prompt window have separate contracts."""

import os
import pathlib
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pins  # noqa: E402


def test_concurrent_pins_are_atomic_and_retained():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "pins.txt"
        previous = os.environ.get("METTACLAW_PINS_PATH")
        os.environ["METTACLAW_PINS_PATH"] = str(path)
        try:
            workers = [threading.Thread(target=pins.pin, args=("pin-%02d" % i,))
                       for i in range(24)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            lines = path.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 24
            assert len(set(lines)) == 24
            assert pins.view() != "(no pins)"
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_PINS_PATH", None)
            else:
                os.environ["METTACLAW_PINS_PATH"] = previous


def test_context_view_names_omissions_and_keeps_the_newest_pins():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "pins.txt"
        path.write_text("\n".join("[%02d] %s" % (i, "x" * 500 + str(i))
                                  for i in range(30)) + "\n")
        previous = os.environ.get("METTACLAW_PINS_PATH")
        os.environ["METTACLAW_PINS_PATH"] = str(path)
        try:
            view = pins.context_view()
            assert "newest 12 of 30" in view
            assert "(pins) reads the full retained list" in view
            assert "[29]" in view
            assert "[00]" not in view
            assert len(view) < 6000
            assert len(pins.view()) > len(view)
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_PINS_PATH", None)
            else:
                os.environ["METTACLAW_PINS_PATH"] = previous


if __name__ == "__main__":
    test_concurrent_pins_are_atomic_and_retained()
    test_context_view_names_omissions_and_keeps_the_newest_pins()


def test_long_pins_do_not_alias_after_a_shared_prefix():
    import unittest.mock as mock
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,
            {"METTACLAW_PINS_PATH": str(pathlib.Path(tmp) / "pins.txt")}):
        prefix = "same prefix " * 100
        pins.pin(prefix + "first")
        pins.pin(prefix + "second")
        retained = pins._read()
        assert len(retained) == 2
        assert retained[0].endswith(prefix + "first")
        assert retained[1].endswith(prefix + "second")
        assert "first" in pins.view() and "second" in pins.view()
