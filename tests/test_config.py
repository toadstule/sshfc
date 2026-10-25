#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for loading TOML policies."""

from __future__ import annotations

import os
import textwrap
from typing import TYPE_CHECKING

import pytest

from sshfc.core import InvalidCommandError
from sshfc.core.config import ConfigError, check_ownership, load_policy, policy_files

if TYPE_CHECKING:
    from pathlib import Path

_PODMAN = """
[commands.podman]
help = "Podman."
run = "/usr/bin/podman"
"""


def _write(directory: Path, name: str, content: str) -> Path:
    """Write a policy file."""
    path = directory / name
    path.write_text(textwrap.dedent(content))
    return path


def _load(directory: Path) -> list[str]:
    """Load a policy and return its top-level command names."""
    return [c.name for c in load_policy(directory, check_owners=False).commands]


def test_policy_files(tmp_path: Path) -> None:
    """Test that only *.toml files not starting with '.' are read, in sorted order."""
    for name in ["b.toml", "a.toml", ".hidden.toml", "c.toml~", "d.toml.rpmnew", "e.toml.dpkg-old", "README"]:
        (tmp_path / name).write_text("")
    assert [p.name for p in policy_files(tmp_path)] == ["a.toml", "b.toml"]
    assert policy_files(tmp_path / "a.toml") == [tmp_path / "a.toml"]
    with pytest.raises(ConfigError, match=r"no such file or directory"):
        policy_files(tmp_path / "missing")


def test_full_example(tmp_path: Path) -> None:
    """Test every kind of field, including how the result parses and renders."""
    _write(
        tmp_path,
        "p.toml",
        """
        [commands.tool]
        help = "A tool."
        run = "/usr/bin/tool"

        [commands."tool show"]
        help = "Show a thing."
        options."--format" = { help = "Format.", go_template = ["ID", "Name"] }
        options."--color" = { help = "Color.", pattern = "red|green", description = "red or green", required = false }
        options."--since" = { help = "Since.", pattern = "[a-z]+", description = "a word", required = true }
        flags."--all" = { help = "All." }
        flags."--no-pager" = { help = "No pager.", required = true }
        positionals = [{ name = "thing", help = "A thing.", validator = "container_name" }]
        accept = [
            "tool show --since=x --no-pager x",
            { command = "tool show --no-pager --since x x", argv = "/usr/bin/tool show --since x --no-pager -- x" },
        ]
        reject = ["tool show x"]

        [commands."tool raw"]
        help = "No end-of-options marker."
        end_of_options = false
        positionals = [{ name = "thing", help = "A thing.", validator = "container_name" }]
        """,
    )
    policy = load_policy(tmp_path, check_owners=False)
    tool = policy.commands[0]
    assert [c.name for c in tool.subcommands] == ["raw", "show"]
    show = tool.subcommands[1]
    assert show.source == "p.toml"
    assert show.accept == [
        "tool show --since=x --no-pager x",
        ("tool show --no-pager --since x x", "/usr/bin/tool show --since x --no-pager -- x"),
    ]
    assert show.reject == ["tool show x"]
    assert [(a.name, getattr(a, "required", None)) for a in show.args] == [
        ("--format", False),
        ("--color", False),
        ("--since", True),
        ("--all", False),
        ("--no-pager", True),
        ("thing", None),
    ]
    assert show.args[0].help == "Format. Fields: ID, Name."

    root = policy.root
    assert root.parse("tool show --no-pager --color=red --since=y --format '{{.Name}}' x") == [
        "/usr/bin/tool", "show", "--format", "{{.Name}}", "--color", "red", "--since", "y", "--no-pager", "--", "x",
    ]  # fmt: skip
    assert root.parse("tool raw x") == ["/usr/bin/tool", "raw", "x"]
    with pytest.raises(InvalidCommandError, match=r"expected red or green"):
        root.parse("tool show --color=blue --since=y --no-pager x")
    with pytest.raises(InvalidCommandError, match=r"the following arguments are required: --since, --no-pager"):
        root.parse("tool show x")


def test_sorted_commands(tmp_path: Path) -> None:
    """Test that commands are sorted by name at each level, whatever the file order."""
    _write(tmp_path, "a.toml", '[commands.zeta]\nhelp = "Z."\nrun = "/z"\n')
    _write(tmp_path, "b.toml", '[commands.alpha]\nhelp = "A."\nrun = "/a"\n')
    assert _load(tmp_path) == ["alpha", "zeta"]


def test_shared_parent(tmp_path: Path) -> None:
    """Test that identical definitions in several files merge, with examples combined and both sources recorded."""
    _write(tmp_path, "a.toml", _PODMAN + 'reject = ["podman rm x"]\n[commands."podman ps"]\nhelp = "List."\n')
    _write(tmp_path, "b.toml", _PODMAN + '[commands."podman stop"]\nhelp = "Stop."\n')
    podman = load_policy(tmp_path, check_owners=False).commands[0]
    assert podman.source == "a.toml, b.toml"
    assert podman.reject == ["podman rm x"]
    assert [(c.name, c.source) for c in podman.subcommands] == [("ps", "a.toml"), ("stop", "b.toml")]


