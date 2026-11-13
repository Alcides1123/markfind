"""The command-line parser as a whole: every command registered and documented.

`build_parser` hands each command to its own `_parser_<name>` function, so one
that is never called would disappear without a word. These tests are the net
under that, and under the documentation drifting away from the commands.
"""

import re
from pathlib import Path

import pytest

from markfind import cli

ROOT = Path(__file__).parent.parent
PARSER = cli.build_parser()
COMMANDS = sorted(PARSER._subparsers._group_actions[0].choices)


def subparser(name):
    return PARSER._subparsers._group_actions[0].choices[name]


def test_there_is_one_parser_function_per_command():
    defined = {name[len("_parser_") :] for name in vars(cli) if name.startswith("_parser_")}
    assert defined == set(COMMANDS)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_runs_a_handler_of_its_own_name(command):
    func = subparser(command).get_default("func")
    assert func is getattr(cli, f"cmd_{command}")
    assert func.__name__ == f"cmd_{command}"


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_prints_its_own_help(command, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main([command, "--help"])
    assert exit.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: markfind {command}")


def test_the_top_level_help_lists_them_all(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert re.search(rf"^    {command} ", out, re.MULTILINE), f"{command} has no help line"


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_is_in_the_reference(command):
    reference = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    assert f"\n## {command}\n" in reference, f"docs/cli.md has no section for {command}"
    assert f"[`{command}`](#{command})" in reference, f"{command} is missing from the command list"


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_is_in_the_readme(command):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"`markfind {command}" in readme, f"README.md doesn't mention {command}"
