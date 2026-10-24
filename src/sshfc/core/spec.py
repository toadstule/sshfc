#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Declarative specification of allowed commands.

This is the entire API a command author needs. A command is a tree of ``Command`` nodes; leaves may take
``Option``/``Flag``/``Positional`` arguments. The infrastructure turns the tree into a strict parser and, for an
accepted command, renders the exact argv to run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Validates (and may canonicalize) one argument value; raise ``argparse.ArgumentTypeError`` to reject it.
type Validator = Callable[[str], str]


@dataclass(frozen=True)
class Option:
    """An option that takes a value, e.g. ``--format VALUE``. Rendered as ``[name, value]`` when given.

    ``required`` makes the SSH user type it, e.g. to bound a query (``journalctl --since``).
    """

    name: str
    help: str
    validator: Validator
    required: bool = False


@dataclass(frozen=True)
class Flag:
    """A boolean option, e.g. ``--all``. Rendered as ``[name]`` when given.

    ``required`` makes the SSH user type it, e.g. ``podman stats --no-stream``, so what runs is always what they
    typed rather than something sshfc adds silently.
    """

    name: str
    help: str
    required: bool = False


@dataclass(frozen=True)
class Positional:
    """A required positional argument. Rendered after the options (and after ``--``; see ``Command``)."""

    name: str
    help: str
    validator: Validator


@dataclass(frozen=True)
class Command:
    """A command or subcommand.

    Attributes:
        name: The name the SSH user types.
        help: One-line description, shown in help output.
        run: Absolute path of the program to run. Inherited by subcommands, whose names are appended to the argv,
            e.g. ``run="/usr/bin/podman"`` on ``podman`` makes ``podman pod ps`` run ``/usr/bin/podman pod ps``.
        args: Options, flags and positionals. Only leaf commands (no ``subcommands``) may have them.
        subcommands: Child commands. When present, one of them is required.
        end_of_options: Put ``--`` before positionals so a value can never be read as an option. Disable only for
            programs that don't understand ``--``.
        accept: Example command lines (full, from the top-level name) that must be accepted. Use a
            ``(command_line, expected_argv)`` tuple to also check the rendered argv. Run by ``sshfc --check``.
        reject: Example command lines that must be rejected. Run by ``sshfc --check``.
        source: Where this command was defined (e.g. a policy file name); set by the config loader, used in errors and
            audit records.
    """

    name: str
    help: str
    run: str | None = None
    args: Sequence[Option | Flag | Positional] = ()
    subcommands: Sequence[Command] = ()
    end_of_options: bool = True
    accept: Sequence[str | tuple[str, str]] = ()
    reject: Sequence[str] = ()
    source: str | None = field(default=None, compare=False)
