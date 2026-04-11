# Terminal execution

The terminal tool runs shell commands in a configurable execution environment. The environment is a pluggable "backend" — swap from local to Docker to a cloud VM without changing anything about how Hermes works.

## What it is

When Hermes calls `run_command("ls -la")`, the terminal tool dispatches it to the active backend. The backend handles the actual execution, captures output, and returns it. The agent loop treats all backends identically.

The backend abstraction lives in `tools/environments/base.py` as `BaseEnvironment`.

## The six backends

### Local (default)

Commands run directly on your machine, in the configured working directory.

```yaml
terminal:
  backend: "local"
  cwd: "."
  timeout: 180
```

`cwd: "."` means the directory where you launched `hermes`. For gateway mode, it defaults to your home directory.

**Security note:** The agent can run any command your user can run. Use the [approval flow](tool-system.md) to gate dangerous commands.

### Docker

Commands run inside a Docker container. The container is started on first use and stays running for the session lifetime.

```yaml
terminal:
  backend: "docker"
  cwd: "/workspace"
  docker_image: "nikolaik/python-nodejs:python3.11-nodejs20"
  docker_mount_cwd_to_workspace: true   # Mount your local dir into /workspace
  docker_forward_env:
    - "GITHUB_TOKEN"
    - "NPM_TOKEN"
```

By default, the local directory is NOT mounted (security). Set `docker_mount_cwd_to_workspace: true` to opt in.

### SSH

Commands run on a remote server via SSH. The agent code stays local; only commands and their output travel over the connection.

```yaml
terminal:
  backend: "ssh"
  cwd: "/home/myuser/project"
  ssh_host: "my-server.example.com"
  ssh_user: "myuser"
  ssh_port: 22
  ssh_key: "~/.ssh/id_rsa"
```

**Security benefit:** With SSH, the agent cannot read its own `.env` file (API keys are on your laptop, not the remote server). Good for isolation.

### Modal (serverless cloud)

Commands run on Modal's cloud infrastructure in ephemeral sandboxes. The sandbox hibernates when idle and wakes on demand — costs nearly nothing between sessions.

```yaml
terminal:
  backend: "modal"
  cwd: "/workspace"
  modal_image: "nikolaik/python-nodejs:python3.11-nodejs20"
```

Requires: `pip install modal && modal setup` (browser-based auth — no API key in `.env` needed).

### Daytona (workspace-as-code)

Commands run in a Daytona workspace that persists between sessions. Good for persistent cloud development environments.

```yaml
terminal:
  backend: "daytona"
  cwd: "/workspace"
```

Requires: Daytona account and the `daytona` CLI installed and authenticated.

### Singularity/Apptainer

Commands run inside a Singularity container. Common in HPC clusters where Docker is unavailable.

```yaml
terminal:
  backend: "singularity"
  cwd: "/workspace"
  singularity_image: "docker://nikolaik/python-nodejs:python3.11-nodejs20"
```

## Switching backends

Change the backend in `~/.hermes/config.yaml`:

```bash
hermes config set terminal.backend docker
hermes config set terminal.backend ssh
hermes config set terminal.backend local
```

Or for a single session:

```bash
hermes --terminal-backend modal
```

## Sudo support

All backends support sudo via password piping:

```yaml
terminal:
  sudo_password: "hunter2"
```

> **Warning:** The password is stored in plaintext in `config.yaml`. Only use on trusted, single-user machines.

Alternatives:
- **SSH backend**: Configure passwordless sudo on the remote server
- **Docker/Singularity**: Run as root inside the container (no sudo needed)
- **CLI without config**: Leave unset — you'll be prompted interactively with a 45-second timeout

## File synchronization

When using remote backends (SSH, Modal, Daytona), `tools/environments/file_sync.py` synchronizes files between your local machine and the remote environment. Hermes tracks which files it has written and keeps them in sync.

## Environment variables in containers

The agent's API keys are NOT automatically forwarded into Docker or Modal containers. This is intentional — the agent running inside a container shouldn't be able to read the keys used to call itself.

To forward specific env vars into Docker:

```yaml
terminal:
  docker_forward_env:
    - "GITHUB_TOKEN"
    - "NPM_TOKEN"
```

## Session lifecycle

Each backend instance has a configurable lifetime:

```yaml
terminal:
  lifetime_seconds: 300    # Clean up after 5 minutes of inactivity
```

After the lifetime expires, the backend is destroyed. The next command creates a fresh one. For Docker, this means a new container; for Modal, a new sandbox.

## Choosing a backend

| Use case | Recommended backend |
|----------|-------------------|
| Local development | `local` |
| Reproducible environments | `docker` |
| Agent isolated from its own code | `ssh` |
| GPU-intensive tasks | `modal` or `ssh` to a GPU server |
| HPC cluster | `singularity` |
| Persistent cloud dev environment | `daytona` |
| Security-conscious production | `ssh` or `docker` |

## Interaction with other subsystems

| Subsystem | Interaction |
|-----------|-------------|
| [Tool system](tool-system.md) | `run_command` and `run_interactive` dispatch here |
| [Agent loop](agent-loop.md) | Terminal results returned as tool results |
| Approval flow | Dangerous commands intercepted before reaching the backend |
