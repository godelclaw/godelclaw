import pathlib
import re
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "channels"))

import context_sources  # noqa: E402
import development_state  # noqa: E402
import effect_receipts  # noqa: E402


class ContextSourcesTest(unittest.TestCase):
    def setUp(self):
        self.projector = context_sources.Projector()
        self.spec = context_sources.SourceSpec("memory", 12)

    def test_known_value_is_named_and_bounded(self):
        observed = self.projector.observe(self.spec, lambda: "abcdefghijklm")
        self.assertEqual(observed.status, "known")
        self.assertEqual(observed.source_id, "memory")
        self.assertLessEqual(len(observed.text), 12)

    def test_unavailable_preserves_last_known_value(self):
        self.projector.observe(self.spec, lambda: "witnessed")

        def unavailable():
            raise RuntimeError("temporary")

        observed = self.projector.observe(self.spec, unavailable)
        self.assertEqual(observed.status, "stale-known")
        self.assertEqual(observed.text, "witnessed")

    def test_absence_clears_the_previous_projection(self):
        self.projector.observe(self.spec, lambda: "witnessed")
        absent = self.projector.observe(self.spec, lambda: "")
        self.assertEqual(absent.status, "absent")
        unavailable = self.projector.observe(
            self.spec, lambda: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.text, "")

    def test_source_order_and_identity_are_explicit(self):
        first = context_sources.SourceSpec("first", 20)
        second = context_sources.SourceSpec("second", 20)
        rendered = self.projector.render((
            (first, lambda: "one"),
            (second, lambda: "two"),
        ))
        self.assertLess(rendered.index("SOURCE[first]"),
                        rendered.index("SOURCE[second]"))
        self.assertEqual(rendered.count("SOURCE[first]"), 1)
        self.assertEqual(rendered.count("SOURCE[second]"), 1)

    def test_live_registry_has_unique_stable_ids(self):
        ids = [spec.source_id for spec in context_sources.SOURCE_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("effect-receipts", ids)
        self.assertIn("development-state", ids)
        self.assertIn("recent-proposals", ids)
        self.assertNotIn("recent-actions", ids)
        self.assertLess(ids.index("effect-receipts"),
                        ids.index("development-state"))
        self.assertLess(ids.index("development-state"),
                        ids.index("recent-proposals"))
        self.assertIn("project-capabilities", ids)
        self.assertIn("project-evidence", ids)
        self.assertIn("task-phase", ids)
        self.assertIn("active-query-declaration", ids)
        self.assertEqual(ids[-1], "conversation")

    def test_development_state_shows_active_iter_and_no_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            transformations = root / "transformations"
            proposals = root / "proposals"
            transformations.mkdir()
            (proposals / ("a" * 64)).mkdir(parents=True)
            (transformations / "10_active.metta").write_text(
                "(= (iter-transform $visible) (success $visible))\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {
                "METTACLAW_ITER_PROCESS_DIR": str(transformations),
                "METTACLAW_SELFMOD_PROPOSAL_STORE": str(proposals),
            }):
                observed = __import__("json").loads(development_state.view())

        active = observed["iter_transformations"]["active"]
        self.assertEqual(active[0]["name"], "10_active.metta")
        self.assertNotIn("protected_source_proposals", observed)
        self.assertNotIn("promotion", observed["iter_transformations"])

    def test_broker_receipt_crosses_the_real_context_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "receipts.jsonl"
            with mock.patch.dict(os.environ, {
                "METTACLAW_EFFECT_RECEIPT_PATH": str(path),
                "METTACLAW_HISTORY_PATH": str(
                    pathlib.Path(directory) / "history.metta"),
                "METTACLAW_TELEGRAM_LOG_PATH": str(
                    pathlib.Path(directory) / "updates.jsonl"),
            }):
                effect_receipts.record_command(
                    8, "returned", ["send-file", "report.md"],
                    "partial(failed)",
                )
                rendered = context_sources.bundle()
        stable, conversation, per_turn = rendered.split(
            "\n\n" + context_sources.CACHE_BOUNDARY + "\n\n")
        self.assertIn("SOURCE[project-capabilities]", stable)
        self.assertIn("SOURCE[conversation]", conversation)
        self.assertIn("SOURCE[effect-receipts] status=known", per_turn)
        self.assertIn("partial(failed)", per_turn)
        self.assertIn("SOURCE[recent-proposals]", per_turn)
        self.assertTrue(per_turn.rsplit("\n\n", 1)[-1].startswith(
            "CONTEXT_CERTIFICATE "))

    def test_layout_orders_sources_least_changeable_first(self):
        observations = [
            context_sources.Observation(source_id, "known", "x", "r")
            for source_id in ("pins", "conversation", "project-evidence",
                              "effect-receipts", "project-capabilities",
                              "development-state")
        ]
        groups = context_sources.layout(observations).split(
            "\n\n" + context_sources.CACHE_BOUNDARY + "\n\n")
        order = [re.findall(r"SOURCE\[([^\]]+)\]", group) for group in groups]
        self.assertEqual(order, [
            ["project-capabilities", "development-state", "project-evidence"],
            ["conversation"],
            ["pins", "effect-receipts"],
        ])

    def test_atlas_query_receipt_enters_the_next_certificate_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "receipts.jsonl"
            with mock.patch.dict(os.environ, {
                "METTACLAW_EFFECT_RECEIPT_PATH": str(path),
                "METTACLAW_HISTORY_PATH": str(
                    pathlib.Path(directory) / "history.metta"),
                "METTACLAW_TELEGRAM_LOG_PATH": str(
                    pathlib.Path(directory) / "updates.jsonl"),
            }):
                effect_receipts.record_atlas_result(
                    "zahrada-atlas-shadow",
                    "atlas_query",
                    {
                        "view_id": "view:" + "a" * 64,
                        "state_revision": "state:" + "b" * 64,
                        "status": "ContextualFamily",
                        "receipt": {
                            "receipt_id": "receipt:" + "c" * 64
                        },
                    },
                )
                rendered = context_sources.bundle()
        certificate_line = rendered.splitlines()[-1].partition(" ")[2]
        import json
        certificate = json.loads(certificate_line)
        self.assertIn(
            "effect-receipts",
            {source["source_id"] for source in certificate["sources"]},
        )
        self.assertIn("zahrada-atlas-shadow", rendered)
        self.assertIn("state:" + "b" * 64, rendered)
        self.assertIn("receipt:" + "c" * 64, rendered)

    def test_bad_optional_query_file_does_not_erase_base_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "queries.json"
            path.write_text("not-json", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "METTACLAW_ACTIVE_QUERIES_PATH": str(path),
                "METTACLAW_HISTORY_PATH": str(
                    pathlib.Path(directory) / "history.metta"),
                "METTACLAW_TELEGRAM_LOG_PATH": str(
                    pathlib.Path(directory) / "updates.jsonl"),
            }):
                rendered = context_sources.bundle()
        certificate_line = rendered.splitlines()[-1].partition(" ")[2]
        import json
        certificate = json.loads(certificate_line)
        self.assertIn("operator-stimulus", certificate["active_queries"])
        self.assertRegex(
            rendered,
            r"SOURCE\[active-query-declaration\] "
            r"status=(?:unavailable|stale-known)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
