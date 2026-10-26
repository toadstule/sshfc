#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

# noinspection GrazieInspection
"""CLI entrypoint for sshfc.

sshfc's own arguments come from ``command="..."`` in authorized_keys (or ``ForceCommand`` in sshd_config), so they
are controlled by the admin, never by the SSH user. The SSH user's requested command comes from
``SSH_ORIGINAL_COMMAND``, or from ``-c`` for local testing.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Final

from sshfc.core.audit import AuditLog
from sshfc.core.check import check_policy
from sshfc.core.config import DEFAULT_PATH, ConfigError, load_policy
from sshfc.core.executor import execute
from sshfc.core.parser import InvalidCommandError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from sshfc.core.spec import Command

# Shown to the SSH user when the policy can't be loaded; the details go to the audit log only.
_CONFIG_ERROR_MSG = "SSH command rejected: sshfc is misconfigured; contact the administrator"

# Shown to the SSH user for "help" as the whole command. The command list is appended by _overview().
_OVERVIEW_INTRO: Final = """\
sshfc: restricted command access

This SSH account doesn't give you a shell. Each command is checked against an allowlist, and only the commands
listed below can run. Anything else is rejected, with the reason.

Send one command per connection, quoted as a single string:

  ssh HOST "COMMAND [OPTIONS] [ARGUMENTS]"

Nothing is interpreted by a shell. Pipes (|), redirects (>), ; and &&, variables ($HOME), and globs (*) are not special:
they are rejected, or passed to the command as plain text if its rules allow it.

Finding out more:

  help           This message.
  COMMAND -h     Help for a command or group: its subcommands, options, flags and arguments.
                 For example, "podman -h" lists the podman subcommands, and "podman ps -h" shows the options for ps.

Rules:

  - A rejected command prints "SSH command rejected:" followed by the reason, and exits with status 1.
  - Options must be spelled out as -h shows them. Abbreviations such as --for for --format are not accepted.
  - Options and flags marked as required must be included.
  - Otherwise, the exit status is the command's own.

Available commands:
"""
_OVERVIEW_OUTRO: Final = "\nNeed something that isn't listed? Ask the administrator.\n"


def _parse_args(args: Sequence[str] | None) -> argparse.Namespace:
    """Parse sshfc's own arguments.

    Args:
        args: The arguments to parse.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="sshfc",
        description="SSHfc: run an SSH user's requested command only if it is in the allowed command set.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_PATH,
        metavar="PATH",
        help=f"Policy file, or directory of *.toml policy files (default: {DEFAULT_PATH}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the command that would run instead of running it."
    )
    parser.add_argument(
        "-c",
        dest="command",
        metavar="COMMAND",
        help="Validate COMMAND instead of $SSH_ORIGINAL_COMMAND (for local testing). Quote it as one string.",
    )
    parser.add_argument(
        "--key-id",
        metavar="LABEL",
        help="Label included in audit records, e.g. to tell apart keys that share an account.",
    )
    parser.add_argument(
        "--insecure-config",
        action="store_true",
        help="Skip the policy ownership checks (only with --dry-run or --check, e.g. to try a policy in a checkout).",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check",
        action="store_true",
        help="Check the policy and run every command's accept/reject examples, then exit.",
    )
    modes.add_argument(
        "--samples",
        nargs="?",
        const="",
        metavar="NAME",
        help="List the sample policy files shipped with SSHfc, or print one, then exit.",
    )
    options = parser.parse_args(args)
    if options.insecure_config and not (options.dry_run or options.check):
        parser.error("--insecure-config requires --dry-run or --check")
    return options


