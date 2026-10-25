#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Strict parser built from command specs; renders the argv to run for an accepted command."""

from __future__ import annotations

import argparse
import shlex
from typing import TYPE_CHECKING, Any, Final, NoReturn

from sshfc.core.spec import Command, Flag, Option, Positional

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    # _SubParsersAction is only generic in the type stubs, not at runtime.
    _SubParsersActionBase = argparse._SubParsersAction[argparse.ArgumentParser]  # noqa: SLF001
else:
    _SubParsersActionBase = argparse._SubParsersAction  # noqa: SLF001

# Namespace keys used internally; prefixed so they can't collide with anything argparse derives.
_LEAF: Final = "_sshfc_leaf"
_ARG: Final = "_sshfc_arg_{index}"
_SUBCOMMAND: Final = "_sshfc_subcommand:{prog}"


class InvalidCommandError(Exception):
    """Raised when a command is invalid."""


class SpecError(Exception):
    """Raised when a command spec is malformed (a maintainer error, not an SSH user error)."""

    def __init__(self, message: str, command: Command | None = None) -> None:
        """Initialize the error with the command whose spec is malformed, if any."""
        super().__init__(message)
        self.command = command


class _ParseError(Exception):
    """Raised by _StrictArgumentParser instead of exiting."""

    def __init__(self, prog: str, message: str) -> None:
        """Initialize the error with the prog (subcommand path) of the parser that raised it."""
        super().__init__(message)
        self.prog = prog


class _SubcommandsAction(_SubParsersActionBase):
    """Subparsers action that gives each subcommand a clean prog and uses its help as its description."""

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser:  # noqa: ANN401
        """Add a subcommand parser."""
        # argparse joins the prefix and name with a space, which leaves a leading space when the root prog is "".
        kwargs.setdefault("prog", f"{self._prog_prefix} {name}".strip())
        if "help" in kwargs:
            kwargs.setdefault("description", kwargs["help"])
        return super().add_parser(name, **kwargs)


