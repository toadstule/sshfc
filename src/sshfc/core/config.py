#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Load the allowed command set from TOML policy files.

A policy is a single ``.toml`` file or a directory of them (read in sorted order; only ``*.toml`` names not starting
with ``.``). Each file has a flat ``[commands."<path>"]`` table per command, keyed by the full command path::

    [commands.podman]
    help = "Manage pods, containers and images."
    run = "/usr/bin/podman"

    [commands."podman ps"]
    help = "List containers."
    options."--format" = { help = "Go template.", go_template = ["ID", "Image"] }

Merging is order-independent: each command path is defined by one file, except that the same path may appear in
several files if every definition is identical (so independent files can share a parent such as ``podman``).
``accept``/``reject`` examples aren't part of the definition; they're combined.
"""

from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from sshfc.core.parser import CommandRoot, SpecError
from sshfc.core.spec import Command, Flag, Option, Positional
from sshfc.core.validators import NAMED, GoTemplateFormat, Pattern

if TYPE_CHECKING:
    from collections.abc import Collection

    from sshfc.core.spec import Validator

DEFAULT_PATH: Final = Path("/etc/sshfc/commands.d")

_ROOT_UID: Final = 0
_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_TOP_LEVEL_KEYS: Final = frozenset({"commands"})
_COMMAND_KEYS: Final = frozenset(
    {"help", "run", "end_of_options", "options", "flags", "positionals", "accept", "reject"}
)
_EXAMPLE_KEYS: Final = frozenset({"accept", "reject"})
_VALIDATOR_KEYS: Final = frozenset({"validator", "pattern", "go_template"})


class ConfigError(Exception):
    """Raised when the policy can't be loaded: missing, insecure, malformed, or inconsistent."""


@dataclass(frozen=True)
class Policy:
    """A loaded policy."""

    files: list[Path]
    commands: list[Command]
    root: CommandRoot


@dataclass
class _Entry:
    """One command path, merged across files."""

    definition: dict[str, Any]
    sources: list[str]
    accept: list[Any] = field(default_factory=list)
    reject: list[Any] = field(default_factory=list)


