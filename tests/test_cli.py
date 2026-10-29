#  Copyright (C) 2026 Stephen T. Jibson.
#  SPDX-License-Identifier: MIT

"""Tests for the CLI entrypoint: input source, mode, and audit records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import sshfc
from sshfc import cli
from sshfc.core.audit import AuditLog
from sshfc.core.config import ConfigError, Policy, load_policy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SAMPLES = Path(str(sshfc.__file__)).parent / "samples"


@pytest.fixture(autouse=True)
def policy_path(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Load the shipped samples (without ownership checks) whatever --config says; record the paths requested."""
    requested: list[Path] = []

    def fake_load_policy(paths: list[Path], *, check_owners: bool = True) -> Policy:
        del check_owners  # unused
        requested.extend(paths)
        return load_policy([SAMPLES], check_owners=False)

    # noinspection unresolved-references
    monkeypatch.setattr(cli, "load_policy", fake_load_policy)
    return requested


@pytest.fixture(autouse=True)
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture audit records instead of sending them to syslog."""
    records: list[dict[str, Any]] = []

    def make_log(environ: Mapping[str, str], key_id: str | None = None) -> AuditLog:
        return AuditLog(environ, key_id=key_id, emit=lambda _, m: records.append(json.loads(m)))

    # noinspection unresolved-references
    monkeypatch.setattr(cli, "AuditLog", make_log)
    return records


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the executor with one that records argvs and returns status 3."""
    calls: list[list[str]] = []

    def fake_execute(argv: Sequence[str]) -> int:
        calls.append(list(argv))
        return 3

    # noinspection unresolved-references
    monkeypatch.setattr(cli, "execute", fake_execute)
    return calls


