#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Argument validators for command specs."""

from __future__ import annotations

import argparse
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


class Pattern:
    """Validator that accepts a value only if it fully matches a regex."""

    def __init__(self, regex: str, description: str) -> None:
        """Initialize the validator; ``description`` is shown to the SSH user when a value is rejected."""
        self._regex = re.compile(regex)
        self._description = description

    def __call__(self, value: str) -> str:
        """Validate a value."""
        if not self._regex.fullmatch(value):
            msg = f"invalid value: {value!r} (expected {self._description})"
            raise argparse.ArgumentTypeError(msg)
        return value


#: A podman/docker container, image, or pod name or ID (no tags, digests, or registry paths).
CONTAINER_NAME: Final = Pattern(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", "a container, image, or pod name or ID")


class GoTemplateFormat:
    """Argparse ``type=`` callable that accepts only a restricted subset of Go template syntax.

    A Go template is a small program (``{{json .}}``, ``{{range ...}}``, ``{{.Config.Env}}``, ...), so this does not
    try to recognize "safe" templates. Instead, it only accepts:

    - an optional leading ``table`` keyword,
    - placeholders of the exact form ``{{.Field}}`` (whitespace inside the braces allowed), where ``Field`` is in an
      explicit allowlist -- no functions, pipelines, nested fields, or control structures,
    - literal text between placeholders, made of a small set of characters that excludes ``{`` and ``}``, so literal
      text can never open a template action.

    The value returned to argparse is rebuilt from the parsed pieces, not the caller's original string.
    """

    _PLACEHOLDER: Final = re.compile(r"\{\{\s*\.([A-Za-z][A-Za-z0-9]*)\s*\}\}")
    _LITERAL: Final = re.compile(r"[A-Za-z0-9 \t:|,;=/._-]*")
    _TABLE_PREFIX: Final = re.compile(r"table\s+")

    def __init__(self, fields: Iterable[str], *, max_length: int = 256, max_fields: int = 16) -> None:
        """Initialize the validator with the allowed field names and size limits."""
        self._fields = frozenset(fields)
        self._max_length = max_length
        self._max_fields = max_fields
        self.__name__ = "format"  # Used by argparse in some error messages.

    @property
    def fields(self) -> list[str]:
        """Return the allowed field names, sorted."""
        return sorted(self._fields)

    def __call__(self, value: str) -> str:
        """Validate a format string and return a canonical copy of it."""
        if len(value) > self._max_length:
            msg = f"format is longer than {self._max_length} characters"
            raise argparse.ArgumentTypeError(msg)

        prefix = ""
        if table := self._TABLE_PREFIX.match(value):
            prefix = "table "
            value = value[table.end() :]

        parts: list[str] = []
        fields: list[str] = []
        pos = 0
        for match in self._PLACEHOLDER.finditer(value):
            parts.append(self._literal(value[pos : match.start()]))
            field = match.group(1)
            if field not in self._fields:
                msg = f"field not allowed: {field!r} (choose from {', '.join(self.fields)})"
                raise argparse.ArgumentTypeError(msg)
            fields.append(field)
            parts.append(f"{{{{.{field}}}}}")
            pos = match.end()
        parts.append(self._literal(value[pos:]))

        if not fields:
            msg = "format must contain at least one {{.Field}} placeholder"
            raise argparse.ArgumentTypeError(msg)
        if len(fields) > self._max_fields:
            msg = f"format has more than {self._max_fields} placeholders"
            raise argparse.ArgumentTypeError(msg)

        return prefix + "".join(parts)

    def _literal(self, text: str) -> str:
        """Validate literal text between placeholders."""
        if not self._LITERAL.fullmatch(text):
            msg = (
                f"invalid template text: {text!r} "
                "(only {{.Field}} placeholders, letters, digits, spaces, tabs and :|,;=/._- are allowed)"
            )
            raise argparse.ArgumentTypeError(msg)
        return text


#: Validators that policy files can refer to by name (``validator = "container_name"``).
NAMED: Final = {
    "container_name": CONTAINER_NAME,
}
