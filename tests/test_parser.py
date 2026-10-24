#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for the parser infrastructure, using made-up command specs."""

from __future__ import annotations

import re

import pytest

from sshfc.core import Command, CommandRoot, Flag, InvalidCommandError, Option, Pattern, Positional, SpecError

_WORD = Pattern(r"[a-z]+", "a lowercase word")

_SPECS = [
    Command(
        "spam",
        "Spam spam spam spam.",
        run="/bin/spam",
        subcommands=[
            Command(
                "eggs",
                "Eggs.",
                subcommands=[
                    Command(
                        "ham",
                        "Ham.",
                        args=[
                            Option("--name", "A name.", _WORD),
                            Flag("--long", "Long output."),
                            Flag("-e", "Short flag e."),
                            Flag("-f", "Short flag f."),
                            Positional("item", "An item.", _WORD),
                        ],
                    ),
                ],
            ),
            Command("bare", "No args.", end_of_options=False, args=[Positional("item", "An item.", _WORD)]),
            Command(
                "strict",
                "Required options.",
                args=[
                    Option("--since", "Start.", _WORD, required=True),
                    Flag("--no-stream", "One snapshot.", required=True),
                    Flag("--all", "All."),
                ],
            ),
        ],
    ),
    Command("solo", "A top-level leaf.", run="/bin/solo"),
]


@pytest.fixture
def root() -> CommandRoot:
    """Build a command root from the made-up specs."""
    return CommandRoot(_SPECS)


@pytest.mark.parametrize(
    ("cmd_str", "expected_argv"),
    [
        ("solo", ["/bin/solo"]),
        ("spam eggs ham x", ["/bin/spam", "eggs", "ham", "--", "x"]),
        # Options are rendered in spec order and canonical form, whatever order/form the user typed.
        (
            "spam eggs ham x -fe --name=bob --long",
            ["/bin/spam", "eggs", "ham", "--name", "bob", "--long", "-e", "-f", "--", "x"],
        ),
        ("spam bare x", ["/bin/spam", "bare", "x"]),
        # Required options and flags are rendered just as the user typed them; nothing is added.
        ("spam strict --no-stream --since=today", ["/bin/spam", "strict", "--since", "today", "--no-stream"]),
        (
            "spam strict --all --since today --no-stream",
            ["/bin/spam", "strict", "--since", "today", "--no-stream", "--all"],
        ),
    ],
)
def test_render(root: CommandRoot, cmd_str: str, expected_argv: list[str]) -> None:
    """Test that accepted commands render the expected argv."""
    assert root.parse(cmd_str) == expected_argv


@pytest.mark.parametrize(
    ("cmd_str", "expected_error"),
    [
        ("", r"the following arguments are required: command \(choose from spam, solo\)$"),
        ("eggs", r"argument command: invalid choice: 'eggs' \(choose from spam, solo\)$"),
        ("spam 'unbalanced", r"No closing quotation$"),
        ("solo -x", r"solo -- unrecognized arguments: -x$"),
        ("solo --help-me", r"solo -- unrecognized arguments: --help-me$"),
        ("spam", r"spam -- the following arguments are required: subcommand \(choose from eggs, bare, strict\)$"),
        ("spam eggs", r"spam eggs -- the following arguments are required: subcommand \(choose from ham\)$"),
        ("spam beans", r"spam -- argument subcommand: invalid choice: 'beans'"),
        ("spam eggs beans", r"spam eggs -- argument subcommand: invalid choice: 'beans'"),
        ("spam --bad", r"spam -- unrecognized arguments: --bad$"),
        ("spam eggs --bad", r"spam eggs -- unrecognized arguments: --bad$"),
        ("spam eggs ham", r"spam eggs ham -- the following arguments are required: item$"),
        ("spam eggs ham x y", r"spam eggs ham -- unrecognized arguments: y$"),
        ("spam eggs ham x --bad", r"spam eggs ham -- unrecognized arguments: --bad$"),
        ("spam eggs ham x -ex", r"spam eggs ham -- unrecognized arguments: -x$"),
        # Required options and flags must be typed.
        ("spam strict", r"spam strict -- the following arguments are required: --since, --no-stream$"),
        ("spam strict --since today", r"spam strict -- the following arguments are required: --no-stream$"),
        ("spam strict --no-stream", r"spam strict -- the following arguments are required: --since$"),
        # Abbreviated options are not matched.
        ("spam eggs ham x --lo", r"spam eggs ham -- unrecognized arguments: --lo$"),
        # Validators apply to options and positionals.
        ("spam eggs ham X", r"spam eggs ham -- argument item: invalid value: 'X' \(expected a lowercase word\)$"),
        ("spam eggs ham x --name=B", r"spam eggs ham -- argument --name: invalid value: 'B'"),
        ("spam eggs ham x --name", r"spam eggs ham -- argument --name: expected one argument$"),
        # A value that looks like an option is never taken as a positional.
        ("spam eggs ham -- --long", r"spam eggs ham -- argument item: invalid value: '--long'"),
    ],
)
def test_reject(root: CommandRoot, cmd_str: str, expected_error: str) -> None:
    """Test that rejected commands get a consistently prefixed error naming the right subcommand path."""
    with pytest.raises(InvalidCommandError, match=rf"^SSH command rejected: {expected_error}"):
        root.parse(cmd_str)


def test_help(root: CommandRoot, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that -h shows a clean prog, the subcommand's help as its description, and the available subcommands."""
    with pytest.raises(SystemExit) as exc_info:
        root.parse("spam eggs -h")
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: spam eggs [-h] subcommand ...\n\nEggs.\n")
    assert re.search(r"^ +ham +Ham\.$", out, re.MULTILINE)


@pytest.mark.parametrize(
    ("specs", "expected_error"),
    [
        ([Command("x", "X.")], r"x: no run path"),
        ([Command("x", "X.", run="x")], r"x: run must be an absolute path"),
        ([Command("x", "X.", run="/x"), Command("x", "X.", run="/x")], r"top-level commands: duplicate names \['x'\]"),
        (
            [Command("x", "X.", run="/x", args=[Flag("--a", "A.")], subcommands=[Command("y", "Y.")])],
            r"x: only leaf commands",
        ),
        ([Command("x", "X.", run="/x", args=[Flag("a", "A.")])], r"x: invalid option name 'a'"),
        ([Command("x", "X.", run="/x", args=[Flag("-h", "A.")])], r"x: invalid option name '-h'"),
        ([Command("x", "X.", run="/x", args=[Positional("-a", "A.", _WORD)])], r"x: positional names must not"),
        ([Command("x", "X.", run="/x", args=[Flag("--a", "A."), Flag("--a", "A.")])], r"x args: duplicate names"),
        ([Command("x", "X.", subcommands=[Command("y", "Y.")])], r"x y: no run path"),
    ],
)
def test_spec_errors(specs: list[Command], expected_error: str) -> None:
    """Test that malformed specs are caught when the parser is built."""
    with pytest.raises(SpecError, match=expected_error):
        CommandRoot(specs)
