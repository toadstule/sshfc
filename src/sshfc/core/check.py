#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Check a policy: load it and run every command's accept/reject examples against the merged result."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sshfc.core.config import ConfigError, check_ownership, load_policy, policy_files
from sshfc.core.parser import InvalidCommandError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from sshfc.core.config import Policy
    from sshfc.core.spec import Command


@dataclass
class CheckResult:
    """Problems found by check_policy()."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy: Policy | None = None


def check_policy(path: Path, *, check_owners: bool = True) -> CheckResult:
    """Check the policy at path, collecting every problem rather than stopping at the first.

    Args:
        path: The path to the policy file or directory.
        check_owners: Whether to check the ownership of the policy file or directory.

    Returns:
        A CheckResult object containing any errors or warnings.
    """
    result = CheckResult()
    try:
        files = policy_files(path)
    except ConfigError as e:
        result.errors.append(str(e))
        return result
    if check_owners:
        for checked in [path, *files]:
            try:
                check_ownership(checked)
            except ConfigError as e:
                result.errors.append(str(e))
    try:
        result.policy = load_policy(path, check_owners=False)
    except ConfigError as e:
        result.errors.append(str(e))
        return result

    if result.policy is not None:
        if not result.policy.commands:
            result.warnings.append(f"{path}: no commands defined; every command will be rejected")
        result.errors.extend(run_examples(result.policy))
    return result


def run_examples(policy: Policy) -> list[str]:
    """Run every accept/reject example against the whole policy; return a message for each failure.

    Args:
        policy: The policy to check.

    Returns:
        A list of failure messages.
    """
    failures: list[str] = []
    for where, command in _walk(policy.commands):
        top = where.split(" ", 1)[0]
        prefix = f"{command.source}: commands.{where!r}"
        for example in command.accept:
            cmd_str, expected = example if isinstance(example, tuple) else (example, None)
            if not _starts_with(cmd_str, top):
                failures.append(f"{prefix}: accept example {cmd_str!r} must be a full command line from {top!r}")
                continue
            try:
                argv = policy.root.parse(cmd_str)
            except InvalidCommandError as e:
                failures.append(f"{prefix}: accept example {cmd_str!r} was rejected: {e}")
                continue
            if expected is not None and shlex.join(argv) != expected:
                failures.append(
                    f"{prefix}: accept example {cmd_str!r} would run {shlex.join(argv)!r}, expected {expected!r}"
                )
        for cmd_str in command.reject:
            if not _starts_with(cmd_str, top):
                failures.append(f"{prefix}: reject example {cmd_str!r} must be a full command line from {top!r}")
                continue
            try:
                argv = policy.root.parse(cmd_str)
            except InvalidCommandError:
                continue
            failures.append(f"{prefix}: reject example {cmd_str!r} was accepted (would run {shlex.join(argv)!r})")
    return failures


def _starts_with(cmd_str: str, top: str) -> bool:
    """Return whether a command line starts with the given top-level command name.

    Args:
        cmd_str: The command line to check.
        top: The top-level command name to check for.

    Returns:
        True if the command line starts with the given top-level command name, False otherwise.
    """
    try:
        words = shlex.split(cmd_str)
    except ValueError:
        return cmd_str.split(" ", 1)[0] == top
    return bool(words) and words[0] == top


def _walk(commands: Sequence[Command], path: str = "") -> Iterator[tuple[str, Command]]:
    """Yield (path, command) for every command in the tree.

    Args:
        commands: The commands to walk.
        path: The current path.

    Yields:
        A tuple of (path, command).
    """
    for command in commands:
        here = f"{path} {command.name}".strip()
        yield here, command
        yield from _walk(command.subcommands, here)