def policy_files(paths: list[Path]) -> list[Path]:
    """Return the policy files at paths (each a file, or a directory of ``*.toml`` files)."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.name.endswith(".toml") and not p.name.startswith(".")))
        elif path.is_file():
            files.append(path)
        else:
            msg = f"{path}: no such file or directory"
            raise ConfigError(msg)
    return files


def check_ownership(path: Path, trusted_uids: Collection[int] = (_ROOT_UID,)) -> None:
    """Check that path, its symlink target, and every ancestor directory can only be changed by a trusted user.

    Each must be owned by a trusted user (root) and not writable by group or others. A world-writable ancestor is
    accepted only if it has the sticky bit (e.g. /tmp), since others can't then replace what's inside it.

    Args:
        path: The path to check.
        trusted_uids: The UIDs that are considered trusted.
    """
    try:
        real = path.resolve(strict=True)
    except OSError as e:
        msg = f"{path}: {e.strerror}"
        raise ConfigError(msg) from e
    for depth, current in enumerate([real, *real.parents]):
        mode = current.stat().st_mode
        if current.stat().st_uid not in trusted_uids:
            msg = f"{current}: must be owned by root"
            raise ConfigError(msg)
        sticky_ancestor = depth > 0 and stat.S_ISDIR(mode) and mode & stat.S_ISVTX
        if mode & (stat.S_IWGRP | stat.S_IWOTH) and not sticky_ancestor:
            msg = f"{current}: must not be writable by group or others"
            raise ConfigError(msg)


def load_policy(
    paths: list[Path], *, check_owners: bool = True, trusted_uids: Collection[int] = (_ROOT_UID,)
) -> Policy:
    """Load, merge, and validate policies from multiple paths; raise ConfigError on any problem.

    Args:
        paths: The paths to policy files or directories.
        check_owners: Whether to check the ownership of the policy files or directories.
        trusted_uids: The UIDs that are considered trusted.

    Returns:
        The loaded policy.
    """
    files = policy_files(paths)
    if check_owners:
        for path in paths:
            check_ownership(path, trusted_uids)
        for file in files:
            check_ownership(file, trusted_uids)

    entries: dict[str, _Entry] = {}
    for file in files:
        for key, table in _read(file).items():
            _merge(entries, key, table, file.name)

    commands = [_build(entries, key) for key in sorted(entries) if " " not in key]
    for key, entry in entries.items():
        parent = key.rpartition(" ")[0]
        if parent and parent not in entries:
            msg = f"{_where(entry, key)}: parent command {parent!r} is not defined"
            raise ConfigError(msg)

    try:
        root = CommandRoot(commands)
    except SpecError as e:
        source = e.command.source if e.command is not None else None
        msg = f"{source}: {e}" if source else str(e)
        raise ConfigError(msg) from e
    return Policy(files=files, commands=commands, root=root)


def _read(file: Path) -> dict[str, Any]:
    """Read one policy file and return its commands table.

    Args:
        file: The path to the policy file.

    Returns:
        The commands table from the policy file.
    """
    try:
        with file.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        msg = f"{file.name}: {e}"
        raise ConfigError(msg) from e
    _check_keys(data, _TOP_LEVEL_KEYS, file.name)
    commands = data.get("commands", {})
    if not isinstance(commands, dict):
        msg = f"{file.name}: 'commands' must be a table"
        raise ConfigError(msg)
    return commands


def _merge(entries: dict[str, _Entry], key: str, table: Any, source: str) -> None:  # noqa: ANN401
    """Merge one file's definition of a command path into entries.

    Args:
        entries: The entries to merge into.
        key: The command path to merge.
        table: The table to merge.
        source: The source of the table.
    """
    where = f"{source}: commands.{key!r}"
    names = key.split(" ")
    if not all(_NAME.fullmatch(n) for n in names):
        msg = f"{where}: a command path is names separated by single spaces; names use letters, digits and _.-"
        raise ConfigError(msg)
    if not isinstance(table, dict):
        msg = f"{where}: must be a table"
        raise ConfigError(msg)
    definition = {k: v for k, v in table.items() if k not in _EXAMPLE_KEYS}
    entry = entries.get(key)
    if entry is None:
        entry = entries[key] = _Entry(definition=definition, sources=[source])
    elif entry.definition != definition:
        msg = (
            f"commands.{key!r} is defined differently in {entry.sources[0]} and {source}; "
            "a command shared by several files must be identical in each"
        )
        raise ConfigError(msg)
    else:
        entry.sources.append(source)
    entry.accept.extend(_list(table, "accept", where))
    entry.reject.extend(_list(table, "reject", where))


def _build(entries: dict[str, _Entry], key: str) -> Command:
    """Build the Command for a path, and its subcommands, from merged entries.

    Args:
        entries: The entries to build from.
        key: The command path to build.

    Returns:
        The built Command.
    """
    entry = entries[key]
    where = _where(entry, key)
    table = entry.definition
    _check_keys(table, _COMMAND_KEYS, where)

    args: list[Option | Flag | Positional] = []
    for name, arg in _table(table, "options", where).items():
        arg_where = f"{where}: options.{name!r}"
        required = _bool(_as_table(arg, arg_where), "required", arg_where, default=False)
        help_, validator = _validated_arg({k: v for k, v in arg.items() if k != "required"}, arg_where)
        args.append(Option(name=name, help=help_, validator=validator, required=required))
    for name, arg in _table(table, "flags", where).items():
        arg_where = f"{where}: flags.{name!r}"
        _check_keys(_as_table(arg, arg_where), frozenset({"help", "required"}), arg_where)
        required = _bool(arg, "required", arg_where, default=False)
        args.append(Flag(name=name, help=_str(arg, "help", arg_where, required=True), required=required))
    for index, arg in enumerate(_list(table, "positionals", where)):
        arg_where = f"{where}: positionals[{index}]"
        name = _str(_as_table(arg, arg_where), "name", arg_where, required=True)
        help_, validator = _validated_arg({k: v for k, v in arg.items() if k != "name"}, arg_where)
        args.append(Positional(name=name, help=help_, validator=validator))

    prefix = f"{key} "
    children = sorted(k for k in entries if k.startswith(prefix) and " " not in k.removeprefix(prefix))
    return Command(
        name=key.rpartition(" ")[2],
        help=_str(table, "help", where, required=True),
        run=_str(table, "run", where),
        args=args,
        subcommands=[_build(entries, child) for child in children],
        end_of_options=_bool(table, "end_of_options", where, default=True),
        accept=[_accept_example(ex, f"{where}: accept") for ex in entry.accept],
        reject=[_example_str(ex, f"{where}: reject") for ex in entry.reject],
        source=", ".join(entry.sources),
    )


def _validated_arg(arg: Any, where: str) -> tuple[str, Validator]:  # noqa: ANN401
    """Return (help, validator) for an option or positional table.

    Args:
        arg: The argument to validate.
        where: The location of the argument.

    Returns:
        A tuple of (help, validator).
    """
    table = _as_table(arg, where)
    _check_keys(table, frozenset({"help", "description"}) | _VALIDATOR_KEYS, where)
    help_ = _str(table, "help", where, required=True)
    kinds = sorted(_VALIDATOR_KEYS & table.keys())
    if len(kinds) != 1:
        msg = f"{where}: needs exactly one of {', '.join(sorted(_VALIDATOR_KEYS))}"
        raise ConfigError(msg)
    kind = kinds[0]
    if "description" in table and kind != "pattern":
        msg = f"{where}: 'description' is only used with 'pattern'"
        raise ConfigError(msg)

    if kind == "validator":
        name = _str(table, "validator", where, required=True)
        if name not in NAMED:
            msg = f"{where}: unknown validator {name!r} (choose from {', '.join(sorted(NAMED))})"
            raise ConfigError(msg)
        return help_, NAMED[name]
    if kind == "pattern":
        regex = _str(table, "pattern", where, required=True)
        try:
            re.compile(regex)
        except re.error as e:
            msg = f"{where}: invalid pattern: {e}"
            raise ConfigError(msg) from e
        return help_, Pattern(regex, _str(table, "description", where, required=True))
    fields = _str_list(table, "go_template", where)
    if not fields or not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", f) for f in fields):
        msg = f"{where}: go_template must be a non-empty list of field names"
        raise ConfigError(msg)
    template = GoTemplateFormat(fields)
    return f"{help_} Fields: {', '.join(template.fields)}.", template


def _accept_example(example: Any, where: str) -> str | tuple[str, str]:  # noqa: ANN401
    """Return an accept example: a command line, or a (command line, expected argv) pair.

    Args:
        example: The example to validate.
        where: The location of the example.

    Returns:
        A tuple of (command line, expected argv) or just the command line.
    """
    if isinstance(example, dict):
        _check_keys(example, frozenset({"command", "argv"}), where)
        return _str(example, "command", where, required=True), _str(example, "argv", where, required=True)
    return _example_str(example, where)


def _example_str(example: Any, where: str) -> str:  # noqa: ANN401
    """Return an example command line.

    Args:
        example: The example to validate.
        where: The location of the example.

    Returns:
        The example command line.
    """
    if not isinstance(example, str):
        msg = f"{where}: examples must be strings (or, for accept, {{ command = ..., argv = ... }} tables)"
        raise ConfigError(msg)
    return example


def _where(entry: _Entry, key: str) -> str:
    """Describe where a command is defined, for error messages.

    Args:
        entry: The entry to describe.
        key: The command path.

    Returns:
        A string describing where the command is defined.
    """
    return f"{', '.join(entry.sources)}: commands.{key!r}"


def _check_keys(table: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Reject unknown keys, so a typo can't be silently ignored.

    Args:
        table: The table to check.
        allowed: The allowed keys.
        where: The location of the table.

    Raises:
        ConfigError: If there are unknown keys.
    """
    if unknown := sorted(table.keys() - allowed):
        msg = f"{where}: unknown key(s) {', '.join(map(repr, unknown))} (allowed: {', '.join(sorted(allowed))})"
        raise ConfigError(msg)


