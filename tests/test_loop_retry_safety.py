"""Static boundary checks for retry pacing and privacy-safe prompt logging."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LoopRetrySafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")

    def test_prompt_body_is_not_written_to_normal_log(self):
        self.assertNotIn(
            "(CHARS_SENT: (string_length $send) $send)", self.loop
        )
        self.assertIn(
            "(CHARS_SENT: (string_length $send)\n"
            "                                    sha256 (str:sha256 $send))",
            self.loop,
        )

    def test_provider_retry_uses_non_channel_backoff(self):
        self.assertIn(
            "(py-call (cognitive_health.wait_for_retry $retryWait))",
            self.loop,
        )
        retry = self.loop.index("($retryWait")
        wait = self.loop.index("($waitResult", retry)
        block = self.loop[retry:wait + 500]
        self.assertIn("(> $retryWait 0)", block)
        self.assertIn("(channel:sleep-until-message $wait $enabled)", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
