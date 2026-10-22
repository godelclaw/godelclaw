"""The native Telegram clocks answer as src/helper.py's Python originals do.

channels/telegram_clock.metta replaces helper.local_clock, helper.pad20,
helper.rest_notice_text and the time.time/strftime/sleep calls behind
tg:now, tg:now-iso and tg:sleep on CeTTa; helper.py stays the oracle.
Each zone runs one CeTTa process; Python computes the same cases here in
that zone. Set CETTA_BIN to a Python-enabled cetta.
"""
import json
from isolated_env import isolated_environ  # noqa: E402
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper  # noqa: E402

ZONES = ["UTC", "Europe/Berlin", "America/New_York", "Asia/Kolkata"]

# Fixed epochs: ordinary, fractional, negative, DST edges, year 9999/10000.
EPOCHS = [0, 1, 1790000000, 1790000000.9, -1.5, -86400, 1700000000,
          1711846799, 1711846800, 1730613599, 1730613600, 951782400,
          253402300799, 253402300800, 1e11, -1e11]
CLOCK_TEXTS = ["1790000000", "1e9", "1.79e9", "1790000000.999", "-0", "+7",
               ".5", "-1.5", " 1790000000 ", "\t12\n", "00012", "1E+9"]
# What reads as no time: "" either way.
INVALID = ["", "abc", "nan", "NaN", "inf", "-Infinity", "1e400", "12abc",
           "0x10", "1/2", "1,000", "--1", "True", "False", "1e5.5", " ",
           1e18, 1e300, 10 ** 30, -10 ** 30]
PAD20 = [0, 1, 7, -1, -5, 1792678147378159000, -1234567890123456789,
         99999999999999999999, 100000000000000000000, -99999999999999999999,
         123456789012345678901234, -123456789012345678901234, 7.9, -7.9]
LEFT = [0, 0.4, 1, 45.7, 59, 59.99, 60, 61, 125, 3599, 3600, 3661, 86399,
        -3, -0.5]
NOW = [1790000000, 1790000000.5, 1790000059.9, 1711846790]