def _as_table(value: Any, where: str) -> dict[str, Any]:  # noqa: ANN401
    """Return value if it's a table.

    Args:
        value: The value to check.
        where: The location of the value.

    Returns:
        The value if it's a table.

    Raises:
        ConfigError: If the value is not a table.
    """
    if not isinstance(value, dict):
        msg = f"{where}: must be a table"
        raise ConfigError(msg)
    return value


def _table(table: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    """Return an optional sub-table.

    Args:
        table: The table to check.
        key: The key to check.
        where: The location of the table.

    Returns:
        The sub-table if it exists, otherwise an empty table.
    """
    return _as_table(table.get(key, {}), f"{where}: {key}")


def _list(table: dict[str, Any], key: str, where: str) -> list[Any]:
    """Return an optional list.

    Args:
        table: The table to check.
        key: The key to check.
        where: The location of the table.

    Returns:
        The list if it exists, otherwise an empty list.

    Raises:
        ConfigError: If the value is not a list.
    """
    value = table.get(key, [])
    if not isinstance(value, list):
        msg = f"{where}: {key} must be a list"
        raise ConfigError(msg)
    return value


def _str_list(table: dict[str, Any], key: str, where: str) -> list[str]:
    """Return an optional list of strings.

    Args:
        table: The table to check.
        key: The key to check.
        where: The location of the table.

    Returns:
        The list of strings if it exists, otherwise an empty list.

    Raises:
        ConfigError: If the value is not a list of strings.
    """
    value = _list(table, key, where)
    if not all(isinstance(v, str) for v in value):
        msg = f"{where}: {key} must be a list of strings"
        raise ConfigError(msg)
    return value


def _str(table: dict[str, Any], key: str, where: str, *, required: bool = False) -> Any:  # noqa: ANN401
    """Return an optional (or required, non-empty) string.

    Args:
        table: The table to check.
        key: The key to check.
        where: The location of the table.
        required: Whether the string is required.

    Returns:
        The string if it exists, otherwise None.

    Raises:
        ConfigError: If the value is not a string or is empty when required.
    """
    value = table.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        msg = f"{where}: {key} must be a{' non-empty' if required else ''} string"
        raise ConfigError(msg)
    return value


def _bool(table: dict[str, Any], key: str, where: str, *, default: bool) -> bool:
    """Return an optional boolean.

    Args:
        table: The table to check.
        key: The key to check.
        where: The location of the table.
        default: The default value if the key is not found.

    Returns:
        The boolean value if it exists, otherwise the default.

    Raises:
        ConfigError: If the value is not a boolean.
    """
    value = table.get(key, default)
    if not isinstance(value, bool):
        msg = f"{where}: {key} must be true or false"
        raise ConfigError(msg)
    return value
