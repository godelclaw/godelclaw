"""Goal views at native helpers, complete caller and skill; generated data only."""
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_context_bundle_native import ROOT, HEADER, CALLER, guest, guest_env, q, save_evidence
from test_small_views_native import encoded
import context_sources
import goals
import helper
import goal_views_probe as probe


def goal(name="a", sti="0.5", lti="0.2", blocked="none", verified="never", note="fixture", legacy=False):
    prefix = '(goal %s Area sti:%s lti:%s vibes:(C 日) blocked-by:%s' % (name, sti, lti, blocked)
    return prefix + (')' if legacy else ' last-verified:%s note:%s)' % (verified, note))


class GoalViewsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "goals.metta"
        self.kernel = self.directory / "goals_kernel.json"
        self.compost = self.directory / "goals_compost.log"
        registry = self.directory / "registry.json"
        registry.write_text('{"schema":1,"projects":[]}')
        self.settings = {"METTACLAW_GOALS_PATH": str(self.path),
                         "METTACLAW_PROJECT_REGISTRY_PATH": str(registry),
                         "METTACLAW_ITER_PROCESS_DIR": str(self.directory / "iter"),
                         "METTACLAW_OBSERVED_STRATEGY": "0"}
        self.index = 0

    def step(self, text=None, mood=None, compost=None):
        files = {str(self.path): None if text is None else encoded(text.encode() if isinstance(text, str) else text),
                 str(self.kernel): None if mood is None else encoded(mood),
                 str(self.compost): None if compost is None else encoded(compost.encode())}
        return {"files": files}

    def test_runtime_manifest_includes_explicit_imports(self):
        selected = set(json.loads((ROOT / "config/runtime-files.json").read_text()))
        self.assertTrue({"src/goal_views.metta", "src/goal_views_boundary.py",
                         "src/goal_views_cetta.metta", "src/goal_views_python.metta"} <= selected)

    def test_missing_native_read_is_unavailable_not_invented_absence(self):
        log = self.directory / "missing-read"
        guest(self.directory, HEADER + ["!(import! &self ./src/goal_views_cetta.metta)",
            "!(import! &self ./tests/goal_views_probe.py)", "!(py-call (goal_views_probe.setup 1 0))",
            "!(match &self (= (fs:read-text-result $path $max $errors) $body) "
            "(remove-atom &self (= (fs:read-text-result $path $max $errors) $body)))",
            '!(py-call (goal_views_probe.record %s (json:stringify (JsonArray '
            '((JsonArray (gv:load "work-state")) (JsonArray (gv:load "affect-gestalt")))))))' % q(log)], **self.settings)
        row = json.loads(log.read_text())
        self.assertEqual(json.loads(row["text"]), [["unavailable"], ["unavailable"]])
        self.assertEqual(row["forbidden"], 0)

    def compare(self, name, steps, caller=False, skill=False, swi=False, boot=False, mixed=False, prelude=()):
        self.index += 1
        expected, paths = [], []
        env = guest_env(self.directory, "petta" if swi else "cetta", **self.settings)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                context_sources, "_loaders", probe.loaders), mock.patch.object(
                context_sources, "_PROJECTOR", context_sources.Projector()):
            helper._change_marks.clear()
            for i, step in enumerate(steps):
                path = self.directory / ("step-%d-%d.json" % (self.index, i))
                path.write_text(json.dumps(step))
                paths.append(path)
                probe.base.apply(path)
                if caller:
                    expected.append(context_sources.bundle())
                else:
                    try:
                        expected.append(["value", probe.ORIGINAL[name]()])
                    except Exception:
                        expected.append(["unavailable"])
        # The first step always installs/removes all three files, so the guest
        # starts from identical bytes without borrowing oracle process state.
        log = self.directory / ("output-%d" % self.index)
        lines = HEADER + ["!(import! &self ./tests/goal_views_probe.py)"]
        if caller:
            if not boot:
                lines += CALLER
        elif skill:
            lines += ["!(import! &self ./src/skills.metta)"]
        else:
            lines += ["!(import! &self ./src/goal_views_cetta.metta)"]
        lines += list(prelude)
        lines += ["!(py-call (goal_views_probe.setup %d %d))" % (not swi, caller)]
        for i, path in enumerate(paths):
            lines += ["!(py-call (small_views_probe.apply %s))" % q(path)]
            if caller:
                expr = ('(json:parse (str:symbol->string (py-call (goal_views_probe.python_bundle))))'
                        if mixed and i % 2 else "(addition-context-sources)")
            elif mixed and i % 2:
                expr = '(py-call (goal_views_probe.python_view %s))' % q(name)
            elif skill:
                expr = '(json:stringify (JsonArray (sv:outcome (collapse (catch (goals))))))' if not swi else '(py-call (goal_views_probe.wire (goals)))'
            else:
                source = {"work_state_view": "work-state", "affect_view": "affect-gestalt"}.get(name)
                expr = '(json:stringify (JsonArray (gv:load %s)))' % q(source) if source else '(json:stringify (JsonArray (sv:outcome (collapse (catch (gv:goals (gv:path) True))))))'
            lines += ["!(py-call (goal_views_probe.record %s %s))" % (q(log), expr)]
        guest(self.directory, lines, swi=swi, engine="petta" if swi else "cetta", boot=boot, **self.settings)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        actual = [row["text"] if caller else json.loads(row["text"]) for row in rows]
        save_evidence("goal-views-parity", {"view": name, "caller": caller, "skill": skill,
                      "swi": swi, "boot": boot, "mixed": mixed, "steps": steps,
                      "expected": expected, "actual": rows})
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["forbidden"] == 0 for row in rows))
        return rows

    def test_work_present_absent_empty_error_duplicates_order_notes_flags_compost(self):
        canonical = goal("a", note='"canonical"')
        records = "\n".join([goal("z", verified="2026-10-02", note="free note"),
            goal("a", legacy=True), goal("b", blocked='"wait for fixture"', verified="2026-10-03"),
            canonical, goal("a", legacy=True), goal("z", verified="2026-10-04"),
            goal("日🙂", sti="2", lti="-2", note='"  日\t🙂\u001f\u3000é  "'),
            goal("long", note='"' + "🙂é" * 90 + '"'),
            "(goal-flag a stuck)", "(goal-flag a cull-review)", "(goal-flag a stuck)",
            "(goal-budget 3.0)", "(mood C:1)", ";; retained comment"])
        steps = [self.step(), self.step(""), self.step("\n"), self.step(records)]
        steps += [self.step(records, compost="\n".join(
            "2026-10-%02dT00:00:00 solved %s" % (i+1, goal("done-%d" % i)) for i in range(n))) for n in range(11)]
        steps += [self.step(records), self.step(records), self.step(goal(note="changed")),
                  self.step(goal(note="changed")), self.step("\n  (goal malformed)"),
                  self.step("(goal-budget bad)"), self.step(goal(sti="1e-5")), self.step(records)]
        self.compare("work_state_view", steps)

    def test_work_whitespace_numeric_and_malformed_lines(self):
        values = [goal(sti="0.00005"), goal(sti="0.00015"), goal(sti=".5", lti="1."),
                  goal(sti="٠.٥", lti="１.０"), goal().replace("sti:", "sti: \t"),
                  goal().replace("goal a", "goal\u001fa"), goal().replace("goal a", "goal\u00a0a"),
                  goal().replace("goal a", "goal\ta"), " (goal a)", "(goal\ta)",
                  "(goal-flag a unknown)", "(goal-budget)", "(goal-budget .2)",
                  "(goal-budget 1e-5)", "(mood\tC:1)", ";; ignored", "(goal a)",
                  goal(note='"x\x00🙂"'), goal(note='"one\vline"')]
        self.compare("work_state_view", [self.step(value) for value in values])

    def test_affect_partial_full_rounding_bad_types_and_changing(self):
        moods = [None, {}, {"mood": {}}, {"mood": None}, {"mood": {"C": .00005}},
                 {"mood": {dim: value for dim, value in zip(goals.MOOD_DIMS,
                   [.00015, 1e-5, 1.00005, -0.0, -.00001, .99995, 1.99995, 2.34565, 20.5])}},
                 {"mood": {"Cn": " +٠.٥_٠ "}}, {"mood": {"Cn": True}},
                 {"mood": {"Cn": "+Inf", "C": "-Infinity", "Ct": "-NaN"}},
                 {"mood": {"Cn": "\u001f0.5"}},
                 {"mood": {"Cn": "bad"}}, {"mood": [1]}, [], b"{", b'"\xff"',
                 {"mood": {"Sp": 2}}]
        self.compare("affect_view", [self.step(goal(), mood=value) for value in moods])

    def test_skill_preserves_raw_splitlines_unicode_empty_and_fair_off(self):
        self.compare("goals_view", [self.step(value) for value in
            [None, "", "\n", goal(), ";; 日🙂\r\n\r\n", "a\v日\x1cb\u2028🙂\r\n", "a\0b", b"\xff"]], skill=True)

    def test_skill_fair_snapshot_only_on_present_nonempty_file(self):
        import strategy_host
        with mock.patch.object(strategy_host, "fair_enabled", return_value=True), mock.patch.object(
                strategy_host, "goal_snapshot", create=True, return_value={"z": ["日🙂", {"b": 2, "a": True}], "a": "fixture"}):
            rows = self.compare("goals_view", [self.step(value) for value in [None, "", goal(), "\n", goal()]],
                skill=True, prelude=['!(py-call (goal_views_probe.fixture_fair))'])
        self.assertEqual(rows[-1]["fair_calls"], 3)

    def test_full_work_clipping_and_unicode_compost(self):
        many = "\n".join(goal("g%02d" % i, note='"' + "🙂é 日" * 80 + '"') for i in range(45))
        self.compare("work_state_view", [self.step(many), self.step(many),
            self.step(goal(), compost="日🙂timestamp reason (goal done Area)\nodd\n"),
            self.step(goal(), compost="a  solved (goal spaced Area)\n")])

    def test_existing_missing_fair_snapshot_is_not_a_native_success(self):
        import strategy_host
        self.assertFalse(hasattr(strategy_host, "goal_snapshot"))
        with mock.patch.object(strategy_host, "fair_enabled", return_value=True):
            self.compare("goals_view", [self.step(goal())], skill=True,
                prelude=['!(py-call (goal_views_probe.fixture_fair_missing))'])

    @unittest.expectedFailure
    def test_capability_gap_json_nonfinite_mood(self):
        # Python's non-standard JSON extension; never count this as parity.
        self.compare("affect_view", [self.step(goal(), mood=b'{"mood":{"C":NaN}}')])

    @unittest.expectedFailure
    def test_capability_gap_json_unused_lone_surrogate(self):
        # Python accepts a non-scalar in an unused field; native JSON refuses.
        self.compare("affect_view", [self.step(goal(), mood=b'{"mood":{"unused":"\\ud800"}}')])

    @unittest.expectedFailure
    def test_capability_gap_goal_float_overflow_before_clamp(self):
        # Python float(decimal) becomes infinity, then _goal_dict clamps it.
        # The evaluator's numeric conversion raises before reaching clamp.
        self.compare("work_state_view", [self.step(goal(sti="9" * 400))])

    @unittest.expectedFailure
    def test_capability_gap_json_exponent_overflow(self):
        self.compare("affect_view", [self.step(goal(), mood=b'{"mood":{"C":1e400}}')])

    def test_swi_skill_unchanged(self):
        self.compare("goals_view", [self.step(goal()), self.step(""), self.step("\n")], skill=True, swi=True)

    def test_path_defaults_empty_and_nonregular_files(self):
        nested = self.directory / "日 🙂"
        nested.mkdir()
        target = nested / "goals.metta"
        first = self.step(goal())
        first["env"] = {"METTACLAW_GOALS_PATH": None}
        second = self.step()
        second["files"][str(target)] = encoded(goal("nested").encode())
        second["env"] = {"METTACLAW_HISTORY_PATH": str(nested / "history.metta")}
        empty = self.step()
        empty["env"] = {"METTACLAW_GOALS_PATH": ""}
        directory = self.step()
        directory["env"] = {"METTACLAW_GOALS_PATH": str(nested)}
        for name in ("work_state_view", "affect_view", "goals_view"):
            self.compare(name, [first, second, empty, directory], skill=name == "goals_view")

    def test_seeded_duplicate_record_permutations_at_complete_caller(self):
        rng = random.Random(40404)
        steps = []
        for _ in range(20):
            records = [goal(rng.choice(["a", "b", "日🙂", "é", "z"]),
                sti=rng.choice(["-.1", "0.00005", ".85", "1.5"]),
                verified=rng.choice(["never", "2026-10-23", "2026-10-25"]),
                blocked=rng.choice(["none", "fixture", '"待つ 🙂"']),
                note=rng.choice(['"one\u001f two\u3000日"', "free note", '"🙂é"']),
                legacy=rng.choice([True, False])) for _ in range(18)]
            records += ["(goal-flag a stuck)", "(goal-flag a saturated)"]
            steps.append(self.step("\n".join(records)))
        self.compare("work_state_view", steps, caller=True)

    def test_mixed_marker_owner_native_python_native_and_skipped_errors(self):
        steps = [self.step(text) for text in [goal(), goal(), goal(), goal(), "(goal broken)",
                 goal(), goal(note="changed"), goal(note="changed"), goal(note="changed"), "", goal()]]
        rows = self.compare("work_state_view", steps, mixed=True)
        self.assertEqual(rows[-1]["markers"], {"work-state": 9})
        self.assertIn("unchanged for 3 turns", rows[3]["text"])
        self.assertIn("changed", rows[6]["text"])

    def test_complete_caller_python_views_disabled_outcomes_and_marker_sequences(self):
        steps = [self.step(goal(), mood={"mood": {"C": .00005}}), self.step(goal()),
                 self.step("(goal bad)", mood=[]), self.step(b"\xff"),
                 self.step(""), self.step(goal(note="changed")), self.step(goal(note="changed"))]
        rows = self.compare("work_state_view", steps, caller=True)
        self.assertEqual(rows[-1]["calls"], {name: len(steps) for name in
                         ("pins", "effect-receipts", "recent-proposals", "relevant-files", "conversation")})
        self.assertIn("work state unavailable", rows[2]["text"])
        self.assertIn("SOURCE[work-state] status=stale-known", rows[3]["text"])

    def test_mixed_complete_callers_share_work_and_pins_markers(self):
        steps = [self.step(text) for text in [goal(), goal(), goal(), goal(note="changed"),
                 goal(note="changed"), "(goal broken)", goal(note="changed"), goal(), goal(),
                 goal(note='"x\0one"'), goal(note='"x\0one"'), goal(note='"x\0two"')]]
        steps[0]["files"][str(self.directory / "pins.txt")] = encoded(b"[x] generated pin\n")
        rows = self.compare("work_state_view", steps, caller=True, mixed=True)
        self.assertEqual(rows[-1]["markers"], {"work-state": 11, "pins-context": 12})
        self.assertIn("unchanged for 11 turns", rows[-1]["text"])

    def test_rounding_generated_binary_edges(self):
        rng = random.Random(404)
        values = [0., -0., 1e-5, .00005, .00015, .99995, 1.99995, 1e20, 1e100, 1e308]
        for _ in range(150):
            edge = rng.randrange(-1000000, 1000000) / 10000 + .00005
            values.extend([edge, math.nextafter(edge, -math.inf), math.nextafter(edge, math.inf)])
        log = self.directory / "rounding"
        lines = HEADER + ["!(import! &self ./src/goal_views_cetta.metta)",
            '!(py-call (context_bundle_probe.note %s (json:stringify (JsonArray (%s)))))' %
            (q(log), " ".join("(gv:r %s)" % repr(value) for value in values))]
        guest(self.directory, lines, **self.settings)
        actual = json.loads(log.read_text())
        expected = [goals._r(value) for value in values]
        save_evidence("goal-rounding", {"values": values, "expected": expected, "actual": actual})
        self.assertEqual(actual, expected)

    def test_explicit_slicing_limits_match_python(self):
        self.path.write_text(goal(note='"🙂 é 日 fixture"'))
        limits = [(maximum, note) for maximum in (0, 1, -1, 6000) for note in (0, 1, -1, 180)]
        helper._change_marks.clear()
        expected = [["value", goals.work_state_view(self.path, maximum, note)] for maximum, note in limits]
        log = self.directory / "slicing"
        lines = HEADER + ["!(import! &self ./src/goal_views_cetta.metta)",
                          "!(import! &self ./tests/goal_views_probe.py)",
                          "!(py-call (goal_views_probe.setup 1 0))"]
        for maximum, note in limits:
            lines += ['!(py-call (goal_views_probe.record %s (json:stringify (JsonArray '
                      '(sv:outcome (collapse (catch (gv:work %s %d %d))))))))' %
                      (q(log), q(self.path), maximum, note)]
        guest(self.directory, lines, **self.settings)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        actual = [json.loads(row["text"]) for row in rows]
        save_evidence("goal-slicing", {"limits": limits, "expected": expected, "actual": actual})
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["forbidden"] == 0 for row in rows))

    def test_direct_production_boot_order(self):
        # Same safe Chroma import fixture as the accepted direct-boot check.
        library = Path(os.environ.get("PETTA_LIB_CHROMADB", ROOT / "repos" / "petta_lib_chromadb"))
        self.assertTrue((library / "lib_chromadb.metta").is_file())
        shutil.copytree(library, self.directory / "repos" / "petta_lib_chromadb",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        rendered = subprocess.run([sys.executable, str(ROOT / "scripts/render_channel_config.py"), "telegram"],
                                  env=guest_env(self.directory), capture_output=True, text=True, check=True)
        (self.directory / "channel.metta").write_text(rendered.stdout)
        self.settings["METTACLAW_TELEGRAM_CLIENT"] = "metta"
        self.compare("work_state_view", [self.step(goal()), self.step(goal(note="new"))], caller=True, boot=True)

    def test_swi_bundle_unchanged(self):
        self.compare("work_state_view", [self.step(goal()), self.step(goal()), self.step("(goal broken)")], caller=True, swi=True)


if __name__ == "__main__":
    unittest.main()
