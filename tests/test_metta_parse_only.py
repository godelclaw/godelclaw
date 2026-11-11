import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CETTA = os.environ.get("CETTA_BIN", "")


@unittest.skipUnless(CETTA and pathlib.Path(CETTA).is_file(), "CeTTa is required")
class CriticalMeTTaParseTests(unittest.TestCase):
    def parse(self, path):
        return subprocess.run(
            [CETTA, "--lang", "petta", str(ROOT / "scripts" / "metta_parse_only.metta"), "--", str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def test_loop_and_attention_graph_parse_without_evaluation(self):
        for relative in ("src/loop.metta", "src/attention_graph.metta"):
            with self.subTest(relative=relative):
                result = self.parse(ROOT / relative)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("SYNTAX_OK", result.stdout)

    def test_an_unclosed_expression_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "broken.metta"
            path.write_text("(= (broken $x", encoding="utf-8")
            result = self.parse(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("SYNTAX_ERROR", result.stdout)


if __name__ == "__main__":
    unittest.main()
