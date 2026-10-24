#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for the audit log."""

from __future__ import annotations

import json
import os
import pwd
import syslog
from typing import TYPE_CHECKING

from sshfc.core.audit import AuditLog

if TYPE_CHECKING:
    import pytest


def _log(environ: dict[str, str] | None = None, key_id: str | None = None) -> tuple[AuditLog, list[tuple[int, str]]]:
    """Return an audit log that records into a list."""
    records: list[tuple[int, str]] = []
    return AuditLog(environ or {}, key_id=key_id, emit=lambda p, m: records.append((p, m))), records


def test_records() -> None:
    """Test the content and priority of each kind of record."""
    log, records = _log({"SSH_CONNECTION": "192.0.2.7 51234 198.51.100.1 22"}, key_id="alice")
    log.rejected(source="SSH_ORIGINAL_COMMAND", command="rm -rf /", reason="nope", dry_run=False)
    log.accepted(
        source="-c", command="podman ps", argv=["/usr/bin/podman", "ps"], policy="podman-readonly.toml", dry_run=True
    )
    log.exited(argv=["/usr/bin/podman", "ps"], status=3, duration=1.23456)
    log.config_error(source="SSH_ORIGINAL_COMMAND", command="pwd", error="x.toml: bad", dry_run=False)

    user = pwd.getpwuid(os.getuid()).pw_name
    context = {"user": user, "key_id": "alice", "client": "192.0.2.7 51234"}
    assert [(p, json.loads(m)) for p, m in records] == [
        (
            syslog.LOG_WARNING,
            {
                "event": "rejected",
                **context,
                "source": "SSH_ORIGINAL_COMMAND",
                "command": "rm -rf /",
                "reason": "nope",
                "dry_run": False,
            },
        ),
        (
            syslog.LOG_INFO,
            {
                "event": "accepted",
                **context,
                "source": "-c",
                "command": "podman ps",
                "argv": ["/usr/bin/podman", "ps"],
                "policy": "podman-readonly.toml",
                "dry_run": True,
            },
        ),
        (
            syslog.LOG_INFO,
            {"event": "exited", **context, "argv": ["/usr/bin/podman", "ps"], "status": 3, "duration_s": 1.235},
        ),
        (
            syslog.LOG_ERR,
            {
                "event": "config_error",
                **context,
                "source": "SSH_ORIGINAL_COMMAND",
                "command": "pwd",
                "error": "x.toml: bad",
                "dry_run": False,
            },
        ),
    ]


def test_missing_context() -> None:
    """Test that a local run (no SSH_CONNECTION, no --key-id) records nulls rather than failing."""
    log, records = _log()
    log.rejected(source="none", command="", reason="x", dry_run=False)
    record = json.loads(records[0][1])
    assert record["client"] is None
    assert record["key_id"] is None


def test_no_log_injection() -> None:
    """Test that control characters and non-ASCII in SSH-user input can't split or forge records."""
    log, records = _log()
    log.rejected(
        source="SSH_ORIGINAL_COMMAND",
        command='x\n{"event":"accepted"}\r\x1b[2J\x00é',
        reason="x",
        dry_run=False,
    )
    _, message = records[0]
    assert message.isascii()
    assert message.isprintable()
    assert json.loads(message)["command"] == 'x\n{"event":"accepted"}\r\x1b[2J\x00é'


def test_syslog_emitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that by default records go to syslog under the sshfc ident, at the given priority."""
    opened: list[tuple[str, int, int]] = []
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(syslog, "openlog", lambda ident, opt, fac: opened.append((ident, opt, fac)))
    monkeypatch.setattr(syslog, "syslog", lambda p, m: sent.append((p, m)))
    AuditLog({}).rejected(source="SSH_ORIGINAL_COMMAND", command="pwd", reason="nope", dry_run=False)
    assert opened == [("sshfc", syslog.LOG_PID, getattr(syslog, "LOG_AUTHPRIV", syslog.LOG_AUTH))]
    assert [(p, json.loads(m)["event"]) for p, m in sent] == [(syslog.LOG_WARNING, "rejected")]
