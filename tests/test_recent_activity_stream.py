"""Recent messages agree with a forward reference, including escaped routes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper


class ReverseLinesTests(unittest.TestCase):
    def test_matches_forward_reader_across_blocks_and_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger"
            for text in ("", "a", "a\n", "\n", "\n\n", "a\r\nb\n",
                         "α" * 50000 + "\n\nβ\n" + "c" * 90000):
                path.write_bytes(text.encode())
                reader = helper.reverse_lines_open(path)
                with path.open(encoding="utf-8") as stream:
                    expected = [line.rstrip("\r\n") for line in stream]
                self.assertEqual(list(reader), expected[::-1])

    def test_append_does_not_change_opened_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger"
            path.write_text("old\n")
            reader = helper.reverse_lines_open(path)
            with path.open("a") as stream:
                stream.write("new\n")
            self.assertEqual(list(reader), ["old"])
            self.assertEqual(list(helper.reverse_lines_open(path)), ["new", "old"])

    def test_early_close_releases_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger"
            path.write_text("a\nb\n")
            reader = helper.reverse_lines_open(path)
            self.assertEqual(helper.reverse_lines_next(reader), ("line", "b"))
            self.assertEqual(helper.reverse_lines_close(reader), 1)
            self.assertEqual(helper.reverse_lines_next(reader), ("end",))


@unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"), "set MODE_TEST_CETTA_BIN")
class RecentActivityTests(unittest.TestCase):
    def test_matches_reference_for_escaped_titles_and_sparse_old_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, output = root / "ledger.jsonl", root / "result"
            records = []
            for i in range(80):
                records.append(dict(allowed=i % 7 != 0, chat_title="Lab" if i < 6 or i % 13 == 0 else "Other",
                    chat_id="42" if i < 6 or i % 13 == 0 else "43", from_="Ada",
                    received_at="2026-01-01T12:%02d:00" % (i % 60),
                    text="line %d\n%s" % (i, "ordinary message" if i < 6 or i % 13 == 0 else "Lab in unrelated text")))
            for row in records:
                row["from"] = row.pop("from_")
            lines = [json.dumps(row).replace('"Lab"', '"\\u004cab"') for row in records]
            ledger.write_text("\n".join(lines) + '\n{invalid\n')
            expected = []
            for row in records:
                if row["allowed"] and row["chat_title"].lower() == "lab":
                    flat = " ".join(row["text"].split())
                    short = flat[:24] + "…" if len(flat) > 24 else flat
                    expected.append("[%s %s %s: %s]" % (row["received_at"][11:16], row["chat_title"], row["from"], short))
            source = root / "query.metta"
            source.write_text('!(import! &self (library lib_import))\n'
                '!(import! &self "%s/channels/telegram.metta")\n'
                '!(fs:write %s (tg:recent-activity 4 "lab" 24))\n' % (ROOT, json.dumps(str(output))))
            environment = dict(os.environ, METTACLAW_TELEGRAM_LOG_PATH=str(ledger),
                PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")]))
            result = subprocess.run([os.environ["MODE_TEST_CETTA_BIN"], "--lang", "petta",
                "--import-mode", "ancestor-walk", str(source)], cwd=ROOT, env=environment,
                capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr[-1500:])
            self.assertTrue(output.exists(), result.stdout[-1500:])
            self.assertEqual(output.read_text(), " ".join(expected[-4:]))


if __name__ == "__main__":
    unittest.main()