def test_conflicting_definitions(tmp_path: Path) -> None:
    """Test that differing definitions of one path are an error naming both files."""
    _write(tmp_path, "a.toml", _PODMAN)
    _write(tmp_path, "b.toml", _PODMAN.replace("/usr/bin/podman", "/opt/podman"))
    with pytest.raises(ConfigError, match=r"commands\.'podman' is defined differently in a\.toml and b\.toml"):
        load_policy(tmp_path, check_owners=False)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        ("this is not toml", r"p\.toml: Expected '=' after a key"),
        ("[other]\nx = 1\n", r"p\.toml: unknown key\(s\) 'other'"),
        ("commands = 1\n", r"'commands' must be a table"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nfixed_arg = ["-q"]\n', r"unknown key\(s\) 'fixed_arg'"),
        ('[commands.x]\nrun = "/x"\n', r"commands\.'x': help must be a non-empty string"),
        ('[commands.x]\nhelp = " "\nrun = "/x"\n', r"help must be a non-empty string"),
        ('[commands.x]\nhelp = "X."\nrun = 1\n', r"run must be a string"),
        # fixed_args was removed: sshfc never adds arguments the user didn't type.
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nfixed_args = ["-q"]\n', r"unknown key\(s\) 'fixed_args'"),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\nflags."--a" = { help = "A.", required = "yes" }\n',
            r"flags\.'--a': required must be true or false",
        ),
        (
            (
                '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", validator = "container_name", '
                "required = 1 }\n"
            ),
            r"options\.'--a': required must be true or false",
        ),
        (
            (
                '[commands.x]\nhelp = "X."\nrun = "/x"\npositionals = [{ name = "a", help = "A.", '
                'validator = "container_name", required = true }]\n'
            ),
            r"positionals\[0\]: unknown key\(s\) 'required'",
        ),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nend_of_options = "no"\n', r"end_of_options must be true or false"),
        ('[commands."x  y"]\nhelp = "X."\n', r"names separated by single spaces"),
        ('[commands."x;y"]\nhelp = "X."\n', r"names separated by single spaces"),
        ('[commands."x y"]\nhelp = "Y."\nrun = "/y"\n', r"commands\.'x y': parent command 'x' is not defined"),
        ('[commands.x]\nhelp = "X."\nrun = "x"\n', r"p\.toml: x: run must be an absolute path"),
        ('[commands.x]\nhelp = "X."\n', r"p\.toml: x: no run path"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nflags.all = { help = "A." }\n', r"p\.toml: x: invalid option name"),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\nflags."--a" = { help = "A." }\n[commands."x y"]\nhelp = "Y."\n',
            r"p\.toml: x: only leaf commands",
        ),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nflags."--a" = { help = "A.", x = 1 }\n', r"unknown key\(s\) 'x'"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A." }\n', r"needs exactly one of"),
        (
            (
                '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", validator = "container_name", '
                'pattern = "a" }\n'
            ),
            r"needs exactly one of",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", validator = "anything" }\n',
            r"unknown validator 'anything' \(choose from container_name, journalctl_date, unit_name\)",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", pattern = "(" }\n',
            r"invalid pattern",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", pattern = "a" }\n',
            r"description must be a non-empty string",
        ),
        (
            (
                '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", validator = "container_name", '
                'description = "d" }\n'
            ),
            r"'description' is only used with 'pattern'",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", go_template = [] }\n',
            r"go_template must be a non-empty list of field names",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", go_template = ["json ."] }\n',
            r"go_template must be a non-empty list of field names",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\noptions."--a" = { help = "A.", go_template = ["ID", 1] }\n',
            r"options\.'--a': go_template must be a list of strings",
        ),
        (
            '[commands.x]\nhelp = "X."\nrun = "/x"\npositionals = [{ help = "A.", validator = "container_name" }]\n',
            r"positionals\[0\]: name must be a non-empty string",
        ),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\naccept = [1]\n', r"accept: examples must be strings"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\naccept = [{ command = "x" }]\n', r"argv must be a non-empty string"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\nreject = "x"\n', r"reject must be a list"),
        ("[commands]\nx = 1\n", r"commands.'x': must be a table"),
        ('[commands.x]\nhelp = "X."\nrun = "/x"\noptions = 1\n', r"options: must be a table"),
    ],
)
def test_invalid(tmp_path: Path, content: str, expected_error: str) -> None:
    """Test that malformed policies are rejected with an error naming the file and command."""
    _write(tmp_path, "p.toml", content)
    with pytest.raises(ConfigError, match=expected_error):
        load_policy(tmp_path, check_owners=False)


def test_empty_policy(tmp_path: Path) -> None:
    """Test that an empty directory loads as a policy that rejects everything."""
    policy = load_policy(tmp_path, check_owners=False)
    assert policy.commands == []
    with pytest.raises(InvalidCommandError):
        policy.root.parse("anything")


def test_ownership(tmp_path: Path) -> None:
    """Test the ownership and permission checks (trusting the current user, since tests don't run as root)."""
    me = (0, os.getuid())
    directory = tmp_path / "commands.d"
    directory.mkdir(mode=0o755)
    file = _write(directory, "p.toml", _PODMAN)
    file.chmod(0o644)
    check_ownership(directory, me)
    check_ownership(file, me)
    load_policy(directory, trusted_uids=me)

    with pytest.raises(ConfigError, match=r"p\.toml: must be owned by root"):
        check_ownership(file)

    file.chmod(0o664)
    with pytest.raises(ConfigError, match=r"p\.toml: must not be writable by group or others"):
        load_policy(directory, trusted_uids=me)
    file.chmod(0o644)

    directory.chmod(0o777)
    with pytest.raises(ConfigError, match=r"commands\.d: must not be writable by group or others"):
        check_ownership(file, me)
    directory.chmod(0o755)

    # A symlink is checked through to its target.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o777)  # Not via mkdir(mode=...), which the umask would mask.
    target = _write(elsewhere, "t.toml", _PODMAN)
    target.chmod(0o644)
    (directory / "link.toml").symlink_to(target)
    with pytest.raises(ConfigError, match=r"elsewhere: must not be writable by group or others"):
        load_policy(directory, trusted_uids=me)

    with pytest.raises(ConfigError, match=r"missing\.toml: No such file or directory"):
        check_ownership(directory / "missing.toml", me)
