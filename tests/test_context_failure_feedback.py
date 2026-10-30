"""Local preparation errors retain their stage, code and pending input."""
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import admission_frontier
import strategy_host
import synthetic_llm


class ContextFailureFeedbackTests(unittest.TestCase):
    def test_typed_code_survives_the_actual_health_reporting_path(self):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"SYNTHETIC_EMPTY_BACKOFF": "0"}), \
             mock.patch("provider_rejection.blocked", return_value=False), \
             mock.patch.object(synthetic_llm.cognitive_health, "turn_failed") as failed, \
             contextlib.redirect_stderr(stderr):
            result = strategy_host.preparation_failed("context-compaction-required")
        self.assertEqual(result, "()")
        failed.assert_called_once_with("context-compaction-required", retry_after=0.0)
        self.assertIn("[context] preparation withheld:", stderr.getvalue())
        self.assertNotIn("failed provider turn", stderr.getvalue())

    def test_arbitrary_exception_text_is_not_trusted_as_a_reason_code(self):
        error = RuntimeError("context-budget fixture-secret")
        self.assertEqual(admission_frontier.failure_code(error), "RuntimeError")

    def test_malformed_context_reason_is_replaced_without_reflecting_it(self):
        for text in ["context-budget https://fixture-secret.test", "context-" + "x" * 100,
                     "context-private\nAuthorization: fixture-secret"]:
            error = admission_frontier.ContextPreparationFailure(text)
            self.assertEqual(admission_frontier.failure_code(error), "context-preparation-failed")
            self.assertNotIn("fixture-secret", str(error))


if __name__ == "__main__":
    unittest.main()