class _StrictArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never exits on error and never matches abbreviated options.

    Subparsers are created with the parent's class, so every parser in the tree inherits this behavior.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize the parser."""
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", _SubcommandsAction)

    def parse_known_args(  # type: ignore[override]
        self, args: Iterable[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        """Parse args, rejecting unrecognized args and requiring a subcommand wherever subcommands are defined.

        Both checks happen here, in the parser that owns the args, so the error names the right subcommand path.
        argparse's own ``required=True`` check only names the metavar; this lists the valid choices too.

        Args:
            args: The arguments to parse.
            namespace: The namespace to populate.

        Returns:
            A tuple of the parsed namespace and any extra arguments.
        """
        parsed, extras = super().parse_known_args(args, namespace)
        if extras:
            self.error(f"unrecognized arguments: {' '.join(extras)}")
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction) and getattr(parsed, action.dest, None) is None:  # noqa: SLF001
                choices = ", ".join(action.choices)
                self.error(f"the following arguments are required: {action.metavar} (choose from {choices})")
        # noinspection bad-return
        return parsed, extras

    def error(self, message: str) -> NoReturn:
        """Raise instead of printing usage and exiting."""
        raise _ParseError(self.prog, message)


class CommandRoot:
    """Parser for the allowed command set."""

    _REJECTED_MSG: Final = "SSH command rejected: {name} -- {error}"
    _REJECTED_NO_NAME_MSG: Final = "SSH command rejected: {error}"

    def __init__(self, commands: Sequence[Command]) -> None:
        """Build the parser from command specs; raise SpecError if a spec is malformed.

        Args:
            commands: The commands to parse.
        """
        self._parser = _StrictArgumentParser(prog="")
        group = self._add_subcommands(self._parser, metavar="command", title="available commands")
        self._check_unique([c.name for c in commands], "top-level commands", None)
        for command in commands:
            self._add_command(group, command, path=(), run=None)

    def parse(self, cmd_str: str) -> list[str]:
        """Validate a command line and return the argv to run; raise InvalidCommandError if it isn't allowed.

        Args:
            cmd_str: The command string to parse.

        Returns:
            The argv to run.
        """
        argv, _ = self.parse_command(cmd_str)
        return argv

    def parse_command(self, cmd_str: str) -> tuple[list[str], Command]:
        """Like parse(), but also return the matched leaf command (e.g. for its source).

        Args:
            cmd_str: The command string to parse.

        Returns:
            A tuple of (argv, command).
        """
        try:
            args = shlex.split(cmd_str)
        except ValueError as e:
            raise InvalidCommandError(self._REJECTED_NO_NAME_MSG.format(error=e)) from e
        try:
            namespace = self._parser.parse_args(args)
        except _ParseError as e:
            msg = (
                self._REJECTED_MSG.format(name=e.prog, error=e)
                if e.prog
                else self._REJECTED_NO_NAME_MSG.format(error=e)
            )
            raise InvalidCommandError(msg) from e
        path: tuple[Command, ...] = getattr(namespace, _LEAF)
        return self._render(namespace), path[-1]

    @staticmethod
    def _add_subcommands(
        parser: argparse.ArgumentParser, metavar: str = "subcommand", title: str = "available subcommands"
    ) -> argparse._SubParsersAction[argparse.ArgumentParser]:
        """Add a group of subcommands to a parser.

        The subcommand is required, but that is enforced by _StrictArgumentParser.parse_known_args() rather than
        argparse's ``required=True`` so the error message can list the valid choices.

        Args:
            parser: The parser to add the subcommands to.
            metavar: The metavar to use for the subcommands.
            title: The title to use for the subcommands.

        Returns:
            The subcommand group.
        """
        dest = _SUBCOMMAND.format(prog=parser.prog)
        return parser.add_subparsers(title=title, dest=dest, metavar=metavar)

    def _add_command(
        self,
        group: argparse._SubParsersAction[argparse.ArgumentParser],
        command: Command,
        path: tuple[Command, ...],
        run: str | None,
    ) -> None:
        """Add a command (and, recursively, its subcommands) to a subcommand group.

        Args:
            group: The subcommand group to add the command to.
            command: The command to add.
            path: The path to the command.
            run: The run command to use.
        """
        path = (*path, command)
        where = " ".join(c.name for c in path)
        if command.run is not None:
            if not command.run.startswith("/"):
                msg = f"{where}: run must be an absolute path, got {command.run!r}"
                raise SpecError(msg, command)
            run = command.run

        parser = group.add_parser(command.name, help=command.help)

        if command.subcommands:
            if command.args:
                msg = f"{where}: only leaf commands (without subcommands) may have args"
                raise SpecError(msg, command)
            self._check_unique([c.name for c in command.subcommands], f"{where} subcommands", command)
            sub_group = self._add_subcommands(parser)
            for subcommand in command.subcommands:
                self._add_command(sub_group, subcommand, path, run)
            return

        if run is None:
            msg = f"{where}: no run path (set run= on this command or an ancestor)"
            raise SpecError(msg, command)
        self._add_args(parser, command, where)
        parser.set_defaults(**{_LEAF: path})

    def _add_args(self, parser: argparse.ArgumentParser, command: Command, where: str) -> None:
        """Add a leaf command's options, flags and positionals to its parser.

        Args:
            parser: The parser to add the arguments to.
            command: The command to add the arguments for.
            where: The location of the command.
        """
        self._check_unique([a.name for a in command.args], f"{where} args", command)
        for index, arg in enumerate(command.args):
            dest = _ARG.format(index=index)
            match arg:
                case Option():
                    self._check_option_name(arg.name, where, command)
                    metavar = arg.name.lstrip("-").upper()
                    parser.add_argument(
                        arg.name, dest=dest, type=arg.validator, metavar=metavar, required=arg.required, help=arg.help
                    )
                case Flag():
                    self._check_option_name(arg.name, where, command)
                    parser.add_argument(arg.name, dest=dest, action="store_true", required=arg.required, help=arg.help)
                case Positional():
                    if arg.name.startswith("-"):
                        msg = f"{where}: positional names must not start with '-', got {arg.name!r}"
                        raise SpecError(msg, command)
                    parser.add_argument(dest, type=arg.validator, metavar=arg.name, help=arg.help)

    @staticmethod
    def _check_option_name(name: str, where: str, command: Command) -> None:
        """Check that an option or flag name looks like one.

        Args:
            name: The name to check.
            where: The location of the name.
            command: The command to check the name for.
        """
        if not name.startswith("-") or name in {"-", "--", "-h", "--help"}:
            msg = f"{where}: invalid option name {name!r}"
            raise SpecError(msg, command)

    @staticmethod
    def _check_unique(names: Sequence[str], where: str, command: Command | None) -> None:
        """Check that names are unique.

        Args:
            names: The names to check.
            where: The location of the names.
            command: The command to check the names for.
        """
        if duplicates := sorted({n for n in names if names.count(n) > 1}):
            msg = f"{where}: duplicate names {duplicates}"
            raise SpecError(msg, command)

    @staticmethod
    def _render(namespace: argparse.Namespace) -> list[str]:
        """Build the argv to run from the parsed namespace -- never from the original command string.

        Args:
            namespace: The parsed namespace.

        Returns:
            The argv to run.
        """
        path: tuple[Command, ...] = getattr(namespace, _LEAF)
        argv: list[str] = []
        for command in path:
            if command.run is not None:
                argv = [command.run]
            else:
                argv.append(command.name)

        leaf = path[-1]
        positionals: list[str] = []
        for index, arg in enumerate(leaf.args):
            value = getattr(namespace, _ARG.format(index=index))
            match arg:
                case Option() if value is not None:
                    argv.extend([arg.name, value])
                case Flag() if value:
                    argv.append(arg.name)
                case Positional():
                    positionals.append(str(value))
        if positionals:
            if leaf.end_of_options:
                argv.append("--")
            argv.extend(positionals)
        return argv
