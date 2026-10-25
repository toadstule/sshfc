# Deploying SSHfc

This guide covers installing SSHfc on a host, pointing SSH keys at it, rolling it out safely, and reading its audit
log. For the policy files it enforces, see [Samples](samples.md) and [Adding commands](adding-commands.md).

## How SSHfc is invoked

SSHfc runs as the forced command for an SSH key. sshd sets `SSH_ORIGINAL_COMMAND` to whatever the SSH user typed,
and SSHfc decides whether to run it.

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

- **Your options are separate from the SSH user's input.** SSHfc's own options come from `command="..."` in
  `authorized_keys` (or `ForceCommand` in `sshd_config`), so the admin controls them, never the SSH user.
- **`--config PATH`** is a policy directory or a single policy file. It defaults to `/etc/sshfc/commands.d`. Different
  keys can use different policies.
- **`-c` takes a single string,** the same form as `SSH_ORIGINAL_COMMAND`, so local tests take the same path as
  production. If both are present, `-c` wins.
- **A login with no command is rejected.** If neither is present, as with an interactive `ssh host`, SSHfc rejects it
  and lists the available commands.
- **Commands run without a shell,** with stdin, stdout and stderr passed through. SSHfc exits with the program's
  status, `128 + N` if it was killed by signal N, or 127/126 if the program can't be found or run.
- **The environment is minimal.** `PATH` is fixed to `/usr/bin:/bin`. `HOME`, `USER` and `LOGNAME` come from the
  passwd entry. Only `LANG`, `LC_ALL`, `TERM` and `XDG_RUNTIME_DIR` (needed by rootless podman) are passed through.

## Installing

