"""Emit the executable Python oracle for the native help/arity migration."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import command_arity  # noqa: E402
import helper  # noqa: E402
import skills_help  # noqa: E402


def oracle():
    names = sorted(set(skills_help.PAGES) | set(command_arity.ARITY))
    help_names = ["", " ", '""', '"pins"', "pins", "unknown", "unknown'name",
                  "task-resolve", "context-policy"] + names
    verdict_cases = [
        "", "atom", "()", "(pins)", "(pins extra)", "(shell ls)",
        '(shell "ls /tmp")', '(shell "echo hi" 2)',
        '(shell echo hi there)', '(unknown)', '(unknown "a b")',
        '(send "a b" (nested x))', '(send "unterminated)',
        '(send "a\\"b")', '(send "a\\\\b")',
        '(Error syntax_error bad)', '(syntax_error bad)', '(parse error bad)',
        'x' * 120, '(' + 'x' * 120 + ')',
    ]
    error_commands = [
        None, 1, "", [], (), [None], [1], ["pins"], ["pins", "extra"],
        ["shell"], ["shell", "ls", "2", "extra"], ["unknown"],
        ["request-page"], ["request-page", "0", "8", "extra"],
        ["rest-command"], ("pins", "extra"),
    ]
    bare_cases = ["", " ", "pins", "pins extra", "memory-health now",
                  "request-page 0 8", "rest-command now", "unknown", "pinsx",
                  "PINS", '"pins"', "(pins)", "pins\u3000x", "pins\tx",
                  "pins\nmore"]
    return {
        "base": "eaacea460bdea43cacad1afb5194b7f88406a06b",
        "page_count": len(skills_help.PAGES),
        "arity_count": len(command_arity.ARITY),
        "pages": skills_help.PAGES,
        "aliases": {name: skills_help.PAGES[name] for name in (
            "unpin", "write-file", "append-file", "send-image",
            "send-telegram-chat", "energy-set", "sibling-on", "sibling-status",
            "remember", "mcp-servers", "mcp-tools", "mcp-tool-info",
            "search-tavily", "search-ddg")},
        "arity": {name: list(bounds)
                  for name, bounds in sorted(command_arity.ARITY.items())},
        "equation_arities": {name: sorted(counts) for name, counts in
                             sorted(command_arity.equation_arities().items())},
        "equation_commands": list(command_arity.EQUATION_COMMANDS),
        "names_text": command_arity.names_text(),
        "help": {name: skills_help.help(name) for name in help_names},
        "verdicts": {case: skills_help.verdict(case, case)
                     for case in verdict_cases},
        "command_errors": [skills_help.command_error(command)
                           for command in error_commands],
        "command_error_inputs": error_commands,
        "bare": {case: helper._is_bare_command(case) for case in bare_cases},
        "split_top": {case: command_arity._split_top(case)
                      for case in verdict_cases},
    }


if __name__ == "__main__":
    json.dump(oracle(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
