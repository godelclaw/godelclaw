import json
from pathlib import Path
import random
import string
import unittest

from src.wire import (
    MAX_INPUT_BYTES,
    MAX_NESTING,
    PATRICK_LINE_DECODER_COMMITS,
    WireError,
    WireErrorCode,
    decode_patrick_line_output,
    decode_sexpr_output,
    observe_model_output,
)


FIXTURES = Path(__file__).with_name("fixtures") / "command_wire.json"


class CommandWireFixtureTests(unittest.TestCase):
    def test_shared_fixtures(self):
        for fixture in json.loads(FIXTURES.read_text(encoding="utf-8")):
            with self.subTest(fixture["name"]):
                if "error" in fixture:
                    with self.assertRaises(WireError) as raised:
                        decode_sexpr_output(fixture["input"])
                    self.assertEqual(raised.exception.code.value, fixture["error"])
                    continue
                decoded = decode_sexpr_output(fixture["input"])
                self.assertEqual(list(decoded.actions), fixture["actions"])
                self.assertEqual(decoded.affect, fixture.get("affect"))
                self.assertEqual(
                    decoded.encoding, fixture.get("encoding", "sexpr-sequence")
                )

    def test_escaped_backslash_before_quote(self):
        value = r'(send "ends with slash \\")'
        decoded = decode_sexpr_output(value)
        self.assertEqual(decoded.actions, (value,))

    def test_pathological_nesting_is_rejected(self):
        value = "(" * (MAX_NESTING + 1) + "send" + ")" * (MAX_NESTING + 1)
        with self.assertRaises(WireError) as raised:
            decode_sexpr_output(value)
        self.assertEqual(raised.exception.code, WireErrorCode.SYNTAX)

    def test_input_limit_is_measured_in_bytes(self):
        value = "é" * (MAX_INPUT_BYTES // 2 + 1)
        with self.assertRaises(WireError) as raised:
            decode_sexpr_output(value)
        self.assertEqual(raised.exception.code, WireErrorCode.INPUT_TOO_LARGE)

    def test_more_than_five_actions_is_rejected(self):
        with self.assertRaises(WireError) as raised:
            decode_sexpr_output("\n".join("(pin x)" for _ in range(6)))
        self.assertEqual(raised.exception.code, WireErrorCode.TOO_MANY_ACTIONS)

    def test_malformed_affect_is_rejected(self):
        value = (
            '(send "hello")\n'
            "[Cn:no C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5]"
        )
        with self.assertRaises(WireError) as raised:
            decode_sexpr_output(value)
        self.assertEqual(raised.exception.code, WireErrorCode.INVALID_AFFECT)

    def test_large_trailing_garbage_is_not_silently_removed(self):
        with self.assertRaises(WireError) as raised:
            decode_sexpr_output('(send "ok")\n' + "x" * 4096)
        self.assertEqual(raised.exception.code, WireErrorCode.ACTION_NOT_EXPRESSION)

    def test_seeded_ast_print_decode_round_trip(self):
        rng = random.Random(20260714)
        alphabet = string.ascii_letters + string.digits + " ()[]\\\"\n"
        commands = ("send", "pin", "write-file", "query", "shell")
        for _ in range(250):
            count = rng.randint(1, 5)
            actions = tuple(
                f"({rng.choice(commands)} {json.dumps(''.join(rng.choice(alphabet) for _ in range(rng.randrange(40))))})"
                for _ in range(count)
            )
            decoded = decode_sexpr_output("\n".join(actions))
            self.assertEqual(decoded.actions, actions)


class PatrickLineDecoderTests(unittest.TestCase):
    def test_escaped_newline_cannot_create_another_command(self):
        decoded = decode_patrick_line_output('send hello_newline_shell echo injected')
        self.assertEqual(decoded.actions, ('(send "hello\\nshell echo injected")',))

    def test_provenance_names_both_patrick_commits(self):
        self.assertEqual(
            PATRICK_LINE_DECODER_COMMITS,
            (
                "edb1a811b06ff2ba54936c8e2ddcc72f3f1e2608",
                "0aa488eeb4dca3bd4032df5a742da8e1c2e7d13d",
            ),
        )

    def test_multiline_shell_blocks_from_corrected_patrick_parser(self):
        value = """shell cat <<'PYEOF' > /tmp/example.py
print("hello")
PYEOF
shell python /tmp/example.py"""
        decoded = decode_patrick_line_output(value)
        self.assertEqual(
            decoded.actions,
            (
                "(shell \"cat <<'PYEOF' > /tmp/example.py\\nprint(\\\"hello\\\")\\nPYEOF\")",
                '(shell "python /tmp/example.py")',
            ),
        )
        self.assertEqual(decoded.encoding, "patrick-lines")

    def test_write_file_has_two_arguments(self):
        decoded = decode_patrick_line_output(
            'write-file "proof.py" print(lambda x: x)'
        )
        self.assertEqual(
            decoded.actions,
            ('(write-file "proof.py" "print(lambda x: x)")',),
        )

    def test_json_quoted_filename_handles_escaped_quote(self):
        decoded = decode_patrick_line_output(
            'write-file "a\\\"b.txt" hello'
        )
        self.assertEqual(
            decoded.actions,
            ('(write-file "a\\\"b.txt" "hello")',),
        )

    def test_leading_prose_is_not_invented_as_a_command(self):
        with self.assertRaises(WireError) as raised:
            decode_patrick_line_output("Here is what I will do:\nsend hello")
        self.assertEqual(raised.exception.code, WireErrorCode.INVALID_ACTION_HEAD)

    def test_line_decoder_shares_five_action_limit(self):
        with self.assertRaises(WireError) as raised:
            decode_patrick_line_output("\n".join("pin x" for _ in range(6)))
        self.assertEqual(raised.exception.code, WireErrorCode.TOO_MANY_ACTIONS)

    def test_observation_is_sanitized_and_keeps_decoders_distinct(self):
        strict, patrick = observe_model_output("send hello")
        self.assertFalse(strict.accepted)
        self.assertEqual(strict.error_code, "ActionNotExpression")
        self.assertTrue(patrick.accepted)
        self.assertEqual(patrick.action_count, 1)
        self.assertEqual(patrick.encoding, "patrick-lines")
        self.assertNotIn("hello", repr((strict, patrick)))


if __name__ == "__main__":
    unittest.main()