@pytest.mark.parametrize(
    ("args", "environ"),
    [
        (["--dry-run", "-c", "podman ps --format='{{ .ID }}'"], {}),
        (["--dry-run"], {"SSH_ORIGINAL_COMMAND": "podman ps --format='{{ .ID }}'"}),
        # -c wins over the environment.
        (["--dry-run", "-c", "podman ps --format='{{ .ID }}'"], {"SSH_ORIGINAL_COMMAND": "hostname"}),
    ],
)
def test_dry_run(
    args: list[str], environ: dict[str, str], executed: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that --dry-run prints the rendered argv and executes nothing, whichever source the command came from."""
    cli.main(args, environ)
    assert capsys.readouterr().out == "Would run: /usr/bin/podman ps --format '{{.ID}}'\n"
    assert executed == []


@pytest.mark.parametrize(
    ("args", "environ"),
    [
        ([], {"SSH_ORIGINAL_COMMAND": "podman pod inspect mypod"}),
        (["-c", "podman pod inspect mypod"], {}),
    ],
)
def test_execute(args: list[str], environ: dict[str, str], executed: list[list[str]]) -> None:
    """Test that without --dry-run the rendered argv is executed and its status becomes sshfc's exit status."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(args, environ)
    assert exc_info.value.code == 3
    assert executed == [["/usr/bin/podman", "pod", "inspect", "--", "mypod"]]


@pytest.mark.parametrize(
    ("args", "environ", "expected"),
    [
        ([], {"SSH_ORIGINAL_COMMAND": "rm -rf /"}, r"invalid choice: 'rm'"),
        (["-c", "podman stop 'web;id'"], {}, r"podman stop -- argument container: invalid value"),
    ],
)
def test_reject(args: list[str], environ: dict[str, str], expected: str, executed: list[list[str]]) -> None:
    """Test that rejected commands exit with the rejection message and execute nothing."""
    with pytest.raises(SystemExit, match=rf"^SSH command rejected: .*{expected}"):
        cli.main(args, environ)
    assert executed == []


@pytest.mark.parametrize(
    ("environ", "expected_source"),
    [({}, "none"), ({"SSH_ORIGINAL_COMMAND": ""}, "SSH_ORIGINAL_COMMAND")],
)
def test_no_command_shows_help(
    environ: dict[str, str], expected_source: str, audited: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that a login with no command shows the help overview and is audited as a help event."""
    cli.main([], environ)  # No SystemExit: the overview is a normal, successful output.
    out = capsys.readouterr().out
    assert out.startswith("sshfc: restricted command access\n")
    assert "Available commands:\n" in out
    assert [(r["event"], r["source"], r["command"]) for r in audited] == [("help", expected_source, "")]


def test_audit_rejected(audited: list[dict[str, Any]]) -> None:
    """Test that a rejected command is recorded with its source, raw command, and reason."""
    environ = {"SSH_ORIGINAL_COMMAND": "rm -rf /", "SSH_CONNECTION": "192.0.2.7 51234 198.51.100.1 22"}
    with pytest.raises(SystemExit):
        cli.main(["--key-id", "alice"], environ)
    assert len(audited) == 1
    assert audited[0]["event"] == "rejected"
    assert audited[0]["source"] == "SSH_ORIGINAL_COMMAND"
    assert audited[0]["command"] == "rm -rf /"
    assert audited[0]["reason"].startswith("SSH command rejected: argument command: invalid choice: 'rm'")
    assert audited[0]["key_id"] == "alice"
    assert audited[0]["client"] == "192.0.2.7 51234"


def test_audit_interactive_login(audited: list[dict[str, Any]]) -> None:
    """Test that a login with no command is recorded as a help event with source 'none'."""
    cli.main([], {})
    assert [(r["event"], r["source"], r["command"]) for r in audited] == [("help", "none", "")]


def test_audit_dry_run(audited: list[dict[str, Any]]) -> None:
    """Test that a dry run is recorded as accepted (flagged dry_run) and nothing else."""
    cli.main(["--dry-run", "-c", "podman stats --no-stream"], {})
    assert [(r["event"], r["source"], r["argv"], r["dry_run"]) for r in audited] == [
        ("accepted", "-c", ["/usr/bin/podman", "stats", "--no-stream"], True)
    ]


@pytest.mark.usefixtures("executed")
def test_audit_executed(audited: list[dict[str, Any]]) -> None:
    """Test that an executed command is recorded before it runs and again with its exit status."""
    with pytest.raises(SystemExit):
        cli.main([], {"SSH_ORIGINAL_COMMAND": "podman pod inspect mypod"})
    argv = ["/usr/bin/podman", "pod", "inspect", "--", "mypod"]
    assert [r["event"] for r in audited] == ["accepted", "exited"]
    assert audited[0]["argv"] == argv
    assert audited[0]["dry_run"] is False
    assert audited[1]["argv"] == argv
    assert audited[1]["status"] == 3
    assert audited[1]["duration_s"] >= 0


def test_audit_policy_source(audited: list[dict[str, Any]]) -> None:
    """Test that accepted records name the policy file that allowed the command."""
    cli.main(["--dry-run", "-c", "podman stop web-1"], {})
    cli.main(["--dry-run", "-c", "whoami"], {})
    assert [r["policy"] for r in audited] == ["podman-lifecycle.toml", "system-info.toml"]


@pytest.mark.usefixtures("executed")
@pytest.mark.parametrize(
    ("args", "environ"),
    [
        ([], {"SSH_ORIGINAL_COMMAND": "help"}),
        ([], {"SSH_ORIGINAL_COMMAND": " help "}),
        (["--dry-run"], {"SSH_ORIGINAL_COMMAND": "help"}),
    ],
)
def test_help_overview(args: list[str], environ: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    """Test that help as the whole command prints the overview with every command path, and exits normally."""
    cli.main(args, environ)  # No SystemExit: the overview is a normal, successful output.
    out = capsys.readouterr().out
    assert out.startswith("sshfc: restricted command access\n")
    assert "Available commands:\n" in out
    # The path is the first column of each listed command, separated from its help by two or more spaces.
    listed = {line[2:].split("  ")[0] for line in out.partition("Available commands:\n")[2].splitlines() if line}
    assert {"hostname", "podman", "podman inspect", "podman ps", "podman stats", "whoami"} <= listed


def test_help_overview_runs_nothing(executed: list[list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    """Test that the overview is shown in place of a command, so nothing is run."""
    cli.main([], {"SSH_ORIGINAL_COMMAND": "help"})
    assert capsys.readouterr().out
    assert executed == []


def test_help_overview_is_audited(audited: list[dict[str, Any]]) -> None:
    """Test that a request for the overview is recorded as a help event."""
    cli.main([], {"SSH_ORIGINAL_COMMAND": "help"})
    assert [(r["event"], r["source"], r["command"]) for r in audited] == [("help", "SSH_ORIGINAL_COMMAND", "help")]


def test_help_overview_needs_exact_match(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that anything other than exactly help isn't treated as a request for the overview."""
    with pytest.raises(SystemExit, match=r"^SSH command rejected:"):
        cli.main([], {"SSH_ORIGINAL_COMMAND": "'help"})  # Unbalanced quote: not a help request, so rejected.
    assert "sshfc: restricted command access" not in capsys.readouterr().out


def test_help_overview_fails_closed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that the overview isn't shown when the policy can't be loaded."""

    def broken(paths: list[Path], *, check_owners: bool = True) -> Policy:
        del paths, check_owners  # unused
        msg = "broken policy"
        raise ConfigError(msg)

    # noinspection unresolved-references
    monkeypatch.setattr(cli, "load_policy", broken)
    with pytest.raises(SystemExit, match=r"^SSH command rejected: sshfc is misconfigured"):
        cli.main([], {"SSH_ORIGINAL_COMMAND": "help"})
    assert capsys.readouterr().out == ""


def test_default_and_explicit_config(policy_path: list[Path]) -> None:
    """Test that the policy comes from /etc/sshfc/commands.d unless --config says otherwise."""
    cli.main(["--dry-run", "-c", "whoami"], {})
    cli.main(["--dry-run", "--config", "/srv/policy.toml", "-c", "whoami"], {})
    assert policy_path == [Path("/etc/sshfc/commands.d"), Path("/srv/policy.toml")]


def test_multiple_configs(policy_path: list[Path]) -> None:
    """Test that multiple --config arguments are supported."""
    cli.main(["--dry-run", "--config", "/srv/policy.toml", "--config", "/etc/sshfc/commands.d", "-c", "whoami"], {})
    assert policy_path == [Path("/srv/policy.toml"), Path("/etc/sshfc/commands.d")]


def test_config_error(
    monkeypatch: pytest.MonkeyPatch, audited: list[dict[str, Any]], executed: list[list[str]]
) -> None:
    """Test that a policy that can't be loaded rejects everything, with details only in the audit log."""

    def broken(paths: list[Path], *, check_owners: bool = True) -> Policy:
        del paths, check_owners  # unused
        msg = "/etc/sshfc/commands.d/x.toml: must be owned by root"
        raise cli.ConfigError(msg)

    # noinspection unresolved-references
    monkeypatch.setattr(cli, "load_policy", broken)
    with pytest.raises(SystemExit) as exc_info:
        cli.main([], {"SSH_ORIGINAL_COMMAND": "whoami"})
    assert exc_info.value.code == "SSH command rejected: sshfc is misconfigured; contact the administrator"
    assert executed == []
    assert [(r["event"], r["command"], r["error"]) for r in audited] == [
        ("config_error", "whoami", "/etc/sshfc/commands.d/x.toml: must be owned by root")
    ]


def test_insecure_config_requires_dry_run_or_check(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that ownership checks can't be skipped when commands would really run."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--insecure-config", "-c", "whoami"], {})
    assert exc_info.value.code == 2
    assert "--insecure-config requires --dry-run or --check" in capsys.readouterr().err


def test_check_samples(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --check passes on the shipped samples (they aren't root-owned in a checkout, hence the flag)."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--check", "--insecure-config", "--config", str(SAMPLES)], {})
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(
        f"OK: {SAMPLES} "
        "(6 file(s): "
        "files-readonly.toml, "
        "journalctl-user.toml, "
        "podman-lifecycle.toml, "
        "podman-readonly.toml, "
        "system-info.toml, "
        "systemctl-user.toml"
        ")"
    )


def test_check_reports_ownership(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --check reports ownership problems when not told to skip them."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--check", "--config", str(SAMPLES)], {})
    assert exc_info.value.code == 1
    assert "must be owned by root" in capsys.readouterr().err


def test_check_prints_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --check prints each error to stderr and exits 1 when the policy doesn't load."""
    (tmp_path / "a.toml").write_text("[commands.x]\n")
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--check", "--insecure-config", "--config", str(tmp_path)], {})
    assert exc_info.value.code == 1
    assert "error: a.toml: commands.'x': help must be a non-empty string" in capsys.readouterr().err


def test_check_prints_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --check prints warnings to stderr but still passes an empty policy."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--check", "--insecure-config", "--config", str(tmp_path)], {})
    assert exc_info.value.code == 0
    assert f"warning: {tmp_path}: no commands defined" in capsys.readouterr().err


def test_samples_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --samples lists each sample with its one-line description."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--samples"], {})
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "podman-readonly.toml     Podman, read-only:" in out
    assert "system-info.toml         Basic system information:" in out


@pytest.mark.parametrize("name", ["system-info", "system-info.toml"])
def test_samples_show(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --samples NAME prints the sample, with or without the .toml suffix."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--samples", name], {})
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == (SAMPLES / "system-info.toml").read_text()


def test_samples_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that an unknown sample name is an error."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--samples", "nope"], {})
    assert exc_info.value.code == 1
    assert "no sample named 'nope'" in capsys.readouterr().err
