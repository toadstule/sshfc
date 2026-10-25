#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Audit log of accepted and rejected commands.

Each record is one JSON line sent to syslog (facility authpriv where available, else auth; ident ``sshfc``). The
command string is SSH-user input, so JSON encoding with ASCII escaping is also what prevents log injection: control
characters and newlines can't forge or split records.
"""

from __future__ import annotations

import json
import os
import pwd
import syslog
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

#: Receives (syslog priority, message).
type Emitter = Callable[[int, str], None]

_FACILITY: Final = getattr(syslog, "LOG_AUTHPRIV", syslog.LOG_AUTH)


def _syslog_emit(priority: int, message: str) -> None:
    """Send a record to syslog."""
    syslog.openlog("sshfc", syslog.LOG_PID, _FACILITY)
    syslog.syslog(priority, message)


class AuditLog:
    """Writes audit records for one sshfc invocation."""

    def __init__(self, environ: Mapping[str, str], key_id: str | None = None, emit: Emitter | None = None) -> None:
        """Initialize with the context common to every record of this invocation."""
        self._emit = _syslog_emit if emit is None else emit
        client = environ.get("SSH_CONNECTION", "").split()  # "client_ip client_port server_ip server_port"
        self._context: dict[str, Any] = {
            "user": pwd.getpwuid(os.getuid()).pw_name,
            "key_id": key_id,
            "client": " ".join(client[:2]) or None,
        }

    def rejected(self, *, source: str, command: str, reason: str, dry_run: bool) -> None:
        """Record a rejected command."""
        self._log(syslog.LOG_WARNING, "rejected", source=source, command=command, reason=reason, dry_run=dry_run)

    def accepted(self, *, source: str, command: str, argv: Sequence[str], policy: str | None, dry_run: bool) -> None:
        """Record an accepted command (before it runs, so there's a record even if it never finishes).

        ``policy`` names the policy file(s) that define the matched command.
        """
        self._log(
            syslog.LOG_INFO,
            "accepted",
            source=source,
            command=command,
            argv=list(argv),
            policy=policy,
            dry_run=dry_run,
        )

    def config_error(self, *, source: str, command: str, error: str, dry_run: bool) -> None:
        """Record that the policy couldn't be loaded, so the command was rejected without being checked."""
        self._log(syslog.LOG_ERR, "config_error", source=source, command=command, error=error, dry_run=dry_run)

    def help_requested(self, *, source: str, command: str, dry_run: bool) -> None:
        """Record a request for the overview, i.e. ``--help`` or ``-h`` as the whole command."""
        self._log(syslog.LOG_INFO, "help", source=source, command=command, dry_run=dry_run)

    def exited(self, *, argv: Sequence[str], status: int, duration: float) -> None:
        """Record an executed command's exit status and how long it ran."""
        self._log(syslog.LOG_INFO, "exited", argv=list(argv), status=status, duration_s=round(duration, 3))

    def _log(self, priority: int, event: str, **fields: Any) -> None:  # noqa: ANN401
        """Emit one record."""
        record = {"event": event, **self._context, **fields}
        self._emit(priority, json.dumps(record, ensure_ascii=True, separators=(",", ":")))