def main(args: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> None:
    """Validate the requested command, then run it (or, with --dry-run, show what would run).

    Args:
        args: The arguments to parse.
        environ: The environment to use as a base.
    """
    options = _parse_args(args)
    if options.samples is not None:
        raise SystemExit(_samples(options.samples))
    if options.check:
        raise SystemExit(_check(options.config, check_owners=not options.insecure_config))

    environ = os.environ if environ is None else environ
    audit = AuditLog(environ, key_id=options.key_id)

    # No command at all (e.g. an interactive login) is rejected like any other invalid command, listing the choices.
    if options.command is not None:
        source, cmd_str = "-c", options.command
    elif "SSH_ORIGINAL_COMMAND" in environ:
        source, cmd_str = "SSH_ORIGINAL_COMMAND", environ["SSH_ORIGINAL_COMMAND"]
    else:
        source, cmd_str = "none", ""

    # Fail closed: if the policy can't be loaded, reject everything.
    try:
        policy = load_policy(options.config, check_owners=not options.insecure_config)
    except ConfigError as e:
        audit.config_error(source=source, command=cmd_str, error=str(e), dry_run=options.dry_run)
        raise SystemExit(_CONFIG_ERROR_MSG) from e

    if _is_help_request(cmd_str) or not cmd_str.strip():
        audit.help_requested(source=source, command=cmd_str, dry_run=options.dry_run)
        print(_overview(policy.commands), end="")
        return

    try:
        argv, command = policy.root.parse_command(cmd_str)
    except InvalidCommandError as e:
        audit.rejected(source=source, command=cmd_str, reason=str(e), dry_run=options.dry_run)
        raise SystemExit(str(e)) from e

    audit.accepted(source=source, command=cmd_str, argv=argv, policy=command.source, dry_run=options.dry_run)
    if options.dry_run:
        print(f"Would run: {shlex.join(argv)}")
        return

    start = time.monotonic()
    status = execute(argv)
    audit.exited(argv=argv, status=status, duration=time.monotonic() - start)
    raise SystemExit(status)


def _is_help_request(cmd_str: str) -> bool:
    """Return whether the requested command is exactly ``help``."""
    try:
        return shlex.split(cmd_str) == ["help"]
    except ValueError:
        return False


def _overview(commands: Sequence[Command]) -> str:
    """Return the overview shown for ``--help``: how the environment works, then every command path with its help."""
    entries = list(_command_paths(commands))
    width = max((len(path) for path, _ in entries), default=0)
    listing = "".join(f"  {path:<{width}}  {help_}\n" for path, help_ in entries)
    return _OVERVIEW_INTRO + listing + _OVERVIEW_OUTRO


def _command_paths(commands: Sequence[Command], parent: str = "") -> Iterator[tuple[str, str]]:
    """Yield (full command path, help) for every command in the tree, parents before their subcommands."""
    for command in commands:
        path = f"{parent} {command.name}".strip()
        yield path, command.help
        yield from _command_paths(command.subcommands, path)


def _check(path: Path, *, check_owners: bool) -> int:
    """Check a policy, print the results, and return an exit status.

    Args:
        path: The path to check.
        check_owners: Whether to check the owners of the files.

    Returns:
        The exit status.
    """
    result = check_policy(path, check_owners=check_owners)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.errors:
        return 1
    assert result.policy is not None  # noqa: S101 -- no errors means the policy loaded.
    names = ", ".join(f.name for f in result.policy.files)
    print(f"OK: {path} ({len(result.policy.files)} file(s): {names})")
    return 0


def _samples(name: str) -> int:
    """List the sample policy files, or print one; return an exit status.

    Args:
        name: The name of the sample to print.

    Returns:
        The exit status.
    """
    samples = resources.files("sshfc") / "samples"
    files = sorted((f for f in samples.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name)
    if not name:
        print(f"Sample policy files in {samples}:")
        for file in files:
            first_line = file.read_text().partition("\n")[0].removeprefix("#").strip()
            print(f"  {file.name:24} {first_line}")
        print("\nCopy the ones you want into your policy directory, then run: sshfc --check")
        return 0
    for file in files:
        if name in {file.name, file.name.removesuffix(".toml")}:
            print(file.read_text(), end="")
            return 0
    print(f"sshfc: no sample named {name!r}; run 'sshfc --samples' to list them", file=sys.stderr)
    return 1
