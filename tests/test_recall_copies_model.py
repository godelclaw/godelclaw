"""Recall's copy handling matches the RecallProvenance model on random rankings.

The Lean model (MeTTapedia lean/pettaclaw/RecallProvenance.lean) keeps the
first candidate of each text from a best-first ranking, and the fetch that
yields k distinct texts or exhausts the ranking is exact (refetch_exact). The
runtime additionally relabels each kept slot with its group's oldest copy and
best cosine. This test runs src/attention_memory_cetta.metta's am:distinct-rows
on random rankings and compares it with that definition, including the
prefix equation behind the refetch loop.
"""
import os
import pathlib
import random
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def oracle(rows, count):
    """rows: best-first (cos, id, text, time). One slot per text, oldest copy, best cos."""
    groups = {}
    for cos, ident, text, stamp in rows:
        key = (stamp + " " + ident)
        best, rep_key, rep = groups.get(text, (cos, key, ident))
        if key < rep_key:
            rep_key, rep = key, ident
        groups[text] = (max(best, cos), rep_key, rep)
    seen, kept = set(), []
    for cos, ident, text, stamp in rows:
        if text in seen:
            continue
        seen.add(text)
        best, _, rep = groups[text]
        kept.append((best, rep))
        if count and len(kept) == count:
            break
    return kept


def metta_row(cos, ident, text, stamp):
    return '(%s (MemoryInfo "%s" 1 "%s" (("time" "%s"))))' % (repr(cos), ident, text, stamp)


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN")
class RecallCopiesModelTest(unittest.TestCase):
    def test_distinct_rows_matches_the_model_and_its_prefix_equation(self):
        generator = random.Random(20261105)
        cases, lines = [], ["!(import! &self ./src/attention_memory_cetta.metta)"]
        for number in range(60):
            size = generator.randint(0, 9)
            cosines = sorted((generator.choice([0.25, 0.5, 0.75, 1.0]) for _ in range(size)), reverse=True)
            rows = [(cos, "m%d" % index, generator.choice("abcd"),
                     generator.choice(["2026-08-01 10:00:00", "2026-08-01 10:00:05", "2026-09-02 08:00:00"]))
                    for index, cos in enumerate(cosines)]
            count = generator.randint(0, 4)
            prefix = generator.randint(0, size)
            cases.append((rows, count, prefix))
            whole = "(" + " ".join(metta_row(*row) for row in rows) + ")"
            part = "(" + " ".join(metta_row(*row) for row in rows[:prefix]) + ")"
            lines.append('!(println! (CASE %d (am:distinct-rows %s %d) (am:distinct-rows %s %d)))'
                         % (number, whole, count, part, count))
        with tempfile.NamedTemporaryFile("w", dir=ROOT, prefix=".recall-model-", suffix=".metta",
                                         encoding="utf-8") as source:
            source.write("\n".join(lines) + "\n")
            source.flush()
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode",
                                     "ancestor-walk", source.name], cwd=ROOT, text=True,
                                    capture_output=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        answers = {}
        for line in result.stdout.splitlines():
            match = re.match(r"\(CASE (\d+) (.*)\)$", line)
            if match:
                answers[int(match.group(1))] = match.group(2)
        self.assertEqual(len(answers), len(cases), result.stdout[-2000:])
        row = re.compile(r'\(([0-9.]+) \(MemoryInfo "([^"]+)"')
        for number, (rows, count, prefix) in enumerate(cases):
            text = answers[number]
            groups = [m for m in row.findall(text)]
            expected_whole = oracle(rows, count)
            expected_part = oracle(rows[:prefix], count)
            got = [(float(c), i) for c, i in groups]
            self.assertEqual(got, expected_whole + expected_part, (number, rows, count, prefix, text))
            # refetch_exact: a prefix that yields `count` texts, or the whole
            # ranking, answers what the complete ranking answers.
            if count and (len(oracle(rows[:prefix], 0)) >= count or prefix == len(rows)):
                ids_whole = [i for _, i in oracle(rows, count)]
                ids_part = [i for _, i in oracle(rows[:prefix], count)]
                # Representatives come from the fetched rows, so compare texts.
                texts = {ident: text_ for _, ident, text_, _ in rows}
                self.assertEqual([texts[i] for i in ids_part], [texts[i] for i in ids_whole],
                                 (number, rows, count, prefix))


if __name__ == "__main__":
    unittest.main()
