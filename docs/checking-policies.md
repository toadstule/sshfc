# Checking policies

`sshfc --check` validates a policy file, or a directory of them, before you deploy it. It loads the TOML, checks the
rules in [Adding commands](adding-commands.md), and runs every `accept` and `reject` example against the merged policy.
Run it after every edit.

## Running it

```console
$ sudo sshfc --check                                   # the default policy: /etc/sshfc/commands.d
$ sshfc --check --config /etc/sshfc/ci.d               # a different policy directory
$ sshfc --check --config my-policy.toml                # a single file
$ sshfc --check --insecure-config --config my-policy.toml          # a file you're still writing
```

`--insecure-config` skips the ownership checks, so you can check files that aren't root-owned yet. It's accepted only
with `--check` or `--dry-run`. See [Policy security](deploying.md#policy-security).

On success, `--check` prints one line and exits 0:

```console
$ sudo sshfc --check
OK: /etc/sshfc/commands.d (2 file(s): podman-lifecycle.toml, podman-readonly.toml)
```

On failure, it prints the problems it found to stderr, each prefixed `error:` (or `warning:`), and exits 1. It
reports every ownership problem, but loading stops at the first policy error (syntax, unknown key, or a conflict
between files), and the examples run only once the whole policy loads. So fix errors one at a time, and run `--check`
again after each.

## What it checks

- **Ownership** (unless `--insecure-config`): the policy path, every file, and every directory above them.
- **TOML syntax**, and the shape of every table, list and value.
- **Unknown keys** in a command, option, flag or positional, so typos are caught.
- **Validators:** exactly one per option or positional, a known named validator, a valid regular expression, and a
  `description` only with `pattern`.
- **Command structure:** every subcommand's parent is defined, and a command with subcommands has no arguments of its
  own.
- **Merging across files:** a command path defined differently in two files.
- **Examples:** each `accept` example is allowed, each `reject` example is rejected, and any `{ command, argv }` example
  runs exactly the argv given.

Warnings don't fail the check. The main one is a policy with no commands at all, which rejects every command.

## Common messages

| Message contains | What to do |
|---|---|
| `must be owned by root` or `must not be writable by group or others` | Fix ownership and permissions on the path it names. Run `sudo chown root:root` and `sudo chmod go-w`, or create the directory and files as root. |
| `unknown key(s) 'x'` | A typo, or a key that doesn't apply to that place. The message lists the allowed keys. |
| `needs exactly one of` | An option or positional has no validator, or more than one. Pick one of `validator`, `pattern`, `go_template`. |
| `'description' is only used with 'pattern'` | Move the description to a `pattern` validator, or delete it. |
| `unknown validator 'x'` | Use a name from [Validators](adding-commands.md#validators). |
| `invalid pattern` | The regular expression doesn't compile. |
| `defined differently in ... and ...` | The same command path has different contents in two files. Make them identical, or give one of the commands a different name. |
| `parent command 'x' is not defined` | Define the parent, `[commands.x]`, in some file of the policy. |
| `was rejected:` in an accept example | The policy rejects a line the example says must be allowed. The message includes the reason. Loosen the policy, or fix the example if it was wrong. |
| `was accepted (would run ...)` in a reject example | The policy allows what the example says must be rejected. Tighten the argument rules. |
| `must be a full command line from` | Examples start with the command's top-level name, such as `podman ps`, not `ps`. |
| `would run ..., expected ...` | The argv differs from the `argv` you wrote in the example. Check option order and `--` placement. |

## Testing a single command

`--check` checks examples. To see what one command line would run, use `--dry-run` with `-c`:

```console
$ sshfc --dry-run --insecure-config --config my-policy.toml -c "app-status --env prod billing"
Would run: /usr/local/bin/app-status --env prod -- billing
```

If the line is rejected, SSHfc prints the reason and exits 1, the same message an SSH user would see.

## What `--check` can't tell you

- **Whether a command has examples.** A command with no `accept` or `reject` lines passes, with nothing tested. Add
  examples for each command.
- **Whether `run` paths exist** on this host. The check reads the policy, not the filesystem, so run a `--dry-run`
  and a real test over SSH on the host itself.
- **Whether a command is safe.** Examples prove the policy does what you wrote, not that it's what you should allow.
  See [Writing safe commands](adding-commands.md#writing-safe-commands).
