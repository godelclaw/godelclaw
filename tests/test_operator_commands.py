"""Each agent's command replies name that agent; /start undoes a pause."""

import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sibling  # noqa: E402
import start_telegram_service as launcher  # noqa: E402


def declared_names(text):
    return re.findall(r'^\(tg-cmd:agent "([^"]*)"\)', text, re.MULTILINE)


class CommandNameTest(unittest.TestCase):
    def write(self, name):
        with tempfile.TemporaryDirectory() as directory:
            out = pathlib.Path(directory) / "state" / "commands.metta"
            env = {"METTACLAW_AGENT_NAME": name} if name is not None else {}
            with mock.patch.dict(os.environ, env, clear=False):
                if name is None:
                    os.environ.pop("METTACLAW_AGENT_NAME", None)
                launcher.write_commands(ROOT, out)
            return out.read_text(encoding="utf-8")

    def test_shared_file_names_no_agent(self):
        shared = (ROOT / "channels" / "telegram_commands.metta").read_text(encoding="utf-8")
        self.assertEqual(declared_names(shared), [])
        self.assertNotIn("Lila", shared)

    def test_each_agent_gets_its_own_name(self):
        godel = self.write("Gödel")
        lila = self.write("Lila")
        self.assertEqual(declared_names(godel), ["Gödel"])
        self.assertEqual(declared_names(lila), ["Lila"])

    def test_unusable_name_is_left_out(self):
        for name in (None, "", 'Bad"Name', "back\\slash", "x" * 33, "tab\there"):
            self.assertEqual(declared_names(self.write(name)), [], repr(name))

    def test_service_command_line_uses_the_written_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory) / "state"
            env = {"CETTA_TELEGRAM_SERVICE_BIN": "/bin/true", "CETTA_TELEGRAM_SERVICE_ROOT": "/",
                   "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42", "METTACLAW_TELEGRAM_OPERATOR_IDS": "7",
                   "METTACLAW_AGENT_NAME": "Gödel"}
            with mock.patch.dict(os.environ, env):
                argv = launcher.command(ROOT, state, pathlib.Path(directory) / "token")
            path = pathlib.Path(argv[argv.index("--commands") + 1])
            self.assertEqual(path, state / "commands.metta")
            self.assertEqual(declared_names(path.read_text(encoding="utf-8")), ["Gödel"])


class ResumeSelfTest(unittest.TestCase):
    def test_start_clears_own_pause_and_starts_the_service(self):
        with tempfile.TemporaryDirectory() as directory:
            body = pathlib.Path(directory) / "pettaclaw-godel"
            body.mkdir()
            marker = body / "PAUSED-BY-OPERATOR.md"
            marker.write_text("paused by sibling\n", encoding="utf-8")
            states = iter(["inactive", "active"])
            calls = []

            def systemctl(*args):
                calls.append(args)
                if args[0] == "is-active":
                    return 0, next(states)
                return 0, ""

            claws = {"godel": {"body": str(body), "unit": "pettaclaw-godel.service", "label": "Gödel"}}
            with mock.patch.object(sibling, "_CLAWS", claws), \
                    mock.patch.object(sibling, "_me", return_value="godel"), \
                    mock.patch.object(sibling, "_systemctl", side_effect=systemctl):
                report = sibling.resume_self()
            self.assertFalse(marker.exists())
            self.assertIn(("start", "pettaclaw-godel.service"), calls)
            self.assertEqual(report, "pause marker cleared; service active")

    def test_running_agent_is_left_alone(self):
        with mock.patch.object(sibling, "_me", return_value="godel"), \
                mock.patch.object(sibling, "_marker", return_value="/nonexistent/marker"), \
                mock.patch.object(sibling, "_systemctl", return_value=(0, "active")) as systemctl:
            self.assertEqual(sibling.resume_self(), "service already active")
            self.assertEqual([c.args[0] for c in systemctl.call_args_list], ["is-active"])


if __name__ == "__main__":
    unittest.main()


class CommandMenuTest(unittest.TestCase):
    def test_menu_lists_every_declared_command_including_help(self):
        shared = (ROOT / "channels" / "telegram_commands.metta").read_text(encoding="utf-8")
        names = [c["command"] for c in launcher.menu_commands(shared)]
        self.assertEqual(names[0], "help")
        self.assertIn("stop", names)
        self.assertIn("model", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), shared.count("(tg-cmd:command "))

    def test_menu_registration_failure_never_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = pathlib.Path(directory) / "commands.metta"
            commands.write_text('(tg-cmd:command "/help" help "List these commands")\n', encoding="utf-8")
            token = pathlib.Path(directory) / "token"
            token.write_text("123:secret\n", encoding="utf-8")
            with mock.patch.object(launcher.urllib.request, "urlopen", side_effect=OSError("offline")):
                self.assertFalse(launcher.register_menu(commands, token))

    def test_menu_registration_sends_the_declared_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = pathlib.Path(directory) / "commands.metta"
            commands.write_text('(tg-cmd:command "/help" help "List these commands")\n'
                                '(tg-cmd:command "/stop" stop "Stop cognition")\n', encoding="utf-8")
            token = pathlib.Path(directory) / "token"
            token.write_text("123:secret\n", encoding="utf-8")
            sent = {}

            class Response:
                def __enter__(self): return self
                def __exit__(self, *exc): return False
                def read(self): return b'{"ok": true, "result": true}'

            def urlopen(request, timeout):
                sent["url"], sent["body"] = request.full_url, __import__("json").loads(request.data)
                return Response()

            with mock.patch.object(launcher.urllib.request, "urlopen", side_effect=urlopen):
                self.assertTrue(launcher.register_menu(commands, token))
            self.assertTrue(sent["url"].endswith("/bot123:secret/setMyCommands"))
            self.assertEqual([c["command"] for c in sent["body"]["commands"]], ["help", "stop"])
