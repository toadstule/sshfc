# sshfc

A command validator for SSH's `ForceCommand`. sshfc lets an SSH user (a person or an agent) run only an explicit
allowlist of commands, each with an explicit allowlist of arguments. Anything else is rejected with a short message
explaining why and what is allowed.

The allowlist is a **policy**: one or more TOML files in a directory, `/etc/sshfc/commands.d/` by default. sshfc ships
sample policy files you can drop in as they are or adapt.

> **Status: proof of concept.** Try a deployment with `--dry-run` first (see [Deploying](#deploying)).

## Quick start

```console
$ sshfc --samples                          # list the shipped sample policy files
$ sudo install -d -m 755 /etc/sshfc/commands.d
$ sshfc --samples podman-readonly | sudo tee /etc/sshfc/commands.d/podman-readonly.toml >/dev/null
$ sudo sshfc --check                       # check the policy and run its examples
$ sshfc --dry-run -c 'podman ps'           # see what a command would run
Would run: /usr/bin/podman ps
```

Then point an SSH key at sshfc (see [Deploying](#deploying)).

## Running sshfc

```
sshfc [--config PATH] [--dry-run] [-c COMMAND] [--key-id LABEL] [--insecure-config]
sshfc --check [--config PATH] [--insecure-config]
sshfc --samples [NAME]
```

| Invocation | Command comes from | Action |
|---|---|---|
| `sshfc` | `$SSH_ORIGINAL_COMMAND` | Run it. This is the production mode. |
| `sshfc --dry-run` | `$SSH_ORIGINAL_COMMAND` | Print `Would run: ...` and run nothing. |
| `sshfc -c 'podman ps'` | `-c` | Run it. For local testing. |
| `sshfc --dry-run -c 'podman ps'` | `-c` | Print `Would run: ...` and run nothing. |
| `sshfc --check` | | Check the policy, run every example, report problems, and exit 1 if there are any. |
| `sshfc --samples [NAME]` | | List the sample policy files, or print one. |

- **Your options are separate from the SSH user's input.** sshfc's own options come from `command="..."` in
  `authorized_keys` (or `ForceCommand` in `sshd_config`), so the admin controls them, never the SSH user.
- **`--config PATH`** is a policy directory or a single policy file. Different keys can use different policies.
- **`-c` takes a single string,** the same form as `SSH_ORIGINAL_COMMAND`, so local tests take the same path as
  production. If both are present, `-c` wins.
- **A login with no command is rejected.** If neither is present, as with an interactive `ssh host`, sshfc rejects it
  and lists the available commands.
- **Commands run without a shell,** with stdin, stdout and stderr passed through. sshfc exits with the program's
  status, `128 + N` if it was killed by signal N, or 127/126 if the program can't be found or run.
- **The environment is minimal.** `PATH` is fixed to `/usr/bin:/bin`. `HOME`, `USER` and `LOGNAME` come from the
  passwd entry. Only `LANG`, `LC_ALL`, `TERM` and `XDG_RUNTIME_DIR` (needed by rootless podman) are passed through.

### Policy security

- **Ownership:** the policy directory, every policy file, the target of any symlink, and every directory above them
  must be owned by root and not writable by group or others. A world-writable ancestor is allowed only if it has the
  sticky bit, like `/tmp`. Otherwise the SSH account could change its own policy.
- **`--insecure-config`** skips these checks so you can try a policy from a checkout. It's accepted only with
  `--dry-run` or `--check`, so nothing can run under an unchecked policy.
- **Fail closed:** if the policy is missing, insecure or malformed, every command is rejected. The SSH user sees only
  `SSH command rejected: sshfc is misconfigured; contact the administrator`. The details go to the audit log as a
  `config_error` record. Run `sshfc --check` to see them.

### Deploying

1. Create a dedicated, low-privilege account.
2. Install a policy in `/etc/sshfc/commands.d/` (see [Quick start](#quick-start)), and run `sudo sshfc --check`.
3. In the account's `~/.ssh/authorized_keys`, prefix each key with:

   ```
   command="/path/to/sshfc --dry-run",restrict ssh-ed25519 AAAA... user@example
   ```

4. Try commands through real SSH (`ssh host podman ps`) and check the `Would run:` output.
5. Remove `--dry-run`.

`restrict` turns off port, agent and X11 forwarding, PTY allocation, and `~/.ssh/rc`.

If several people's keys share the account, give each key its own label so audit records show whose key was used. A
key can also get its own policy:

```
command="/path/to/sshfc --key-id alice",restrict ssh-ed25519 AAAA... alice@example
command="/path/to/sshfc --key-id ci --config /etc/sshfc/ci.d",restrict ssh-ed25519 AAAA... ci@example
```

### Audit log

Every invocation is logged to syslog with the ident `sshfc`, under facility `authpriv` (`auth` where `authpriv`
isn't available). That puts the records next to sshd's own: `/var/log/auth.log` or `/var/log/secure`, or
`journalctl -t sshfc`. Each record is one JSON line:

| Event | When | Priority | Fields (besides `user`, `key_id`, `client`) |
|---|---|---|---|
| `rejected` | The command isn't allowed. A login with no command has `source` `none`. | warning | `source`, `command`, `reason`, `dry_run` |
| `accepted` | Before the command runs, so there's a record even if it never finishes. Also logged for dry runs. | info | `source`, `command`, `argv`, `policy`, `dry_run` |
| `exited` | After the command finishes. | info | `argv`, `status`, `duration_s` |
| `config_error` | The policy couldn't be loaded, so the command was rejected without being checked. | error | `source`, `command`, `error`, `dry_run` |

```json
{"event":"accepted","user":"svc","key_id":"alice","client":"192.0.2.7 51234","source":"SSH_ORIGINAL_COMMAND","command":"podman ps --format='{{ .ID }}'","argv":["/usr/bin/podman","ps","--format","{{.ID}}"],"policy":"podman-readonly.toml","dry_run":false}
```

- **`command` vs `argv`:** `command` is exactly what the SSH user sent, and `argv` is what sshfc ran.
- **`policy`:** the policy file (or files) that define the matched command.
- **`client`:** the client address and port from `SSH_CONNECTION`.
- **Log injection:** records are JSON with every non-ASCII and control character escaped, so a crafted command can't
  split a record or forge a new one.

## How it works

sshfc builds a strict parser from the policy and:

1. Splits the requested command line using shell rules (`shlex`). Nothing is passed to a shell.
2. Parses it against the allowed commands. Unknown commands, subcommands, options and arguments are rejected.
   Abbreviated options are rejected too, so `--for` is not treated as `--format`.
3. Runs every option and positional value through a validator, such as a name pattern or a restricted Go template.
4. **Rebuilds** the argv from the parsed result, not from the original string. The argv is the absolute path of the
   program, then the subcommand path, then the options in canonical form, then `--` and the positionals.

Because of step 4, sshfc runs exactly what it validated. A positional value can never be read as an option by the
target program, and a user can't add arguments the policy doesn't allow.

sshfc also never adds arguments the user didn't type. What runs is what they asked for, in canonical form, so
commands behave the way users of the real tool expect. When a flag must always be present, the policy makes it
**required** and the user types it (see `podman stats --no-stream` below).

## What SSH users see

With sshfc deployed with `--dry-run` and the sample policies:

```console
$ ssh host "podman ps --format '{{ .ID }}  {{.Image}}'"
Would run: /usr/bin/podman ps --format '{{.ID}}  {{.Image}}'

$ ssh host
SSH command rejected: the following arguments are required: command (choose from hostname, podman, pwd, whoami)

$ ssh host podman pod
SSH command rejected: podman pod -- the following arguments are required: subcommand (choose from inspect, ps)

$ ssh host "podman stop 'web;id'"
SSH command rejected: podman stop -- argument container: invalid value: 'web;id' (expected a container, image, or pod name or ID)

$ ssh host "podman ps --format '{{.Command}}'"
SSH command rejected: podman ps -- argument --format: field not allowed: 'Command' (choose from CreatedAt, ...)
```

Without `--dry-run`, an accepted command's output and exit status are the program's own.

Every rejection starts with `SSH command rejected:`, followed by the subcommand path where the error happened, and
exits with status 1. `-h`/`--help` works at every level and shows the available subcommands and options.

## Writing a policy

A policy file has one `[commands."<path>"]` table per command, keyed by the full command path, so even deep trees
stay flat:

```toml
# podman-readonly.toml (excerpt)
[commands.podman]
help = "Manage pods, containers and images."
run = "/usr/bin/podman"
reject = ["podman rm web-1"]

[commands."podman stop"]
help = "Stop a container."
positionals = [{ name = "container", help = "Container name or ID.", validator = "container_name" }]
accept = [{ command = "podman stop web-1", argv = "/usr/bin/podman stop -- web-1" }]

[commands."podman stats"]
help = "Display container resource usage statistics."
flags."--no-stream" = { help = "Print one snapshot instead of a live stream (required).", required = true }

[commands."podman ps".options."--format"]
help = "Go template using only {{.Field}} placeholders."
go_template = ["ID", "Image", "Names", "Status"]
```

Run `sshfc --samples podman-readonly` for the complete file. After any change, run `sshfc --check`.

### Several files in one directory

- **Which files are read:** only `*.toml` files whose names don't start with `.`, in sorted order. Editor backups
  (`*~`) and package-manager leftovers (`.rpmnew`, `.dpkg-old`) are ignored.
- **No overrides:** each command path is defined by one file. The same path may appear in several files only if
  every definition is identical, which is how independent files share a parent such as `podman`. A difference is an
  error naming both files. Because nothing overrides anything, the merged policy doesn't depend on file order, and a
  later file can't quietly change a definition reviewed in another.
- **Examples are combined:** `accept`/`reject` examples aren't part of the definition. They're combined, and
  `sshfc --check` runs them all against the **merged** policy. If one file allows something another file's `reject`
  examples expect to be rejected, the check fails.
- **Commands are listed alphabetically** at each level in help and error messages.

### Command fields

| Field | Meaning |
|---|---|
| `help` | One-line description shown in help output and error messages. Required. |
| `run` | Absolute path of the program. Subcommands inherit it and their names are added to the argv, so `run = "/usr/bin/podman"` on `podman` makes `podman pod ps` run `/usr/bin/podman pod ps`. Every leaf command needs a `run` on itself or an ancestor. |
| `options` | Options that take a value: `options."--name" = { help = "...", <validator> }`. Rendered as `--name VALUE`, if given. Add `required = true` to make the user type it. |
| `flags` | Options without a value: `flags."--all" = { help = "..." }`. Rendered as `--all`, if given. Add `required = true` to make the user type it. |
| `positionals` | Required positional arguments, in order: `[{ name = "...", help = "...", <validator> }]`. Rendered after `--`. |
| `end_of_options` | Put `--` before positionals (default `true`). Turn it off only for programs that don't understand `--`. |
| `accept` | Full example command lines that must be accepted. Use `{ command = "...", argv = "..." }` to also check what would run. |
| `reject` | Full example command lines that must be rejected. |

Only leaf commands (commands without subcommands) can have `options`, `flags` or `positionals`. A command with
subcommands requires the user to choose one. Unknown keys are errors, so a typo can't be silently ignored.

Options and flags are always rendered in the order the policy lists them, whatever order the user typed them in.

A **required** option or flag must be typed. If it's missing, the error names it
(`SSH command rejected: podman stats -- the following arguments are required: --no-stream`), and `-h` shows its help.
Use it when a command is only safe or useful with a particular argument. For example, without `--no-stream`,
`podman stats` streams forever and the SSH session never ends. Put the reason in the help text, since that's what the
user sees.

### Validators

Every option and positional needs exactly one validator. There is no "any string" default.

| Validator | Accepts |
|---|---|
| `validator = "container_name"` | A plain container, image or pod name or ID. Tags, digests, registry paths, and anything starting with `-` or `.` are rejected. |
| `pattern = "<regex>"`, `description = "..."` | Values that **fully** match the regex. `description` is shown to the user when a value is rejected. |
| `go_template = ["Field", ...]` | A restricted Go template for `--format`-style options (details below). The fields are added to the help text. |

`go_template` accepts:

- `{{.Field}}` placeholders, where `Field` must be in the list;
- literal text made of letters, digits, spaces, tabs and `:|,;=/._-`;
- an optional leading `table` keyword.

It rejects functions (`json`, `printf`), pipelines, nested fields, `range`/`if`, variables, comments and stray braces,
and enforces limits on length and placeholder count. The template sshfc runs is rebuilt from the parsed pieces, not
copied from the input.

When choosing which fields or values to allow, think about what they reveal. For example, the podman sample omits
`.Command` because container arguments often contain credentials.

### Python API

The policy loader produces `sshfc.core.Command` specs, and `sshfc.core` can also be used directly to build, check
and parse specs in Python. See `src/sshfc/core/spec.py`.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```console
make dep      # install dependencies
make check    # format, lint (ruff), type-check (mypy --strict), and test (pytest)
make build    # build the wheel
make install  # editable install into the active virtual environment
make help     # list all targets
```

Try the samples from a checkout (they aren't root-owned there, hence `--insecure-config`):

```console
uv run sshfc --check --insecure-config --config src/sshfc/samples
uv run sshfc --dry-run --insecure-config --config src/sshfc/samples -c "podman ps --format '{{.ID}}'"
```

### Project layout

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
tests/
  test_parser.py     parser, using made-up specs
  test_config.py     policy loading, merge rules, schema errors, ownership checks
  test_check.py      example checks, including one file widening another
  test_samples.py    every sample, alone and all together
  test_validators.py validators
  test_executor.py   executor: environment, exit statuses, failures to start
  test_audit.py      audit record format and log-injection safety
  test_cli.py        modes, input source, config errors, and what gets audited
```

## License

MIT
