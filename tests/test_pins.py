"""Persistent pins remain bounded and valid under concurrent updates."""

import os
import pathlib
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pins  # noqa: E402


def test_concurrent_pins_are_atomic_and_capped():
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
            assert len(lines) == pins._MAX_PINS
            assert len(set(lines)) == pins._MAX_PINS
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
    test_concurrent_pins_are_atomic_and_capped()
    test_context_view_names_omissions_and_keeps_the_newest_pins()
