"""Large catalogues remain fully reachable through small operator menu pages."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import helper


class MenuPagesTest(unittest.TestCase):
    def test_every_long_choice_is_reachable_and_callbacks_fit_the_transport(self):
        names = ["provider/" + "long-model-name-" * 8 + str(n) for n in range(500)]
        state = json.dumps({"names": names, "current": names[0], "selected": names[0], "text": "models"})
        seen, offset = [], 0
        with mock.patch.object(helper, "menu_state", return_value=state):
            while True:
                page = json.loads(helper.menu_page("models", "model", offset, provider="synthetic"))
                self.assertLess(len(page["rows"]), 100)
                next_offset = None
                for row in page["rows"]:
                    for _, callback in row:
                        self.assertLessEqual(len(callback.encode()), 64)
                        if callback.startswith("choice:"):
                            seen.append(helper.menu_choice_button(callback[len("choice:"):])[len("model:"):])
                        elif callback == "menu:model:synthetic:%d" % (offset + 24):
                            next_offset = offset + 24
                            self.assertEqual(json.loads(helper.menu_page_button(callback[len("menu:"):])),
                                             json.loads(helper.menu_page("models", "model", next_offset,
                                                                         provider="synthetic")))
                if next_offset is None:
                    break
                offset = next_offset
        self.assertEqual(seen, names)

    def test_provider_pages_separate_routes_and_keep_the_exact_model_choice(self):
        names = ["kimi:k3.1-pro", "hf:moonshotai/Kimi-K3.1-Pro", "claude-fixture",
                 "chatgpt:fixture", "openai:fixture"]
        state = json.dumps({"names": names, "current": names[0], "text": "catalog"})
        seen = []
        with mock.patch.object(helper, "menu_state", return_value=state):
            root = json.loads(helper.menu_page("models", "model"))
            self.assertEqual(len(root["rows"]), 5)
            self.assertIn("Kimi Code", root["rows"][0][0][0])
            for row in root["rows"]:
                callback = row[0][1]
                page = json.loads(helper.menu_page_button(callback[len("menu:"):]))
                choices = [value for buttons in page["rows"] for _, value in buttons
                           if value.startswith("model:")]
                self.assertEqual(len(choices), 1)
                seen.extend(value[len("model:"):] for value in choices)
                self.assertEqual(page["rows"][-1], [["Providers", "menu:model:providers"]])
                self.assertEqual(json.loads(helper.menu_page_button("model:providers")), root)
            self.assertEqual(seen, names)
            # A previously displayed flat page returns to provider selection;
            # it cannot select a model by its old position.
            self.assertEqual(json.loads(helper.menu_page_button("model:24")), root)
            with self.assertRaisesRegex(ValueError, "unknown model provider"):
                helper.menu_page_button("model:unrecognized:0")

    def test_expired_choices_fail_instead_of_selecting_a_different_model(self):
        with mock.patch.object(helper, "menu_state", return_value='{"names":[]}'):
            with self.assertRaisesRegex(ValueError, "expired or ambiguous"):
                helper.menu_choice_button("model:" + "a" * 40)


if __name__ == "__main__":
    unittest.main()
