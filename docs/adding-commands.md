# Adding your own commands

An SSHfc policy is a set of TOML files. Each file declares the commands it allows, the arguments each command accepts,
and examples that prove the policy behaves as you intend. This guide starts with a one-command policy, adds arguments
and subcommands, and ends with the full reference.

Policies go in a directory (default `/etc/sshfc/commands.d/`), one or more `*.toml` files each. While you write one,
check it with `sshfc --check --insecure-config --config my-policy.toml` from any directory. See
[Checking policies](checking-policies.md).

## Start simple: a command with no arguments

Save this as `uptime.toml`:

```toml
[commands.uptime]
help = "Show how long the system has been up."
run = "/usr/bin/uptime"
accept = ["uptime"]
reject = ["uptime -s"]
```

- `[commands.uptime]` declares the command `uptime`. The table name is the command path.
- `help` is shown to SSH users in `-h` output, and in the overview for `--help`, where it's the one-line description
  next to each command. It's required.
- `run` is the absolute path of the program. SSHfc runs that path, not whatever `uptime` the `PATH` would find.
- `accept` lists command lines that must be allowed. `reject` lists command lines that must not be.

Try it:

```console
$ sshfc --check --insecure-config --config uptime.toml
OK: uptime.toml (1 file(s): uptime.toml)
$ sshfc --dry-run --insecure-config --config uptime.toml -c "uptime"
Would run: /usr/bin/uptime
$ sshfc --dry-run --insecure-config --config uptime.toml -c "uptime -s"
SSH command rejected: uptime -- unrecognized arguments: -s
```

That's a complete policy. Everything else in this guide adds to it.

## Add an option and a positional argument

Suppose your team has a program, `/usr/local/bin/app-status`, that takes an environment and an application name. Here's
the policy for it:

```toml
[commands.app-status]
help = "Show the status of an application."
run = "/usr/local/bin/app-status"
options."--env" = { help = "Environment to query.", pattern = "dev|staging|prod", description = "dev, staging or prod" }
positionals = [{ name = "app", help = "Application name.", pattern = "[a-z][a-z0-9-]{0,62}", description = "a lowercase application name" }]
accept = [
    "app-status billing",
    { command = "app-status --env prod billing", argv = "/usr/local/bin/app-status --env prod -- billing" },
]
reject = ["app-status", "app-status --env qa billing", "app-status --env prod"]
```

What this does:

- `options."--env"` takes a value. The user may type it, or leave it out. The value must match the `pattern` in full,
  and `description` is what the user sees when a value is rejected.
- `positionals` are required arguments, in order. Here there's one, `app`, checked by its own pattern. The user can
  type options before or after it, so `app-status billing --env prod` is accepted too.
- The `accept` example with an `argv` also checks what will run. SSHfc puts `--` before positionals, so a value can
  never be read as an option by the program.
- `app-status --env prod` is rejected because the required positional `app` is missing.

## Add subcommands

A command can have subcommands, each a separate table keyed by the full path. The parent defines the program, and the
children inherit it:

```toml
[commands.app]
help = "Manage applications."
run = "/usr/local/bin/app"
reject = ["app"]

[commands."app list"]
help = "List applications."
accept = ["app list"]

[commands."app restart"]
help = "Restart an application."
positionals = [{ name = "app", help = "Application name.", pattern = "[a-z][a-z0-9-]{0,62}", description = "a lowercase application name" }]
accept = [{ command = "app restart billing", argv = "/usr/local/bin/app restart -- billing" }]
reject = ["app restart", "app restart billing extra"]
```

- A command with subcommands can't have `options`, `flags` or `positionals` of its own. The user must choose a
  subcommand, and `app` alone is rejected.
- `run` is set once on `app`, and `app list` runs `/usr/local/bin/app list`.
- Every subcommand's parent must be defined somewhere in the policy. The parent can be in another file.

The samples use the same pattern: `podman` is the parent, and `podman ps` and `podman stats` are subcommands.

## Full reference

### Command fields

| Field | Meaning |
|---|---|
| `help` | One-line description shown in `-h` output and the `--help` overview. Required. |
| `run` | Absolute path of the program. Subcommands inherit it and their names are added to the argv, so `run = "/usr/bin/podman"` on `podman` makes `podman pod ps` run `/usr/bin/podman pod ps`. Every leaf command needs a `run` on itself or an ancestor. |
| `options` | Options that take a value: `options."--name" = { help = "...", <validator> }`. Rendered as `--name VALUE`, if given. Add `required = true` to make the user type it. |
| `flags` | Options without a value: `flags."--all" = { help = "..." }`. Rendered as `--all`, if given. Add `required = true` to make the user type it. |
| `positionals` | Required positional arguments, in order: `[{ name = "...", help = "...", <validator> }]`. Rendered after `--`. |
| `end_of_options` | Put `--` before positionals (default `true`). Turn it off only for programs that don't understand `--`. |
| `accept` | Full example command lines that must be accepted. Use `{ command = "...", argv = "..." }` to also check what would run. |
| `reject` | Full example command lines that must be rejected. |

Any other key is an error, so a typo can't be silently ignored.

Only leaf commands (commands without subcommands) can have `options`, `flags` or `positionals`. A command with
subcommands requires the user to choose one.

Options and flags are always rendered in the order the policy lists them, whatever order the user typed them in.
Positionals are rendered in the order they're declared.

### Option, flag and positional names

