"""Provider-pinned compact Unicode profiles and their real regex consumers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_prose_unicode as generator


class CompactUnicodeProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = generator.native_basis()
        cls.profiles = generator.profiles(cls.native)
        cls.engine = generator.selected_binary()

    def run_engine(self, code, directory, label, lang="petta"):
        source = directory / (label + ".metta")
        source.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [str(self.engine), "--lang", lang, str(source)], cwd=ROOT,
            capture_output=True, timeout=90, check=False)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual((result.returncode, stderr), (0, ""),
                         label + "\n" + stdout + stderr)
        return stdout

    def test_every_scalar_and_independent_compiled_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="unicode-profiles-") as raw:
            directory = Path(raw)
            scalars = directory / "scalars"
            scalars.write_text("".join(chr(c) for c in sorted(generator.DOMAIN)),
                               encoding="utf-8")
            for name, predicate in (("alnum", str.isalnum),
                                    ("nonprintable", lambda c: not c.isprintable())):
                target = {c for c in generator.DOMAIN if predicate(chr(c))}
                old = generator.char_class(sorted(target))
                compact = self.profiles[name]
                for lang in ("petta", "he"):
                    artifacts = []
                    for variant, pattern in (("old", old), ("compact", compact)):
                        artifact = directory / ("%s-%s-%s.bin" % (name, lang, variant))
                        complement = directory / ("%s-%s-complement" % (name, lang))
                        code = ("!(import! &self (library re))\n"
                                "!(import! &self (library fs))\n"
                                "!(let* (($p (re:compile %s)) ($wire (re:store $p)) "
                                "($_ (fs:write %s $wire)) "
                                "($_ (fs:write %s (re:replace-literal $p "
                                "(fs:read-text %s) \"\")))) (str:byte-length $wire))\n" %
                                (json.dumps(pattern), json.dumps(str(artifact)),
                                 json.dumps(str(complement)), json.dumps(str(scalars))))
                        self.run_engine(code, directory,
                                        "%s-%s-%s" % (name, lang, variant), lang)
                        artifacts.append(artifact.read_bytes())
                        if lang == "petta":
                            expected = "".join(chr(c) for c in sorted(generator.DOMAIN - target))
                            self.assertEqual(complement.read_bytes(), expected.encode("utf-8"),
                                             name + " every-scalar membership")
                    self.assertEqual(artifacts[0], artifacts[1],
                                     name + " compiled identity on " + lang)
                    self.assertGreater(len(artifacts[0]), 1000)

    def test_future_letter_and_ascii_space_need_the_corrections(self):
        controls = (
            ("future-letter", r"[\p{L}\p{N}]", self.profiles["alnum"], "Ᲊ",
             "true", "false"),
            ("future-printable", r"[\p{C}\p{Z}]", self.profiles["nonprintable"],
             "Ᲊ", "false", "true"),
            ("ascii-space", r"[\p{C}\p{Z}]", self.profiles["nonprintable"], " ",
             "true", "false"),
        )
        with tempfile.TemporaryDirectory(prefix="unicode-controls-") as raw:
            code = "!(import! &self (library re))\n"
            for name, naive, corrected, char, _, _ in controls:
                code += ("!(let* (($a (re:compile %s)) ($b (re:compile %s))) "
                         "(Control %s (re:is-match $a %s) (re:is-match $b %s)))\n" %
                         (json.dumps(naive), json.dumps(corrected), name,
                          json.dumps(char), json.dumps(char)))
            output = self.run_engine(code, Path(raw), "negative-controls")
            for name, _, _, _, naive, corrected in controls:
                self.assertIn("(Control %s %s %s)" % (name, naive, corrected), output)

    def test_unchanged_small_predicates(self):
        tables = generator.tables(self.native)
        expected = {
            "line-break": "\\x{D}\\x{A}|[\\x{A}-\\x{D}\\x{1C}-\\x{1E}\\x{85}\\x{2028}-\\x{2029}]",
            "blank-run": "[\\x{9}\\x{1F}-\\x{20}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{202F}\\x{205F}\\x{3000}]+",
            "space": "[\\x{9}-\\x{D}\\x{1C}-\\x{20}\\x{85}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}-\\x{2029}\\x{202F}\\x{205F}\\x{3000}]",
            "word": "[^\\x{9}-\\x{D}\\x{1C}-\\x{20}\\x{85}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}-\\x{2029}\\x{202F}\\x{205F}\\x{3000}]+",
            "affect-trace": "\\x{22C4}\\x{27E8}[^\\x{27E9}]*\\x{27E9}",
        }
        self.assertEqual({name: tables[name] for name in expected}, expected)
        self.assertEqual(generator.unicodedata.unidata_version, "15.0.0")

    def test_unknown_providers_refuse_under_optimization_without_publishing(self):
        prefix = "import sys; from pathlib import Path; sys.path.insert(0, %r); " \
                 "import generate_prose_unicode as g; " \
                 "g.TARGET=Path(%r); "
        with tempfile.TemporaryDirectory(prefix="unicode-guards-") as raw:
            target = Path(raw) / "published.metta"
            cases = (
                ("python", "g.ORACLE_PYTHON=(99,99)"),
                ("native", "g.native_basis=lambda: {'version':'17.0.0'}"),
            )
            for name, mutation in cases:
                code = (prefix % (str(ROOT / "scripts"), str(target))) + mutation + \
                       "; sys.exit(g.main([]))"
                result = subprocess.run([sys.executable, "-O", "-c", code],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2, name + result.stdout + result.stderr)
                self.assertIn("REFUSE: unqualified", result.stderr)
                self.assertFalse(target.exists())

    def test_source_bundle_and_executable_correspondence(self):
        manifest = json.loads((self.engine.parent / "build-manifest.json").read_text())
        self.assertEqual(manifest["revision"], self.native["revision"])
        self.assertEqual(manifest["python_version"], "3.12")
        self.assertNotIn("/home/", (ROOT / "scripts/generate_prose_unicode.py").read_text())

    def test_source_commit_identity_does_not_certify_a_dirty_worktree(self):
        revision = self.native["revision"]
        with tempfile.TemporaryDirectory(prefix="unicode-source-guard-") as raw:
            source = Path(raw)
            for status in (1, 128):
                def git(args, **kwargs):
                    if args[-2:] == ["rev-parse", "HEAD"]:
                        return subprocess.CompletedProcess(args, 0, revision + "\n", "")
                    self.assertEqual(args[-3:], ["diff", "--quiet", revision])
                    return subprocess.CompletedProcess(args, status, "", "")
                with patch.dict(os.environ, {"CETTA_SOURCE": str(source)}), \
                        patch.object(generator.subprocess, "run", side_effect=git):
                    with self.assertRaisesRegex(generator.QualificationError, "uncommitted changes"):
                        generator.selected_source(revision)


if __name__ == "__main__":
    unittest.main()
