"""The actual MeTTa parser/dispatcher over a disposable terminal world."""

import json
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godel_shadow import FullShadow, LAUNCH_COMMAND  # noqa: E402


_saved_openai_key = None


def setUpModule():
    # PeTTa's lib_llm builds an OpenAI client when it is imported, and a
    # client without a key raises, which ends the shadow's program. The
    # shadow never calls the model, so any key will do; the process
    # environment is restored afterwards.
    global _saved_openai_key
    _saved_openai_key = os.environ.get("OPENAI_API_KEY")
    os.environ.setdefault("OPENAI_API_KEY", "test-only-unused")


def tearDownModule():
    if _saved_openai_key is None:
        os.environ.pop("OPENAI_API_KEY", None)
    else:
        os.environ["OPENAI_API_KEY"] = _saved_openai_key


class FullGodelShadowTests(unittest.TestCase):
    ENGINE = "petta"

    def run_ok(self, shadow, response, turn):
        result = shadow.execute_response(response, turn)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIn("FULL_SHADOW_RECORDS:", result.stdout)
        return result

    def observe(self, shadow, turn):
        self.run_ok(shadow, "((tmux-windows))", turn)
        return shadow.state()["receipts"]

    def room_receipt(self, shadow):
        return next(
            key for key, value in shadow.state()["receipts"].items()
            if value["window_name"] == "claude-room"
        )

    def test_complete_episode_crosses_real_parser_and_dispatcher(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            self.observe(shadow, 1)
            self.run_ok(
                shadow,
                '((tmux-new-shell-after "r1" "claude-room"))', 2,
            )
            self.observe(shadow, 3)
            room = self.room_receipt(shadow)
            self.run_ok(
                shadow,
                "((tmux-send-observed %s %s))" % (
                    json.dumps(room), json.dumps(LAUNCH_COMMAND)
                ),
                4,
            )
            self.observe(shadow, 5)
            room = self.room_receipt(shadow)
            self.run_ok(
                shadow,
                '((tmux-send-observed %s "yes"))' % json.dumps(room), 6,
            )
            self.observe(shadow, 7)
            room = self.room_receipt(shadow)
            self.run_ok(
                shadow,
                '((tmux-send-observed %s "lean-lsp"))' % json.dumps(room), 8,
            )
            self.run_ok(shadow, '((shadow-finish "done"))', 9)
            state = shadow.state()
            self.assertTrue(state["finished"])
            self.assertEqual(state["phase"], "goal-satisfied")
            self.assertEqual(state["effects"], 4)
            self.assertEqual(state["last_result"], "TASK_VERIFIED")
            phase_state = json.loads(
                shadow.task_phase_path.read_text(encoding="utf-8")
            )
            self.assertTrue(all(
                value["status"] == "completed"
                for value in phase_state["phases"].values()
            ))

    def test_shadow_denies_shell_without_falling_through(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = pathlib.Path(directory) / "must-not-exist"
            response = "((shell %s))" % json.dumps(
                "touch %s" % marker
            )
            with FullShadow(engine=self.ENGINE) as shadow:
                self.run_ok(shadow, response, 1)
                self.assertFalse(marker.exists())
                self.assertIn(
                    "SHADOW_DENIED unsupported-command shell",
                    shadow.state()["last_result"],
                )

    def test_unsupported_grounded_skill_is_inert_before_shadow_broker(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            self.run_ok(shadow, '((pin "must remain a proposal"))', 1)
            self.assertFalse((shadow.directory / "pins.txt").exists())
            self.assertIn(
                "SHADOW_DENIED unsupported-command pin",
                shadow.state()["last_result"],
            )

    def test_new_operator_stimulus_interrupts_effect_but_allows_observation(self):
        with FullShadow(auto_stop_after_first_effect=True,
                        engine=self.ENGINE) as shadow:
            self.observe(shadow, 1)
            response = (
                '((tmux-new-shell-after "r1" "claude-room") '
                '(tmux-windows))'
            )
            result = self.run_ok(shadow, response, 2)
            state = shadow.state()
            self.assertEqual(state["effects"], 1)
            self.assertEqual(len(state["trace"]), 3)  # observe, create, observe
            self.assertNotIn("COMMAND_BATCH_INTERRUPTED:", result.stdout)
            self.assertIn("COMMAND_RETURN:", result.stdout)

    def test_independent_receipt_batch_remains_permitted(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            receipts = self.observe(shadow, 1)
            by_window = {
                value["window_name"]: key for key, value in receipts.items()
            }
            response = (
                "((tmux-send-observed %s \":\") "
                "(tmux-send-observed %s \":\"))"
                % (json.dumps(by_window["code-log"]),
                   json.dumps(by_window["pleatta"]))
            )
            self.run_ok(shadow, response, 2)
            self.assertEqual(shadow.state()["effects"], 2)

    def test_prose_cannot_escape_the_shadow_boundary(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            self.run_ok(shadow, "I will do it now.", 1)
            state = shadow.state()
            self.assertEqual(state["effects"], 0)
            self.assertFalse(state["finished"])

    def test_failed_delete_returns_its_result_without_discarding_suffix(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            result = self.run_ok(
                shadow,
                '((delete-my-recent "research-group" 3) '
                '(send "Done — deleted the messages"))',
                1,
            )
            self.assertIn("SHADOW_DENIED unsupported-command", result.stdout)
            self.assertNotIn("reason failed_effect", result.stdout)
            self.assertIn("send Done — deleted the messages", result.stdout)
            self.assertEqual(shadow.state()["effects"], 0)

    def test_failed_send_returns_its_result_without_discarding_suffix(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            result = self.run_ok(
                shadow,
                '((send-telegram-chat "not-allowed" "report") '
                '(send "reported to the group"))',
                1,
            )
            self.assertIn("SHADOW_DENIED unsupported-command", result.stdout)
            self.assertNotIn("reason failed_effect", result.stdout)
            self.assertIn("send reported to the group", result.stdout)
            self.assertEqual(shadow.state()["effects"], 0)

    def test_corrupt_selector_cannot_fall_through_to_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = pathlib.Path(directory) / "must-not-exist"
            response = "((shell %s))" % json.dumps("touch %s" % marker)
            with FullShadow(backend_selector="tmux_shadow",
                            engine=self.ENGINE) as shadow:
                result = shadow.execute_response(response, 1)
                self.assertFalse(marker.exists())
                diagnostic = result.stdout + result.stderr
                self.assertTrue(result.returncode != 0 or "inconsistent effect backend configuration" in diagnostic, diagnostic[-1000:])
                # The failed begin may stop in Python or at the shared epoch
                # witness.  Seed a matching epoch next so this second probe
                # reaches and independently qualifies the broker guard.
                (shadow.directory / "stimulus-frontier").write_text(
                    "1 2 0 0\n", encoding="ascii"
                )
                depth_result = shadow.execute_response(
                    response, 2, begin_frontier=False
                )
                self.assertFalse(marker.exists())
                self.assertTrue(depth_result.returncode != 0 or
                    "inconsistent shadow effect configuration" in depth_result.stdout)

    def test_receipt_is_reserved_before_send_and_is_single_use(self):
        with FullShadow(engine=self.ENGINE) as shadow:
            receipts = self.observe(shadow, 1)
            receipt = next(iter(receipts))
            response = (
                "((tmux-send-observed %s \":\") "
                "(tmux-send-observed %s \":\"))"
                % (json.dumps(receipt), json.dumps(receipt))
            )
            result = self.run_ok(shadow, response, 2)
            state = shadow.state()
            self.assertEqual(state["effects"], 1)
            self.assertNotIn(receipt, state["receipts"])
            self.assertEqual(state["trace"][1]["status"], "sent")
            self.assertIn("unknown or expired observation receipt",
                          result.stdout)

    def test_post_send_timeout_is_witnessed_and_not_replayable(self):
        with FullShadow(prompt_timeout=0.1,
                        engine=self.ENGINE) as shadow:
            self.observe(shadow, 1)
            self.run_ok(
                shadow,
                '((tmux-new-shell-after "r1" "claude-room"))', 2,
            )
            self.observe(shadow, 3)
            receipt = self.room_receipt(shadow)
            state = shadow.state()
            state["launch_command"] = "printf NO_TRUST"
            shadow._write_state(state)
            self.run_ok(
                shadow,
                '((tmux-send-observed %s "printf NO_TRUST"))'
                % json.dumps(receipt),
                4,
            )
            state = shadow.state()
            self.assertEqual(state["effects"], 2)
            self.assertNotIn(receipt, state["receipts"])
            self.assertEqual(state["trace"][-1]["status"], "sent-unverified")
            self.assertIn("SENT_UNVERIFIED TimeoutError",
                          state["trace"][-1]["result"])
            self.run_ok(
                shadow,
                '((tmux-send-observed %s "printf AGAIN"))'
                % json.dumps(receipt),
                5,
            )
            self.assertEqual(shadow.state()["effects"], 2)


class FullGodelShadowCettaTests(FullGodelShadowTests):
    """Run every historical episode through ``cetta --lang petta`` too."""

    ENGINE = "cetta"

    @classmethod
    def setUpClass(cls):
        cetta = pathlib.Path(os.environ.get(
            "CETTA_BIN",
            pathlib.Path.home() / "repos" / "CeTTa-runtime" / "cetta",
        ))
        if not cetta.is_file():
            raise unittest.SkipTest("CeTTa executable is unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
