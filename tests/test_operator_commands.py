"""Each agent's command replies name that agent; the menu lists the declared commands."""

import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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


def fake_service(directory, private, bare=False):
    """A service binary whose usage does, or does not, offer --allow-private
    and the declaration of bare commands in groups."""
    path = pathlib.Path(directory) / ("service-%s-%s" % ("private" if private else "listed", "bare" if bare else "addressed"))
    usage = "Usage: cetta-telegram-service (--check | --run)%s%s" % (
        " [--allow-private]" if private else "", " --commands FILE: (tg-cmd:bare-in-groups)" if bare else "")
    path.write_text("#!/bin/sh\necho '%s'\n" % usage)
    path.chmod(0o755)
    return str(path)


class BareGroupCommandsTest(unittest.TestCase):
    """An operator's bare /command in a group is declared a command to a
    service that knows the declaration, unless the setting turns it off."""

    def declared(self, extra, capable=True, repo=ROOT):
        with tempfile.TemporaryDirectory() as directory:
            env = {"CETTA_TELEGRAM_SERVICE_BIN": fake_service(directory, True, capable),
                   "CETTA_TELEGRAM_SERVICE_ROOT": "/",
                   "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42", "METTACLAW_TELEGRAM_OPERATOR_IDS": "7"}
            env.update(extra)
            with mock.patch.dict(os.environ, env):
                for name in ("METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS", "METTACLAW_ACCESS_POLICY_PATH"):
                    if name not in extra:
                        os.environ.pop(name, None)
                argv = launcher.command(repo, pathlib.Path(directory) / "state", pathlib.Path(directory) / "token")
            text = pathlib.Path(argv[argv.index("--commands") + 1]).read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line and not line.startswith(";")]
        self.assertTrue(all(line.startswith("(tg-cmd:") for line in lines), lines)
        return lines.count(launcher.BARE_IN_GROUPS)

    def test_declared_by_default_to_a_service_that_knows_it(self):
        self.assertEqual(self.declared({}), 1)
        self.assertEqual(self.declared({"METTACLAW_AGENT_NAME": "Lila"}), 1)
        self.assertEqual(self.declared({"METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS": "1"}), 1)

    def test_never_declared_to_a_service_that_would_refuse_the_file(self):
        self.assertEqual(self.declared({}, capable=False), 0)
        self.assertEqual(self.declared({"METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS": "1"}, capable=False), 0)

    def test_the_setting_turns_it_off(self):
        for off in ("0", "false", "off", "no"):
            self.assertEqual(self.declared({"METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS": off}), 0, off)

    def test_the_instance_setting_is_read_from_its_rendered_environment(self):
        with tempfile.TemporaryDirectory() as repo:
            (pathlib.Path(repo) / "channels").mkdir()
            (pathlib.Path(repo) / "channels" / "telegram_commands.metta").write_text(
                (ROOT / "channels" / "telegram_commands.metta").read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(self.declared({}, repo=repo), 1)
            (pathlib.Path(repo) / ".env").write_text("export METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS='0'\n")
            self.assertEqual(self.declared({}, repo=repo), 0)
            self.assertEqual(self.declared({"METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS": "1"}, repo=repo), 1)

    def test_the_shared_file_does_not_declare_it(self):
        shared = (ROOT / "channels" / "telegram_commands.metta").read_text(encoding="utf-8")
        self.assertNotIn("\n" + launcher.BARE_IN_GROUPS, shared)

    def test_the_default_configuration_renders_it_on(self):
        import subprocess
        result = subprocess.run(["python3", str(ROOT / "scripts" / "config_to_env.py"),
                                 str(ROOT / "config" / "default.toml"), str(ROOT)],
                                check=True, capture_output=True, text=True)
        self.assertIn("export METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS='1'", result.stdout)

    @unittest.skipUnless(os.environ.get("CETTA_ROOT"), "set CETTA_ROOT to an engine bundle")
    def test_the_installed_service_accepts_what_is_written_for_it(self):
        # Whatever the bundle under test is, its own usage decides, and the
        # file written for it passes the service's own check.
        import subprocess
        service = pathlib.Path(os.environ["CETTA_ROOT"]) / "cetta-telegram-service"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            capable = launcher.service_takes_bare_commands(service)
            usage = subprocess.run([str(service), "--help"], capture_output=True, text=True).stdout
            self.assertEqual(capable, launcher.BARE_IN_GROUPS in usage)
            commands = launcher.write_commands(ROOT, root / "commands.metta", capable)
            token = root / "token"
            token.write_text("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
            token.chmod(0o600)
            state = root / "state"
            state.mkdir(mode=0o700)
            check = subprocess.run([str(service), "--check", "--root", os.environ["CETTA_ROOT"], "--state-dir", str(state),
                                    "--worker", "agent", "--chat", "42", "--operator", "7", "--program", "channel",
                                    "--credential-file", str(token), "--commands", str(commands)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            if not capable:
                refused = launcher.write_commands(ROOT, root / "refused.metta", True)
                check = subprocess.run([str(service), "--check", "--root", os.environ["CETTA_ROOT"],
                                        "--state-dir", str(state), "--worker", "agent", "--chat", "42",
                                        "--operator", "7", "--program", "channel", "--credential-file", str(token),
                                        "--commands", str(refused)], capture_output=True, text=True, timeout=60)
                self.assertEqual(check.returncode, 78, check.stdout + check.stderr)


class LauncherAccessTest(unittest.TestCase):
    """telegram.allow_private and the access policy's facts file reach the service."""

    def argv(self, extra, repo=ROOT, private_capable=True):
        with tempfile.TemporaryDirectory() as directory:
            env = {"CETTA_TELEGRAM_SERVICE_BIN": fake_service(directory, private_capable),
                   "CETTA_TELEGRAM_SERVICE_ROOT": os.environ.get("CETTA_ROOT", "/"),
                   "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42", "METTACLAW_TELEGRAM_OPERATOR_IDS": "7"}
            env.update(extra)
            with mock.patch.dict(os.environ, env):
                for name in ("METTACLAW_TELEGRAM_ALLOW_PRIVATE", "METTACLAW_ACCESS_POLICY_PATH"):
                    if name not in extra:
                        os.environ.pop(name, None)
                return launcher.command(repo, pathlib.Path(directory) / "state", pathlib.Path(directory) / "token")

    def test_private_chats_are_admitted_only_when_allowed(self):
        self.assertNotIn("--allow-private", self.argv({}))
        self.assertNotIn("--allow-private", self.argv({"METTACLAW_TELEGRAM_ALLOW_PRIVATE": "0"}))
        self.assertIn("--allow-private", self.argv({"METTACLAW_TELEGRAM_ALLOW_PRIVATE": "1"}))
        self.assertIn("--allow-private", self.argv({"METTACLAW_TELEGRAM_ALLOW_PRIVATE": "true"}))

    def test_a_service_without_the_flag_keeps_the_listed_chats(self):
        with mock.patch("sys.stderr") as stderr:
            argv = self.argv({"METTACLAW_TELEGRAM_ALLOW_PRIVATE": "1"}, private_capable=False)
        self.assertNotIn("--allow-private", argv)
        self.assertIn("cannot admit every private chat", "".join(c.args[0] for c in stderr.write.call_args_list))
        self.assertEqual({argv[i + 1] for i, a in enumerate(argv) if a == "--chat"}, {"42"})

    def test_capability_is_read_from_the_service_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(launcher.service_admits_private(fake_service(directory, True)))
            self.assertFalse(launcher.service_admits_private(fake_service(directory, False)))
            self.assertFalse(launcher.service_admits_private(pathlib.Path(directory) / "absent"))

    @unittest.skipUnless(os.environ.get("CETTA_ROOT"), "set CETTA_ROOT to an engine bundle")
    def test_the_installed_service_is_asked(self):
        # The bundle under test may predate --allow-private (2eefbdff does);
        # whatever it is, its own usage decides, and a fresh build offers it.
        service = pathlib.Path(os.environ["CETTA_ROOT"]) / "cetta-telegram-service"
        usage = __import__("subprocess").run([str(service), "--help"], capture_output=True, text=True).stdout
        self.assertEqual(launcher.service_admits_private(service), "--allow-private" in usage)

    def test_the_instance_setting_is_read_from_its_rendered_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            self.assertFalse(launcher.allow_private(repo))
            (repo / ".env").write_text("# Generated\nexport METTACLAW_TELEGRAM_ALLOW_PRIVATE='1'\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}):
                os.environ.pop("METTACLAW_TELEGRAM_ALLOW_PRIVATE", None)
                self.assertTrue(launcher.allow_private(repo))
                os.environ["METTACLAW_TELEGRAM_ALLOW_PRIVATE"] = "0"
                self.assertFalse(launcher.allow_private(repo))

    @unittest.skipUnless(os.environ.get("CETTA_ROOT"), "set CETTA_ROOT to an engine bundle")
    def test_the_facts_file_adds_chats_owners_and_private_chats(self):
        with tempfile.TemporaryDirectory() as directory:
            facts = pathlib.Path(directory) / "access.metta"
            facts.write_text('; who else\n(owner (sender telegram "66"))\n'
                             '(allow-sender everywhere (sender telegram "55"))\n'
                             '(access (group telegram "-400") groups open)\n'
                             '(access (channel telegram) direct open)\n', encoding="utf-8")
            argv = self.argv({"METTACLAW_ACCESS_POLICY_PATH": str(facts)})
            chats = {argv[i + 1] for i, a in enumerate(argv) if a == "--chat"}
            operators = {argv[i + 1] for i, a in enumerate(argv) if a == "--operator"}
            self.assertEqual(chats, {"42", "55", "66", "-400"})
            self.assertEqual(operators, {"7", "66"})
            self.assertIn("--allow-private", argv)
            facts.write_text('(access (channel telegram) direct allowlist)\n', encoding="utf-8")
            argv = self.argv({"METTACLAW_ACCESS_POLICY_PATH": str(facts), "METTACLAW_TELEGRAM_ALLOW_PRIVATE": "1"})
            self.assertNotIn("--allow-private", argv)
            facts.write_text('(owner (sender telegram 66))\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.argv({"METTACLAW_ACCESS_POLICY_PATH": str(facts)})


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
