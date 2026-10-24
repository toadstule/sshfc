#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for policy checks."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from sshfc.core.check import check_policy

if TYPE_CHECKING:
    from pathlib import Path


def _write(directory: Path, name: str, content: str) -> None:
    """Write a policy file."""
    (directory / name).write_text(textwrap.dedent(content))


def test_passing(tmp_path: Path) -> None:
    """Test a policy whose examples all hold."""
    _write(
        tmp_path,
        "a.toml",
        """
        [commands.x]
        help = "X."
        run = "/usr/bin/x"
        accept = [{ command = "x", argv = "/usr/bin/x" }]
        reject = ["x --all"]
        """,
    )
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == []
    assert result.warnings == []


def test_failing_examples(tmp_path: Path) -> None:
    """Test that every failing example is reported, with the file it came from."""
    _write(
        tmp_path,
        "a.toml",
        """
        [commands.x]
        help = "X."
        run = "/usr/bin/x"
        accept = ["x --all", { command = "x", argv = "/usr/bin/y" }, "y"]
        reject = ["x", "y"]
        """,
    )
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == [
        (
            "a.toml: commands.'x': accept example 'x --all' was rejected: "
            "SSH command rejected: x -- unrecognized arguments: --all"
        ),
        "a.toml: commands.'x': accept example 'x' would run '/usr/bin/x', expected '/usr/bin/y'",
        "a.toml: commands.'x': accept example 'y' must be a full command line from 'x'",
        "a.toml: commands.'x': reject example 'x' was accepted (would run '/usr/bin/x')",
        "a.toml: commands.'x': reject example 'y' must be a full command line from 'x'",
    ]


def test_other_file_widens_policy(tmp_path: Path) -> None:
    """Test that a file allowing what another file's examples expect to be rejected is caught."""
    _write(
        tmp_path,
        "a.toml",
        """
        [commands.podman]
        help = "Podman."
        run = "/usr/bin/podman"
        reject = ["podman rm web-1"]

        [commands."podman ps"]
        help = "List."
        """,
    )
    _write(
        tmp_path,
        "b.toml",
        """
        [commands.podman]
        help = "Podman."
        run = "/usr/bin/podman"

        [commands."podman rm"]
        help = "Remove."
        positionals = [{ name = "container", help = "Container.", validator = "container_name" }]
        """,
    )
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == [
        (
            "a.toml, b.toml: commands.'podman': reject example 'podman rm web-1' was accepted "
            "(would run '/usr/bin/podman rm -- web-1')"
        )
    ]


def test_load_error(tmp_path: Path) -> None:
    """Test that a policy that doesn't load is reported as an error."""
    _write(tmp_path, "a.toml", "[commands.x]\n")
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == ["a.toml: commands.'x': help must be a non-empty string"]
    assert result.policy is None


def test_missing_and_empty(tmp_path: Path) -> None:
    """Test a missing policy (an error) and an empty one (a warning: everything is rejected)."""
    assert check_policy(tmp_path / "missing", check_owners=False).errors == [
        f"{tmp_path / 'missing'}: no such file or directory"
    ]
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == []
    assert result.warnings == [f"{tmp_path}: no commands defined; every command will be rejected"]


def test_ownership_reported(tmp_path: Path) -> None:
    """Test that ownership problems are reported for the directory and each file (tests don't run as root)."""
    _write(tmp_path, "a.toml", '[commands.x]\nhelp = "X."\nrun = "/x"\n')
    errors = check_policy(tmp_path).errors
    assert errors[0] == f"{tmp_path}: must be owned by root"
    assert errors[1] == f"{tmp_path / 'a.toml'}: must be owned by root"


def test_unbalanced_quotes_in_examples(tmp_path: Path) -> None:
    """Test that examples with unbalanced quotes are matched by their first word and still checked."""
    _write(
        tmp_path,
        "a.toml",
        """
        [commands.x]
        help = "X."
        run = "/usr/bin/x"
        reject = ['x "unterminated', 'y "unterminated']
        """,
    )
    result = check_policy(tmp_path, check_owners=False)
    assert result.errors == [
        "a.toml: commands.'x': reject example 'y \"unterminated' must be a full command line from 'x'"
    ]
