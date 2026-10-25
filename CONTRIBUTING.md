# Contributing to SSHfc

Thanks for working on SSHfc. This guide covers setting up a development environment, running the checks, the code
layout, and the design rules a change has to keep. For what SSHfc does and how to use it, start with the
[README](README.md) and the [docs](docs/).

## Setting up

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```console
$ make dep        # install dependencies (uv sync)
$ make help       # list all targets
```

Try it from the checkout without installing. Checkout policy files aren't root-owned, so use `--insecure-config`:

```console
$ uv run sshfc --check --insecure-config --config src/sshfc/samples
$ uv run sshfc --dry-run --insecure-config --config src/sshfc/samples -c "podman ps --format '{{.ID}}'"
```

To install into the active virtual environment, run `make install`. To build the wheel, run `make build`.

## Running the checks

```console
$ make check      # format, lint (ruff), type-check (mypy --strict), and test (pytest)
```

`make check` runs these, in order:

- **Format and import sort:** `ruff format` and `ruff check --select I` rewrite files in place. Commit the changes
  they make.
- **Lint:** `ruff format --check`, then `ruff check` with `select = ["ALL"]` (plus `D401`), then `mypy --strict`.
- **Test:** `pytest` with a coverage gate of 95%, plus an HTML report in `htmlcov/`.

Run `make clean` to remove caches and build output.

## Tests

The tests live in `tests/`, one file per module:

| File | Covers |
|---|---|
| `test_parser.py` | The parser, using made-up specs |
| `test_config.py` | Policy loading, merge rules, schema errors, ownership checks |
| `test_check.py` | Example checks, including one file widening another |
| `test_samples.py` | Every sample, alone and all together |
| `test_validators.py` | Validators |
| `test_executor.py` | Environment, exit statuses, failures to start |
| `test_audit.py` | Audit record format and log-injection safety |
| `test_cli.py` | Modes, input source, config errors, and what gets audited |

When you add behavior, add a test for it. The coverage gate is a floor, not a target.

## Project layout

```
src/sshfc/
  cli.py             entry point: modes, input source, auditing
  core/              infrastructure (public API: sshfc.core)
    spec.py          Command, Option, Flag, Positional
    config.py        TOML policy loading, merging, ownership checks
    check.py         sshfc --check: load the policy, run every example against it
    parser.py        strict parser built from specs; spec checks; argv rendering
    validators.py    Pattern, CONTAINER_NAME, GoTemplateFormat, and the named validators
    executor.py      runs the rendered argv: no shell, minimal environment
    audit.py         JSON audit records to syslog
  samples/           sample policy files, shipped in the package (sshfc --samples)
docs/                user documentation
tests/               see Tests above
```

## Design rules

SSHfc is a security boundary, so changes have to keep these properties. If a change would break one, discuss it first.

- **No shell.** Commands are split with `shlex` and run directly. Nothing is passed to `sh`.
- **Run what was validated.** The argv is rebuilt from the parsed result, never from the input string. Keep the
  parser's rendering as the only path to `execve`.
- **Never add arguments the user didn't type.** A policy can make a flag `required`, but SSHfc doesn't insert defaults.
- **Strict parsing.** Unknown commands, options and arguments are errors. Abbreviated options are rejected. Unknown
  policy keys are errors.
- **Every argument has a validator.** There is no "any string" default.
- **Fail closed.** If the policy is missing, insecure or malformed, every command is rejected, and the SSH user learns
  only that SSHfc is misconfigured. Details go to the audit log.
- **Auditable.** Every SSH invocation writes a record (the `--check` and `--samples` admin modes don't). Records are JSON with control and non-ASCII characters escaped.
- **Explicit policy.** Merging doesn't depend on file order, and no file can silently override another.

## Changing the policy format or the samples

- **Schema changes** (`config.py`, `validators.py`, `spec.py`) change what policy authors can write. Update
  [docs/adding-commands.md](docs/adding-commands.md) and [docs/checking-policies.md](docs/checking-policies.md) in the
  same change, including any error messages users will see.
- **Sample changes** are covered by `tests/test_samples.py`. A shared parent such as `[commands.podman]` must be
  identical in every sample that defines it, or the samples fail to load together.
- **Examples in samples are tests.** Every `accept` and `reject` example is run by `sshfc --check`. Add examples for
  each new command.

## Documentation

- The [README](README.md) is an overview. Keep it short, and link to the docs rather than repeating them.
- The [docs](docs/) are for people running and writing policies. Keep examples runnable: the commands should work as
  written against the samples or a fresh policy.
