# Using the sample policies

SSHfc ships three sample policy files. Each is a working policy you can use as it is, combine with others, or copy and
adapt. They're part of the package, so they're available wherever SSHfc is installed.

## Listing and printing samples

```console
$ sshfc --samples                      # list the samples, with a one-line description of each
$ sshfc --samples podman-readonly      # print one (the .toml suffix is optional)
```

`sshfc --samples` also shows the directory the samples are installed in.

## The samples

### `system-info.toml`

Basic system information: `hostname`, `pwd` and `whoami`. Nothing here changes state, and none of the commands take
arguments.

```console
$ ssh host hostname
$ ssh host whoami
```

`hostname newname` and `hostname -F /etc/shadow` are rejected, because the policy allows no arguments.

### `podman-readonly.toml`

Lists and inspects containers, images and pods, and shows resource usage and processes. It can't change anything:

| Command | Allowed |
|---|---|
| `podman images` | Yes |
| `podman inspect NAME` | Yes; containers, images or pods |
| `podman pod ps`, `podman pod inspect NAME` | Yes |
| `podman ps` | Yes, with `--format` limited to a set of fields |
| `podman stats --no-stream` | Yes. `--no-stream` is required, since a live stream never ends. |
| `podman top NAME` | Yes |
| `podman rm ...`, `podman ps --all`, `podman ps --format '{{.Command}}'` | No |

The `--format` option accepts only `{{.Field}}` placeholders from a list. The policy leaves out `Command`, because
container arguments often carry credentials. `Labels` is included, but it can hold secrets too (for example
basic-auth hashes on a reverse proxy). Remove it from the list if that matters on your hosts.

```console
$ ssh host "podman ps --format '{{ .ID }}  {{.Image}}  {{.Names}}'"
$ ssh host "podman stats"
SSH command rejected: podman stats -- the following arguments are required: --no-stream
```

### `podman-lifecycle.toml`

Starts, stops and restarts one container at a time. It doesn't create, remove or change images, and it doesn't accept
`--all`:

```console
$ ssh host "podman restart web-1"
$ ssh host "podman restart --all"     # rejected: restart needs one container name, and --all isn't allowed
```

The `podman` parent is defined in both `podman-readonly.toml` and `podman-lifecycle.toml`. Its definition (`help`
and `run`) is identical in both, so the two files can be used together. Their `accept` and `reject` examples differ,
which is fine, because examples aren't part of the comparison.

## Using samples on a host

Copy the samples you want into the policy directory, then check them:

```console
$ sudo install -d -m 755 /etc/sshfc/commands.d
$ sshfc --samples podman-readonly | sudo tee /etc/sshfc/commands.d/podman-readonly.toml >/dev/null
$ sshfc --samples podman-lifecycle | sudo tee /etc/sshfc/commands.d/podman-lifecycle.toml >/dev/null
$ sudo sshfc --check
OK: /etc/sshfc/commands.d (2 file(s): podman-lifecycle.toml, podman-readonly.toml)
```

Copies (rather than symlinks to the package) keep the policy under your control: a package upgrade can't change what
runs. Copy them as root, and keep them owned by root and not writable by group or others, or SSHfc will refuse to load
them (see [Policy security](deploying.md#policy-security)).

Only install the samples you want. Don't install `podman-lifecycle.toml` on a key that should only read.

### Requirements on the host

- `podman` must be installed at `/usr/bin/podman`, since the samples use that absolute path. For rootless podman, the
  SSH account needs a working `XDG_RUNTIME_DIR`; SSHfc passes that variable through.
- The `run` paths in the samples are absolute, such as `/usr/bin/hostname`. If a program lives somewhere else on your
  host, copy the sample and change its `run` path.

## Trying samples before installing them

You don't need root or an installed policy to try a sample. Write the samples to a scratch directory, and use
`--insecure-config`, since those files aren't root-owned. It's allowed only with `--dry-run` and `--check`:

```console
$ mkdir try-samples
$ sshfc --samples podman-readonly > try-samples/podman-readonly.toml
$ sshfc --samples podman-lifecycle > try-samples/podman-lifecycle.toml
$ sshfc --check --insecure-config --config try-samples
$ sshfc --dry-run --insecure-config --config try-samples -c "podman ps --format '{{.ID}}'"
Would run: /usr/bin/podman ps --format '{{.ID}}'
```

## Adapting a sample

To change a sample, copy it under a new name, edit the copy, and check it:

```console
$ sshfc --samples podman-readonly > my-podman.toml
$ sshfc --check --insecure-config --config my-podman.toml
```

When you add or remove a command, add `accept` and `reject` examples for it. `--check` runs those examples, and they
catch mistakes before the policy is deployed. [Adding commands](adding-commands.md) explains the format.

If you change a shared parent such as `[commands.podman]`, change its definition the same way in every file that
defines it. Otherwise `--check` fails with an error naming both files.
