#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

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
from typing import TYPE_CHECKING

from sshfc.core.audit import AuditLog
from sshfc.core.check import check_policy
from sshfc.core.config import DEFAULT_PATH, ConfigError, load_policy
from sshfc.core.executor import execute
from sshfc.core.parser import InvalidCommandError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Shown to the SSH user when the policy can't be loaded; the details go to the audit log only.
_CONFIG_ERROR_MSG = "SSH command rejected: sshfc is misconfigured; contact the administrator"


def _parse_args(args: Sequence[str] | None) -> argparse.Namespace:
    """Parse sshfc's own arguments."""
    parser = argparse.ArgumentParser(
        prog="sshfc",
        description="Run an SSH user's requested command only if it is in the allowed command set.",
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
        help="List the sample policy files shipped with sshfc, or print one, then exit.",
    )
    options = parser.parse_args(args)
    if options.insecure_config and not (options.dry_run or options.check):
        parser.error("--insecure-config requires --dry-run or --check")
    return options


def main(args: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> None:
    """Validate the requested command, then run it (or, with --dry-run, show what would run)."""
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


def _check(path: Path, *, check_owners: bool) -> int:
    """Check a policy, print the results, and return an exit status."""
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
    """List the sample policy files, or print one; return an exit status."""
    samples = resources.files("sshfc") / "samples"
    files = sorted((f for f in samples.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name)
    if not name:
        print(f"Sample policy files in {samples}:")
        for file in files:
            first_line = file.read_text().partition("\n")[0].removeprefix("#").strip()
            print(f"  {file.name:24} {first_line}")
        print("\nCopy or symlink the ones you want into your policy directory, then run: sshfc --check")
        return 0
    for file in files:
        if name in {file.name, file.name.removesuffix(".toml")}:
            print(file.read_text(), end="")
            return 0
    print(f"sshfc: no sample named {name!r}; run 'sshfc --samples' to list them", file=sys.stderr)
    return 1
