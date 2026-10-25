#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for argument validators."""

from __future__ import annotations

import argparse

import pytest

from sshfc.core import CONTAINER_NAME, UNIT_NAME, GoTemplateFormat, JournalctlDate

_FORMAT = GoTemplateFormat(["ID", "Image", "Labels", "Mounts"], max_length=64, max_fields=4)
_JOURNALCTL_DATE = JournalctlDate()


@pytest.mark.parametrize("value", ["web", "web-1", "my_app.2", "a1b2c3d4e5f6", "x" * 128])
def test_valid_container_name(value: str) -> None:
    """Test that plain names and IDs are accepted unchanged."""
    assert CONTAINER_NAME(value) == value


@pytest.mark.parametrize(
    "value", ["", "-web", ".web", "web;id", "web 1", "repo/web", "web:latest", "web@sha256:x", "x" * 129, "wéb"]
)
def test_invalid_container_name(value: str) -> None:
    """Test that anything beyond a plain name or ID is rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match=r"expected a container, image, or pod name or ID"):
        CONTAINER_NAME(value)


@pytest.mark.parametrize(
    "value",
    [
        "ssh.service",
        "nginx.service",
        "docker.service",
        "systemd-journald.service",
        "user@1000.service",
        "getty@tty1.service",
        "home.mount",
        "dev-sda.device",
        "sys-devices-pci0000:00-0000:00:1f.2-ata3-host0-target0:0:0:0-block-sda.device",
        "swap.swap",
        "basic.target",
        "multi-user.target",
        "timers.target",
        "cron-daily.timer",
        "paths.path",
        "sockets.socket",
        "automount.automount",
        "system.slice",
        "user.slice",
        "session-1.scope",
    ],
)
def test_valid_unit_name(value: str) -> None:
    """Test that valid systemd unit names are accepted unchanged."""
    assert UNIT_NAME(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".service",
        "ssh",
        "ssh.invalid",
        "-ssh.service",
        "@instance.service",
        "ssh@.service",
        "ssh service",
        "ssh;service",
        "a" * 256 + ".service",
        "wéb.service",
        "ssh@instance@double.service",
    ],
)
def test_invalid_unit_name(value: str) -> None:
    """Test that invalid systemd unit names are rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match=r"expected a systemd unit name"):
        UNIT_NAME(value)


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-15",
        "2024-01-15 14:30",
        "2024-01-15 14:30:45",
        "14:30",
        "14:30:45",
        "yesterday",
        "today",
        "tomorrow",
        "now",
        "YESTERDAY",
        "TODAY",
        "TOMORROW",
        "NOW",
        "-1h",
        "-30min",
        "+2days",
        "-1week",
        "+1year",
        "1 hour ago",
        "30 min ago",
        "2 days ago",
        "1 week ago",
        "1 year ago",
        "1 HOUR AGO",
        "30 MIN AGO",
    ],
)
def test_valid_journalctl_date(value: str) -> None:
    """Test that valid journalctl date formats are accepted."""
    result = _JOURNALCTL_DATE(value)
    if value.lower() in _JOURNALCTL_DATE._KEYWORDS or "ago" in value.lower():
        assert result == value.lower()
    else:
        assert result == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2024-1-15",
        "24-01-15",
        "2024/01/15",
        "yesterdayz",
        "todayz",
        "tomorrowz",
        "nowz",
        "-1",
        "+1",
        "-h",
        "+h",
        "1 ago",
        "hour ago",
        "1 hour",
        "1 hour before",
        "a" * 65,
    ],
)
def test_invalid_journalctl_date(value: str) -> None:
    """Test that invalid journalctl date formats are rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match=r"invalid date"):
        _JOURNALCTL_DATE(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{{.ID}}", "{{.ID}}"),
        ("{{.ID}}  {{.Image}}  {{.Labels}}  {{.Mounts}}", "{{.ID}}  {{.Image}}  {{.Labels}}  {{.Mounts}}"),
        ("{{ .ID }}", "{{.ID}}"),
        ("table {{.ID}}\t{{.Image}}", "table {{.ID}}\t{{.Image}}"),
        ("id={{.ID}}, image={{.Image}}", "id={{.ID}}, image={{.Image}}"),
        ("{{.ID}}|{{.Image}}", "{{.ID}}|{{.Image}}"),
    ],
)
def test_valid_format(value: str, expected: str) -> None:
    """Test that allowed templates are accepted and returned in canonical form."""
    assert _FORMAT(value) == expected


@pytest.mark.parametrize(
    ("value", "expected_error"),
    [
        # Disallowed or unknown fields.
        ("{{.Command}}", r"field not allowed: 'Command'"),
        ("{{.id}}", r"field not allowed: 'id'"),
        # Anything other than a bare {{.Field}} -- functions, pipelines, nested fields, control structures.
        ("{{json .}}", r"invalid template text"),
        ("{{.}}", r"invalid template text"),
        ("{{.Labels.secret}}", r"invalid template text"),
        ("{{.Config.Env}}", r"invalid template text"),
        ('{{.ID | printf "%s"}}', r"invalid template text"),
        ("{{range .Mounts}}{{.}}{{end}}", r"invalid template text"),
        ("{{if .ID}}{{.ID}}{{end}}", r"invalid template text"),
        ("{{/* comment */}}{{.ID}}", r"invalid template text"),
        ("{{- .ID -}}", r"invalid template text"),
        ("{{$x := .ID}}{{.ID}}", r"invalid template text"),
        # Braces or other characters outside placeholders.
        ("{{.ID}}}", r"invalid template text"),
        ("{{{.ID}}", r"invalid template text"),
        ("{.ID}", r"invalid template text"),
        ("{{.ID}}\n{{.Image}}", r"invalid template text"),
        ("{{.ID}} $(id)", r"invalid template text"),
        ("{{.ID}} `id`", r"invalid template text"),
        ("{{.ID}} 'x'", r"invalid template text"),
        # Whole-format keywords and empty formats.
        ("json", r"at least one"),
        ("table", r"at least one"),
        ("", r"at least one"),
        # Size limits.
        ("{{.ID}}" * 5, r"more than 4 placeholders"),
        ("{{.ID}}" + " " * 64, r"longer than 64 characters"),
    ],
)
def test_invalid_format(value: str, expected_error: str) -> None:
    """Test that anything outside the restricted subset is rejected."""
    with pytest.raises(argparse.ArgumentTypeError, match=expected_error):
        _FORMAT(value)