- Option and flag names must start with `-`. `-h` and `--help` are reserved for help, and `-` and `--` aren't allowed.
- Positional names must not start with `-`.
- Every option, flag and positional name must be unique within its command.
- Command names start with a letter or digit, and use letters, digits, and `_.-`. A command path is names separated by
  single spaces.

### Required options and flags

A **required** option or flag must be typed. If it's missing, the error names it:

```
SSH command rejected: podman stats -- the following arguments are required: --no-stream
```

Use it when a command is only safe or useful with a particular argument. For example, without `--no-stream`,
`podman stats` streams forever and the SSH session never ends. Put the reason in the help text, since that's what the
user sees.

### Validators

Every option and positional needs **exactly one** validator. There is no "any string" default.

| Validator | Accepts |
|---|---|
| `validator = "container_name"` | A plain container, image or pod name or ID (letters, digits, `_`, `.`, `-`; up to 128 characters; starting with a letter or digit). Tags, digests, registry paths, and anything starting with `-` or `.` are rejected. |
| `validator = "unit_name"` | A systemd unit name (e.g., `ssh.service`, `nginx@instance.service`). Valid characters are letters, digits, `:`, `_`, `-`, `.`, and `\`. Supports optional `@instance` syntax for template units. Must end with a valid unit type suffix (service, socket, device, mount, automount, swap, target, path, timer, slice, scope). Maximum 255 characters. |
| `validator = "journalctl_date"` | A journalctl date/time format. Accepts absolute dates (`YYYY-MM-DD [HH:MM[:SS]]`), time only (`HH:MM[:SS]`), keywords (`yesterday`, `today`, `tomorrow`, `now`), and relative times (`-1h`, `30min ago`, etc.). Maximum 64 characters. |
| `pattern = "<regex>", description = "..."` | Values that **fully** match the regex. `description` is required with `pattern`, and is shown when a value is rejected. It's not allowed with the other validators. |
| `go_template = ["Field", ...]` | A restricted Go template for `--format`-style options (details below). The listed fields are added to the help text. |

`container_name`, `unit_name`, and `journalctl_date` are the named validators. The other two kinds take their rules from the policy itself.

`pattern` is a regular expression matched against the whole value, so `dev|staging|prod` accepts exactly those three
values. Anchors aren't needed.

Make every pattern **bounded**: name the characters it allows, and limit the length. `.*`, `.+` and `\S+` accept
nearly anything, which defeats the allowlist. `.` also matches `/`, so path-like values slip through. Prefer
`[a-z][a-z0-9-]{0,62}` to `\w+`, and `[0-9]{1,5}` to `\d+`.

#### `go_template`

`go_template` accepts:

- `{{.Field}}` placeholders, where `Field` must be in the list;
- literal text made of letters, digits, spaces, tabs and `:|,;=/._-`;
- an optional leading `table` keyword.

It rejects functions (`json`, `printf`), pipelines, nested fields, `range`/`if`, variables, comments and stray braces,
and enforces limits: at most 256 characters, and at most 16 placeholders. The template SSHfc runs is rebuilt from the
parsed pieces, not copied from the input.

```toml
[commands."podman ps".options."--format"]
help = "Go template using only {{.Field}} placeholders."
go_template = ["ID", "Image", "Names", "Status"]
```

When choosing which fields or values to allow, think about what they reveal. For example, the podman sample omits
`.Command` because container arguments often carry credentials.

### Examples: `accept` and `reject`

- An example is a full command line, starting with the command's top-level name. For example, `podman ps` is an example
  for `podman`, and `ps` alone is not.
- `accept` can be a string, which only checks that the line is allowed. The `{ command, argv }` form also checks the
  argv that would run, exactly. Use it when the argument rendering matters, such as `--` placement or option order.
- `reject` examples must be rejected.
- `sshfc --check` runs every example against the merged policy, not just the file that holds it.

### Several files in one directory

- **Which files are read:** only `*.toml` files whose names don't start with `.`, in sorted order. Editor backups
  (`*~`) and package-manager leftovers (`.rpmnew`, `.dpkg-old`) are ignored.
- **No overrides:** each command path is defined by one file. The same path may appear in several files only if
  every definition is identical, which is how independent files share a parent such as `podman`. `accept` and
  `reject` examples aren't part of the definition, so they can differ. Any other difference is an error naming both
  files. Because nothing overrides anything, the merged policy doesn't depend on file order, and a
  later file can't quietly change a definition reviewed in another.
- **Examples are combined:** `accept` and `reject` examples are checked against the whole merged policy. If one file
  allows something another file's `reject` examples expect to be rejected, the check fails.
- **Commands are listed alphabetically** at each level in help and error messages.

## Writing safe commands

- **Expose only what's needed.** Every option and positional is a choice you're making for whoever holds the key.
- **Be careful with paths.** A positional that takes a file path lets the SSH user name any file the account can read.
  Use a `pattern` that matches only the values you expect, such as `dev|staging|prod`.
- **Limit output fields.** `go_template` lets you choose which fields appear, so leave out anything sensitive.
- **Require arguments that change behavior.** Use `required = true` for a flag that makes the command safe, such as
  `--no-stream`.
- **Write both accept and reject examples for each command.** `--check` can only catch problems that examples cover,
  and a command with no examples isn't tested at all.
- **Test before you deploy.** Use `sshfc --dry-run -c '...'` to see exactly what would run, then run
  `sshfc --check` after every change.
