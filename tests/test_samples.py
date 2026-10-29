#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for the sample policy files shipped with sshfc."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import sshfc
from sshfc.core.check import check_policy, run_examples
from sshfc.core.config import load_policy

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sshfc.core import Command

SAMPLES = Path(sshfc.__file__).parent / "samples"
_FILES = sorted(SAMPLES.glob("*.toml"))


def _walk(commands: Sequence[Command], path: str = "") -> Iterator[tuple[str, Command]]:
    """Yield (path, command) for every command in the tree."""
    for command in commands:
        here = f"{path} {command.name}".strip()
        yield here, command
        yield from _walk(command.subcommands, here)


def test_all_samples_together() -> None:
    """Test that all the samples load together and every example holds against the merged policy."""
    result = check_policy([SAMPLES], check_owners=False)
    assert result.errors == []
    assert result.warnings == []


@pytest.mark.parametrize("sample", _FILES, ids=[f.name for f in _FILES])
def test_each_sample_alone(sample: Path, tmp_path: Path) -> None:
    """Test that each sample works on its own too, as when it's the only one dropped into a policy directory."""
    shutil.copy(sample, tmp_path)
    policy = load_policy([tmp_path], check_owners=False)
    assert run_examples(policy) == []

    # Every command in a shipped sample should have help text and be covered by examples.
    for where, command in _walk(policy.commands):
        assert command.help.strip(), f"{where}: missing help"
        for arg in command.args:
            assert arg.help.strip(), f"{where} {arg.name}: missing help"
        if not command.subcommands:
            assert command.accept, f"{sample.name}: {where}: add at least one accept example"


@pytest.mark.parametrize("sample", _FILES, ids=[f.name for f in _FILES])
def test_sample_description(sample: Path) -> None:
    """Test that each sample starts with the one-line comment `sshfc --samples` shows."""
    first_line = sample.read_text().partition("\n")[0]
    assert first_line.startswith("# ")
    assert len(first_line) <= 110
