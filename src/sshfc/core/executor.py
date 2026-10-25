#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Run an accepted command's argv."""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_PATH: Final = "/usr/bin:/bin"

# Variables passed through from sshfc's own environment, if set. Everything else is dropped.
_PASSTHROUGH: Final = (
    "LANG",
    "LC_ALL",
    "TERM",
    "XDG_RUNTIME_DIR",  # Needed by rootless podman.
)

# Exit statuses for failures to start the program, following shell conventions.
_EXIT_CANNOT_EXECUTE: Final = 126
_EXIT_NOT_FOUND: Final = 127
_EXIT_SIGNAL_BASE: Final = 128


def minimal_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment to run commands with.

    PATH is fixed, and HOME/USER/LOGNAME come from the passwd entry rather than the inherited environment.

    Args:
        environ: The environment to use as a base.

    Returns:
        The environment to run commands with.
    """
    environ = os.environ if environ is None else environ
    user = pwd.getpwuid(os.getuid())
    env = {"PATH": _PATH, "HOME": user.pw_dir, "USER": user.pw_name, "LOGNAME": user.pw_name}
    env.update({name: environ[name] for name in _PASSTHROUGH if name in environ})
    return env


def execute(argv: Sequence[str]) -> int:
    """Run argv without a shell, with stdin/stdout/stderr passed through; return an exit status for sshfc.

    Args:
        argv: The command and its arguments.

    Returns:
        The exit status for sshfc.
    """
    try:
        # argv was rebuilt from a validated parse (never the raw command string), and no shell is involved.
        result = subprocess.run(argv, shell=False, env=minimal_env(), check=False)  # noqa: S603
    except FileNotFoundError:
        print(f"SSH command failed: {argv[0]}: not found", file=sys.stderr)
        return _EXIT_NOT_FOUND
    except PermissionError:
        print(f"SSH command failed: {argv[0]}: permission denied", file=sys.stderr)
        return _EXIT_CANNOT_EXECUTE
    if result.returncode < 0:  # Killed by a signal.
        return _EXIT_SIGNAL_BASE - result.returncode
    return result.returncode