SSHfc is published on PyPI as [`sshfc`](https://pypi.org/project/sshfc/) and needs Python 3.12 or later. Install it
as root into its own virtual environment, then link the command into `/usr/local/bin`.

With pip:

```console
$ sudo python3 -m venv /opt/sshfc
$ sudo /opt/sshfc/bin/pip install sshfc
$ sudo ln -s /opt/sshfc/bin/sshfc /usr/local/bin/sshfc
```

With [uv](https://docs.astral.sh/uv/):

```console
$ sudo uv venv --python /usr/bin/python3 /opt/sshfc
$ sudo uv pip install --python /opt/sshfc/bin/python sshfc
$ sudo ln -s /opt/sshfc/bin/sshfc /usr/local/bin/sshfc
```

With uv, point `--python` at an interpreter every account can read, such as the system's `/usr/bin/python3`. A
Python that uv downloads for root lives under root's home directory, where the SSH account can't run it. If the system
Python is older than 3.12, let uv download one into a shared location instead:
`sudo UV_PYTHON_INSTALL_DIR=/opt/uv/python uv venv --python 3.12 /opt/sshfc`.

Some things to keep in mind:

- **Keep the install root-owned.** The SSH account must not be able to write to `/opt/sshfc`, or it could replace the
  code that enforces its own policy. Installing as root, as above, does this.
- **Use the absolute path in the keys file.** sshd doesn't search `PATH` for a forced command. The examples below
  use `/usr/local/bin/sshfc`.
- **The `/usr/local/bin` link** is what lets `sudo sshfc --check` work, since sudo searches its own fixed `PATH`.

### Upgrading

```console
$ sudo /opt/sshfc/bin/pip install --upgrade sshfc                         # pip
$ sudo uv pip install --python /opt/sshfc/bin/python --upgrade sshfc      # uv
$ sudo sshfc --check
```

To control upgrades, pin a version: `pip install sshfc==X.Y.Z`, using a release listed on PyPI. Upgrading SSHfc doesn't change your policy files.

## Step by step

1. **Create a dedicated, low-privilege account** with no other access it doesn't need. Commands run as this
   account, so everything an allowed command can do is limited to what it can do.

   Give it a real login shell, such as `/bin/sh`, not `/usr/sbin/nologin` or `/bin/false`. sshd runs a forced command
   through the account's shell (`/bin/sh -c "/usr/local/bin/sshfc ..."`), so with `nologin` nothing runs at all. The
   SSH user can't reach this shell: the forced command replaces whatever they typed.

   ```console
   $ sudo useradd --create-home --shell /bin/sh svc
   ```

2. **Install a policy** in `/etc/sshfc/commands.d/`, then check it:

   ```console
   $ sudo install -d -m 755 /etc/sshfc/commands.d
   $ sshfc --samples podman-readonly | sudo tee /etc/sshfc/commands.d/podman-readonly.toml >/dev/null
   $ sudo sshfc --check
   ```

   `--check` prints `OK` and exits 0 when the policy loads and every example behaves as stated. See
   [Checking policies](checking-policies.md) for what it reports.

3. **Keep the account's keys in a root-owned file.** The `command=` restriction lives in the keys file. If the
   account could write that file, a command that can write files could remove the restriction and get a shell. So
   don't use the account's own `~/.ssh/authorized_keys`. Tell sshd to read the account's keys from a root-owned file
   instead, in `sshd_config`:

   ```
   Match User svc
       AuthorizedKeysFile /etc/ssh/authorized_keys/%u
   ```

   ```console
   $ sudo install -d -m 755 /etc/ssh/authorized_keys
   $ sudo install -m 644 /dev/null /etc/ssh/authorized_keys/svc
   $ sudo systemctl reload sshd          # or ssh, depending on the distribution
   ```

   This is the same rule as for the policy files: nothing the SSH account can change may decide what it's allowed to
   do.

4. **Point a key at SSHfc** in `/etc/ssh/authorized_keys/svc`. Prefix the key with `command=` and `restrict`, and
   start with `--dry-run`:

   ```
   command="/usr/local/bin/sshfc --dry-run",restrict ssh-ed25519 AAAA... user@example
   ```

   `restrict` turns off port, agent and X11 forwarding, PTY allocation, and `~/.ssh/rc`. Without a PTY, use plain
   `ssh host command` (no `-t`).

5. **Try commands through real SSH** and check the `Would run:` output:

   ```console
   $ ssh svc@web.example.com "podman ps --format '{{ .ID }}  {{.Image}}'"
   Would run: /usr/bin/podman ps --format '{{.ID}}  {{.Image}}'
   ```

6. **Remove `--dry-run`** from the keys file once the output looks right. An accepted command's output and exit
   status are then the program's own.

### Using ForceCommand instead of command=

To apply SSHfc to every key of an account, use `ForceCommand` in `sshd_config`. The same restrictions apply, and
the keys file should still be root-owned:

```
Match User svc
    AuthorizedKeysFile /etc/ssh/authorized_keys/%u
    ForceCommand /usr/local/bin/sshfc --dry-run
    AllowAgentForwarding no
    AllowTcpForwarding no
    X11Forwarding no
    PermitTTY no
    PermitUserRC no
```

This form can't give different keys different `--key-id` labels or policies. Use `command=` in the keys file for
that.

### Client-side convenience

On the machine you connect from, you can add an alias to `~/.ssh/config`. It is optional and has no effect on SSHfc:

```
Host sshfc-web
    HostName web.example.com
    User svc
    IdentityFile ~/.ssh/id_ed25519_sshfc
```

Then `ssh sshfc-web "podman ps"` works like any other SSH command. Agents that run commands over SSH can use the same
alias.

## Several people or agents on one account

If several keys share an account, give each key its own label, so audit records show whose key was used. A key can
also get its own policy:

```
command="/usr/local/bin/sshfc --key-id alice",restrict ssh-ed25519 AAAA... alice@example
command="/usr/local/bin/sshfc --key-id ci --config /etc/sshfc/ci.d",restrict ssh-ed25519 AAAA... ci@example
```

## Policy security

- **Ownership:** the policy directory, every policy file, the target of any symlink, and every directory above them
  must be owned by root and not writable by group or others. A world-writable ancestor is allowed only if it has the
  sticky bit, like `/tmp`. Otherwise the SSH account could change its own policy.
- **`--insecure-config`** skips these checks so you can try a policy from a checkout. It's accepted only with
  `--dry-run` or `--check`, so nothing can run under an unchecked policy.
- **Fail closed:** if the policy is missing, insecure or malformed, every command is rejected. The SSH user sees only
  `SSH command rejected: sshfc is misconfigured; contact the administrator`. The details go to the audit log as a
  `config_error` record. Run `sshfc --check` to see them.

## Audit log

Every SSH invocation is logged to syslog with the ident `sshfc`, under facility `authpriv` (`auth` where `authpriv`
isn't available). That puts the records next to sshd's own: `/var/log/auth.log` or `/var/log/secure`, or
`journalctl -t sshfc`. Each record is one JSON line:

| Event | When | Priority | Fields (besides `user`, `key_id`, `client`) |
|---|---|---|---|
| `help` | The user sent `--help` or `-h` as the whole command, so SSHfc printed the overview and ran nothing. | info | `source`, `command`, `dry_run` |
| `rejected` | The command isn't allowed. A login with no command has `source` `none`. | warning | `source`, `command`, `reason`, `dry_run` |
| `accepted` | Before the command runs, so there's a record even if it never finishes. Also logged for dry runs. | info | `source`, `command`, `argv`, `policy`, `dry_run` |
| `exited` | After the command finishes. | info | `argv`, `status`, `duration_s` |
| `config_error` | The policy couldn't be loaded, so the command was rejected without being checked. | error | `source`, `command`, `error`, `dry_run` |

```json
{"event":"accepted","user":"svc","key_id":"alice","client":"192.0.2.7 51234","source":"SSH_ORIGINAL_COMMAND","command":"podman ps --format='{{ .ID }}'","argv":["/usr/bin/podman","ps","--format","{{.ID}}"],"policy":"podman-readonly.toml","dry_run":false}
```

- **`command` vs `argv`:** `command` is exactly what the SSH user sent, and `argv` is what SSHfc ran.
- **`policy`:** the policy file (or files) that define the matched command.
- **`client`:** the client address and port from `SSH_CONNECTION`.
- **Log injection:** records are JSON with every non-ASCII and control character escaped, so a crafted command can't
  split a record or forge a new one.

## Troubleshooting

- **Every command prints `This account is currently not available`, or nothing at all:** the account's login shell
  is `nologin` or `false`, so sshd can't start SSHfc. Give it `/bin/sh` (see step 1).
- **Your key is refused after moving it to `/etc/ssh/authorized_keys/`:** check that the `Match` block's
  `AuthorizedKeysFile` line is in effect, and that the file and directory are owned by root and not writable by group
  or others. To see the setting sshd uses for the account:
  `sudo sshd -T -C user=svc,host=localhost,addr=127.0.0.1 | grep -i authorizedkeysfile`.
- **Every command says `sshfc is misconfigured`:** the policy didn't load. Run `sudo sshfc --check` for the reason,
  or find the `config_error` record in the audit log.
- **A command is rejected that you expected to work:** the rejection message names the subcommand and the argument
  that failed. To list what's allowed, run `ssh host --help` for the overview and every command, or `ssh host "podman -h"`
  for one command group (`-h` works at every level). To test an exact line locally, run `sshfc --dry-run -c '...'`.
- **`podman` fails under SSHfc but works in your shell:** check the account's `XDG_RUNTIME_DIR` and that podman is
  installed at `/usr/bin/podman`. SSHfc doesn't pass through other environment variables.