def metta(value):
    """A MeTTa literal for a case value."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def cases():
    """(label, MeTTa expression, Python oracle) for every compared case."""
    out = []
    for e in EPOCHS:
        out.append(("clock %r" % e, "(tg-clock:local-clock %s)" % metta(e),
                    lambda e=e: helper.local_clock(e)))
        out.append(("iso %r" % e, "(tg-clock:iso-stamp %s)" % metta(e),
                    lambda e=e: time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(e))))
    for t in CLOCK_TEXTS + INVALID:
        out.append(("clock %r" % (t,), "(tg-clock:local-clock %s)" % metta(t),
                    lambda t=t: helper.local_clock(t)))
    for n in PAD20:
        out.append(("pad20 %r" % n, "(tg-clock:pad20 %s)" % metta(n),
                    lambda n=n: helper.pad20(n)))
    for left in LEFT:
        for now in NOW:
            out.append(("rest %r at %r" % (left, now),
                        "(tg-clock:rest-notice-text %s %s)" % (metta(left), metta(now)),
                        lambda left=left, now=now: helper.rest_notice_text(left, now)))
    return out


RECORDER = '''import json, os
def put(index, value):
    with open(os.environ["CLOCK_RECORD"], "a", encoding="utf-8") as f:
        f.write(json.dumps([index, value]) + "\\n")
    return 1
def forbid():
    import helper, time
    def forbidden(*args, **kwargs):
        raise AssertionError("native clock called its Python backend")
    helper.local_clock = helper.pad20 = helper.rest_notice_text = forbidden
    time.sleep = time.strftime = time.time = forbidden
    return 1
'''


def cetta_env(directory, zone):
    env = isolated_environ()
    env.update(TZ=zone, METTACLAW_ENGINE="cetta", METTACLAW_ACTIVE_ENGINE="cetta",
               CLOCK_RECORD=str(directory / "record.jsonl"),
               PYTHONPATH=os.pathsep.join((str(directory), str(ROOT / "src"),
                                           str(ROOT / "channels"))))
    env.setdefault("OPENAI_API_KEY", "test-only-unused")
    python = pathlib.Path(os.environ.get(
        "PETTA_PY_ENV", pathlib.Path.home() / "miniforge3" / "envs" / "petta"))
    if python.is_dir():
        env.update(PYTHONHOME=str(python), PYTHONNOUSERSITE="1",
                   LD_LIBRARY_PATH=os.pathsep.join((str(python / "lib"),
                                                    env.get("LD_LIBRARY_PATH", ""))))
    return env


def in_zone(zone, function):
    old = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        return function()
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


class TelegramClockParity(unittest.TestCase):
    def setUp(self):
        self.binary = pathlib.Path(os.environ.get("CETTA_BIN", ""))
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled cetta")

    def run_cetta(self, lines, zone, engine="cetta"):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            (directory / "clock_record.py").write_text(RECORDER)
            env = cetta_env(directory, zone)
            env.update(METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine)
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta",
                                             encoding="utf-8") as source:
                source.write("\n".join(lines) + "\n")
                source.flush()
                result = subprocess.run(
                    [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                     source.name], cwd=ROOT, env=env,
                    capture_output=True, text=True, timeout=120)
            record = directory / "record.jsonl"
            rows = ([json.loads(line) for line in
                     record.read_text(encoding="utf-8").splitlines()]
                    if record.exists() else [])
        detail = result.stdout[-3000:] + result.stderr[-3000:]
        self.assertEqual(result.returncode, 0, detail)
        return {str(i): v for i, v in rows}, result.stdout, detail

    def test_fixed_inputs_match_python_in_every_zone(self):
        compared = cases()
        header = ["!(import! &self ./src/helper.py)", "!(import! &self clock_record.py)",
                  "!(import! &self ./channels/telegram_clock.metta)",
                  "!(py-call (clock_record.forbid))"]
        body = ["!(py-call (clock_record.put %d %s))" % (i, expression)
                for i, (_, expression, _) in enumerate(compared)]
        for zone in ZONES:
            with self.subTest(zone=zone):
                native, _, detail = self.run_cetta(header + body, zone)
                expected = in_zone(zone, lambda: [oracle() for _, _, oracle in compared])
                for i, (label, _, _) in enumerate(compared):
                    self.assertIn(str(i), native, "%s: no native answer\n%s" % (label, detail))
                    self.assertEqual(native[str(i)], expected[i], "%s in %s" % (label, zone))

    def test_invalid_pad20_raises_as_python_does(self):
        lines = ["!(import! &self ./src/helper.py)",
                 "!(import! &self ./channels/telegram_clock.metta)"]
        values = ['"x"', '"1.5"', "(/ 0.0 0.0)", "(* 1e308 10)"]
        lines += ["!(Raised %d (case (catch (tg-clock:pad20 %s)) "
                  "(((Error $call $why) 1) ($value 0))))" % (i, v)
                  for i, v in enumerate(values)]
        lines += ['!(Text (tg-clock:pad20 "12"))']
        _, stdout, detail = self.run_cetta(lines, "UTC")
        self.assertEqual(re.findall(r"\(Raised (\d+) (\d)\)", stdout),
                         [(str(i), "1") for i in range(len(values))], detail)
        self.assertIn("(Text 00000000000000000012)", stdout, detail)
        for value in ("x", "1.5", float("nan"), float("inf")):
            with self.assertRaises((ValueError, OverflowError)):
                helper.pad20(value)

    def test_now_iso_and_sleep_read_the_live_clock(self):
        lines = ["!(import! &self clock_record.py)",
                 "!(import! &self ./channels/telegram_clock.metta)",
                 "!(py-call (clock_record.put 0 (py-call (time.time))))",
                 "!(py-call (clock_record.put 1 (tg-clock:now)))",
                 "!(py-call (clock_record.put 2 (tg-clock:now-iso)))",
                 "!(Slept (tg-clock:sleep 0.25))",
                 "!(py-call (clock_record.put 3 (tg-clock:now)))",
                 "!(py-call (clock_record.put 4 (py-call (time.time))))"]
        for zone in ("UTC", "Asia/Kolkata"):
            with self.subTest(zone=zone):
                native, stdout, detail = self.run_cetta(lines, zone)
                before, now, iso, later, after = (native[str(i)] for i in range(5))
                self.assertIsInstance(now, float)
                self.assertLessEqual(before, now + 1e-3)
                self.assertGreaterEqual(later - now, 0.25 - 1e-3)
                self.assertLessEqual(later, after + 1e-3)
                self.assertIn("(Slept ())", stdout, detail)
                stamps = in_zone(zone, lambda: {
                    time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))
                    for t in range(int(before) - 1, int(later) + 2)})
                self.assertIn(iso, stamps)

    def test_client_selects_native_clocks_only_on_cetta(self):
        program = ["!(import! &self ./src/helper.py)", "!(import! &self ./channels/telegram.py)",
                   "!(import! &self ./channels/telegram.metta)",
                   "!(Native (tg:native-clock?))",
                   '!(Clock (tg:clock "1790000000") (tg:clock ""))',
                   "!(Pad (tg:pad20 -5))",
                   '!(Rest (str:starts-with (tg:rest-notice-text 61) '
                   '"💤 resting — wakes in 1m01s (at "))']
        for engine, native in (("cetta", "true"), ("petta", "false")):
            with self.subTest(engine=engine):
                _, stdout, detail = self.run_cetta(program, "UTC", engine)
                self.assertIn("(Native %s)" % native, stdout, detail)
                self.assertRegex(stdout, r'\(Clock "?2026-09-21 14:13:20"? "?"?\)', detail)
                self.assertRegex(stdout, r'\(Pad "?-0000000000000000005"?\)', detail)
                self.assertIn("(Rest true)", stdout, detail)


if __name__ == "__main__":
    unittest.main()
