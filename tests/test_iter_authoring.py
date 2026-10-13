import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
import iter_authoring as authoring  # noqa: E402


class IterAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "transformations"
        self.environment = mock.patch.dict(os.environ, {
            "METTACLAW_ITER_PROCESS_DIR": str(self.directory),
            "METTACLAW_SELFMOD_PROPOSAL_STORE": str(
                Path(self.temporary.name) / "proposals"
            ),
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    @staticmethod
    def decode(receipt):
        return json.loads(receipt)

    @staticmethod
    def source(value):
        return (
            "(= (iter-transform\n"
            "      (coding-view $messages $advertised))\n"
            "   (success\n"
            "     (coding-view\n"
            "       (coding-message-append-one\n"
            "         $messages (coding-message user %s))\n"
            "       $advertised)))\n" % value
        )

    def test_write_is_exact_and_activates_only_at_next_capture(self):
        current = authoring._snapshot_revision(self.directory)
        source = self.source("next")

        receipt = self.decode(
            authoring.write_transformation("10_value.metta", source)
        )
        next_revision = authoring._snapshot_revision(self.directory)

        self.assertEqual(receipt["state"], "installed")
        self.assertEqual(receipt["syntax"], "pass")
        self.assertEqual(receipt["before_revision"], current)
        self.assertEqual(receipt["activation_revision"], next_revision)
        self.assertEqual(
            receipt["sha256"], hashlib.sha256(source.encode()).hexdigest()
        )
        self.assertNotEqual(current, next_revision)
        self.assertEqual(
            (self.directory / "10_value.metta").read_text(encoding="utf-8"),
            source,
        )

    def test_replace_receipt_binds_prior_and_new_bytes(self):
        old = self.source("old")
        new = self.source("new")
        authoring.write_transformation("10_value.metta", old)

        receipt = self.decode(
            authoring.write_transformation("10_value.metta", new)
        )

        self.assertEqual(
            receipt["prior_sha256"], hashlib.sha256(old.encode()).hexdigest()
        )
        self.assertEqual(
            receipt["sha256"], hashlib.sha256(new.encode()).hexdigest()
        )
        self.assertEqual(
            (self.directory / "10_value.metta").read_text(encoding="utf-8"), new
        )

    def test_syntax_failure_is_installed_then_stutters(self):
        receipt = self.decode(
            authoring.write_transformation("10_broken.metta", "(= broken\n")
        )

        self.assertEqual(receipt["state"], "installed")
        self.assertEqual(receipt["syntax"], "fail")
        self.assertTrue(receipt["syntax_error"])
        self.assertTrue((self.directory / "10_broken.metta").is_file())

    def test_disable_and_enable_are_reversible_next_capture_changes(self):
        source = self.source("active")
        authoring.write_transformation("10_value.metta", source)
        active_revision = authoring._snapshot_revision(self.directory)

        disabled = self.decode(
            authoring.disable_transformation("10_value.metta")
        )
        disabled_revision = authoring._snapshot_revision(self.directory)
        enabled = self.decode(authoring.enable_transformation("10_value.metta"))
        enabled_revision = authoring._snapshot_revision(self.directory)

        self.assertEqual(disabled["state"], "disabled")
        self.assertNotEqual(active_revision, disabled_revision)
        self.assertEqual(enabled["state"], "enabled")
        self.assertEqual(enabled_revision, active_revision)

    def test_list_reports_active_disabled_and_revision(self):
        authoring.write_transformation("10_active.metta", self.source("active"))
        authoring.write_transformation("20_disabled.metta", self.source("off"))
        authoring.disable_transformation("20_disabled.metta")

        observed = self.decode(authoring.list_transformations())

        self.assertEqual(observed["state"], "observed")
        self.assertEqual(
            [item["name"] for item in observed["active"]], ["10_active.metta"]
        )
        self.assertEqual(
            [item["name"] for item in observed["disabled"]],
            ["_20_disabled.metta"],
        )
        self.assertEqual(
            observed["revision"], authoring._snapshot_revision(self.directory)
        )

    def test_name_scope_is_exact_but_shell_remains_a_separate_authority(self):
        for name in (
            "../escape.metta", "/tmp/escape.metta", "_hidden.metta", "plain"
        ):
            with self.subTest(name=name):
                receipt = self.decode(
                    authoring.write_transformation(name, self.source("x"))
                )
                self.assertEqual(receipt["state"], "error")
        self.assertFalse((Path(self.temporary.name) / "escape.metta").exists())

    def test_direct_authoring_does_not_create_a_promotion_proposal(self):
        receipt = self.decode(
            authoring.write_transformation("10_direct.metta", self.source("x"))
        )
        proposal_store = Path(os.environ["METTACLAW_SELFMOD_PROPOSAL_STORE"])

        self.assertEqual(receipt["state"], "installed")
        self.assertFalse(proposal_store.exists())
        self.assertNotIn("promotion", receipt)

    def test_post_rename_sync_failure_cannot_hide_the_installed_effect(self):
        source = self.source("durability-unconfirmed")
        with mock.patch.object(
            authoring, "_sync_directory",
            return_value=("unconfirmed", "OSError: unsupported"),
        ):
            receipt = self.decode(
                authoring.write_transformation("10_value.metta", source)
            )

        self.assertEqual(receipt["state"], "installed")
        self.assertEqual(receipt["directory_sync"], "unconfirmed")
        self.assertEqual(
            (self.directory / "10_value.metta").read_text(encoding="utf-8"),
            source,
        )



if __name__ == "__main__":
    unittest.main()
