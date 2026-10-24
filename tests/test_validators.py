#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for argument validators."""

from __future__ import annotations

import argparse

import pytest

from sshfc.core import CONTAINER_NAME, GoTemplateFormat

_FORMAT = GoTemplateFormat(["ID", "Image", "Labels", "Mounts"], max_length=64, max_fields=4)


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
