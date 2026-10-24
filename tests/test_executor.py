#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for the executor."""

from __future__ import annotations

import json
import os
import pwd
import sys
from typing import TYPE_CHECKING

from sshfc.core.executor import execute, minimal_env

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_minimal_env() -> None:
    """Test that PATH is fixed, identity comes from passwd, and only allowlisted variables pass through."""
    user = pwd.getpwuid(os.getuid())
    environ = {
        "PATH": "/evil:/usr/bin",
        "HOME": "/evil",
        "USER": "root",
        "LD_PRELOAD": "/evil.so",
        "SSH_ORIGINAL_COMMAND": "x",
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }
    assert minimal_env(environ) == {
        "PATH": "/usr/bin:/bin",
        "HOME": user.pw_dir,
        "USER": user.pw_name,
        "LOGNAME": user.pw_name,
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }


def test_execute_status() -> None:
    """Test that the program's exit status is returned."""
    assert execute([sys.executable, "-c", "import sys; sys.exit(3)"]) == 3


def test_execute_env(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    """Test that the program runs with the minimal environment and its output passes straight through."""
    monkeypatch.setenv("LD_PRELOAD", "")
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    assert execute([sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"]) == 0
    names = set(json.loads(capfd.readouterr().out))
    assert {"PATH", "HOME", "USER", "LOGNAME"} <= names
    assert "SECRET_TOKEN" not in names
    assert "LD_PRELOAD" not in names


def test_execute_signal() -> None:
    """Test that death by signal is reported as 128 + signal number, like a shell."""
    assert execute([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]) == 128 + 15


def test_execute_not_found(capfd: pytest.CaptureFixture[str]) -> None:
    """Test that a missing program is reported, not raised."""
    assert execute(["/nonexistent/sshfc-test"]) == 127
    assert capfd.readouterr().err == "SSH command failed: /nonexistent/sshfc-test: not found\n"


def test_execute_not_executable(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """Test that a non-executable program is reported, not raised."""
    program = tmp_path / "program"
    program.write_text("")
    assert execute([str(program)]) == 126
    assert capfd.readouterr().err == f"SSH command failed: {program}: permission denied\n"
