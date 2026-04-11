# Deploy with Docker

Run Hermes in a Docker container for a reproducible, isolated environment.

Docker is useful for:
- Reproducible environments (same container on dev and prod)
- Running Hermes on a server without a Python installation
- Isolating the agent's file access from the host system

## Prerequisites

- Docker Engine installed and running: `docker --version`
- An LLM API key (OpenRouter or similar)

## Option A: Docker terminal backend only

The most common use case: Hermes itself runs locally, but commands execute inside a container. This gives you an isolated sandbox for terminal tool calls.

In `~/.hermes/config.yaml`:

```yaml
terminal:
  backend: "docker"
  docker_image: "nikolaik/python-nodejs:python3.11-nodejs20"
  cwd: "/workspace"
  docker_mount_cwd_to_workspace: true  # Optional: mount current dir into /workspace
```

Start Hermes normally:

```bash
hermes
```

The first `run_command` call starts the container. Subsequent commands reuse it until the lifetime expires.

## Option B: Run Hermes itself in Docker

Run the entire Hermes process inside a container. Good for server deployments.

### 1. Build the image

```bash
cd /path/to/hermes-agent
docker build -t hermes-agent:latest .
```

Expected output:

```
[+] Building 45.2s (12/12) FINISHED
 => [internal] load build definition from Dockerfile
 => ...
 => naming to docker.io/library/hermes-agent:latest
```

### 2. Prepare config

Create a config directory on the host that maps to `~/.hermes` inside the container:

```bash
mkdir -p ~/hermes-data
# Copy or create config files
cp ~/.hermes/config.yaml ~/hermes-data/config.yaml  # if you have one
```

Create `~/hermes-data/.env` with your API keys:

```bash
cat > ~/hermes-data/.env << 'EOF'
OPENROUTER_API_KEY=sk-or-your-key
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USERS=123456789
EOF
```

### 3. Run the CLI

```bash
docker run -it \
  -v ~/hermes-data:/root/.hermes \
  hermes-agent:latest \
  hermes
```

### 4. Run the gateway

```bash
docker run -d \
  --name hermes-gateway \
  --restart unless-stopped \
  -v ~/hermes-data:/root/.hermes \
  hermes-agent:latest \
  hermes gateway start
```

Check logs:

```bash
docker logs -f hermes-gateway
```

### 5. Verify

```bash
docker ps
# Should show hermes-gateway running

docker logs hermes-gateway
# Should show platform connections established
```

## Docker Compose

For a persistent deployment with auto-restart:

```yaml
# docker-compose.yml
version: "3.8"
services:
  hermes:
    image: hermes-agent:latest
    build: .
    restart: unless-stopped
    volumes:
      - ./hermes-data:/root/.hermes
    command: hermes gateway start
    environment:
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS}
```

Start:

```bash
docker compose up -d
docker compose logs -f
```

## Security considerations

**File access** — By default, the container has no access to your host filesystem except the mounted `hermes-data` directory. The agent cannot read your host SSH keys or other sensitive files.

**Network** — The container has outbound network access by default. Restrict with `--network none` and explicit port exposure if needed.

**Capabilities** — The `Dockerfile` drops most Linux capabilities. Do not run the container with `--privileged`.

**API keys** — Keys in `~/hermes-data/.env` are visible to processes inside the container. Use Docker secrets or a secret manager for production.

## Troubleshooting

**Container starts but gateway fails to connect**
- Check that bot tokens are correctly set in the `.env` file
- Verify network connectivity from inside the container: `docker exec hermes-gateway curl https://api.telegram.org`

**Permission denied on mounted volume**
- Check that `~/hermes-data` is owned by your user
- Try `chmod 755 ~/hermes-data`

**Image build fails**
- Check Docker has enough disk space: `docker system df`
- Check Python version requirements in `pyproject.toml`
