# SSHfc

SSHfc is a command validator for SSH's `ForceCommand`. It lets an SSH user, a person or an agent, run only an explicit
allowlist of commands, each with an explicit allowlist of arguments. Anything else is rejected with a short message that
says why and what is allowed.

The allowlist is a **policy**: one or more TOML files in a directory, `/etc/sshfc/commands.d/` by default. SSHfc ships
sample policies you can use as they are or adapt.

> **Status: proof of concept.** Try a deployment with `--dry-run` first.

## Why SSHfc

- **Allowlist, not blocklist.** A command runs only if the policy names it, and each option and argument must pass its
  validator. There's no "any string" default.
- **No shell.** Commands are split with `shlex` and run directly, so shell syntax in a request does nothing. (The
  account still needs a login shell for sshd to start SSHfc, but SSH users can't reach it; see
  [Deploying](docs/deploying.md#step-by-step).)
- **Runs what was validated.** SSHfc rebuilds the command from its parsed form, so the program never sees an argument
  the policy didn't allow, and a positional value can't be read as an option.
- **Fails closed.** If the policy is missing, insecure or malformed, every command is rejected.
- **Audited.** Every SSH invocation is logged to syslog as a JSON record, including what was requested and what ran.

## What SSH users see

With SSHfc deployed in `--dry-run` mode and the sample policies:

```console
$ ssh host "podman ps --format '{{ .ID }}  {{.Image}}'"
Would run: /usr/bin/podman ps --format '{{.ID}}  {{.Image}}'

$ ssh host "podman stop 'web;id'"
SSH command rejected: podman stop -- argument container: invalid value: 'web;id' (expected a container, image, or pod name or ID)
```

## Documentation

| Guide | For |
|---|---|
| [Deploying SSHfc](docs/deploying.md) | Installing SSHfc, SSH and sshd configuration, a safe rollout, and the audit log. |
| [Using the sample policies](docs/samples.md) | The shipped samples: what they allow and how to use or adapt them. |
| [Adding your own commands](docs/adding-commands.md) | Writing a policy, from a one-command file to the full reference. |
| [Checking policies](docs/checking-policies.md) | `sshfc --check`, what it verifies, and how to fix its errors. |
| [Contributing](CONTRIBUTING.md) | Development setup, checks, tests, and the design rules. |

## Quick start

Install from PyPI into a root-owned virtual environment, and link the command onto the `PATH` (see
[Installing](docs/deploying.md#installing) for uv, and for why each step matters):

```console
$ sudo python3 -m venv /opt/sshfc
$ sudo /opt/sshfc/bin/pip install sshfc
$ sudo ln -s /opt/sshfc/bin/sshfc /usr/local/bin/sshfc
```

Then install a policy and try it:

```console
$ sshfc --samples                          # list the shipped sample policies
$ sudo install -d -m 755 /etc/sshfc/commands.d
$ sshfc --samples podman-readonly | sudo tee /etc/sshfc/commands.d/podman-readonly.toml >/dev/null
$ sudo sshfc --check                       # check the policy and run its examples
$ sshfc --dry-run -c 'podman ps'           # see what a command would run
Would run: /usr/bin/podman ps
```

Then follow [Deploying SSHfc](docs/deploying.md) to point an SSH key at it.

## License

MIT. See [LICENSE](LICENSE).
