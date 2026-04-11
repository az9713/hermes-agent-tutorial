# Configure memory

Set up and tune Hermes's persistent memory system to match your needs.

## Prerequisites

- Hermes installed and running
- At least one conversation completed (so there's something to remember)

## Default setup: local files

Out of the box, Hermes uses local Markdown files. No configuration needed.

Files created automatically on first run:

| File | Purpose |
|------|---------|
| `~/.hermes/memories/MEMORY.md` | Facts about you and your environment |
| `~/.hermes/memories/USER.md` | Your personal profile and preferences |

View current memory:

```bash
hermes memory show
```

## Tell Hermes what to remember

In a conversation:

```
you: remember that my production server is at 192.168.1.10 and the deploy user is deploy-bot
hermes: Got it. Adding to memory.
```

Or just have a normal conversation — Hermes decides what's worth remembering based on context.

## Manually edit memory

```bash
# Open MEMORY.md in your default editor
hermes memory edit
```

Or edit directly:

```bash
nano ~/.hermes/memories/MEMORY.md
```

## Set up Honcho (dialectic user modeling)

Honcho builds a richer user model than static files — it tracks beliefs, resolves contradictions, and makes inferences.

1. Create an account at [app.honcho.dev](https://app.honcho.dev) and get an API key.

2. Add the key to `~/.hermes/.env`:

```bash
HONCHO_API_KEY=your-honcho-api-key
```

3. Create the Honcho config file:

```bash
mkdir -p ~/.honcho
cat > ~/.honcho/config.json << 'EOF'
{
    "enabled": true,
    "app_name": "hermes"
}
EOF
```

4. Restart Hermes. The Honcho backend activates automatically.

## Set up Mem0

1. Get an API key at [app.mem0.ai](https://app.mem0.ai)

2. Add to `~/.hermes/.env`:

```bash
MEM0_API_KEY=your-mem0-key
```

3. Enable Mem0 in `~/.hermes/config.yaml`:

```yaml
memory:
  backend: "mem0"
```

## Disable memory

To run without persistent memory (privacy mode):

```yaml
# ~/.hermes/config.yaml
memory:
  enabled: false
```

Or for a single session:

```bash
hermes --no-memory
```

## Clear all memory

```bash
hermes memory clear
```

This deletes `MEMORY.md` and `USER.md`. It does not affect cloud backends (Honcho, Mem0) — clear those through their respective dashboards.

## Tune memory nudges

The memory nudge frequency controls how often Hermes checks whether something is worth remembering:

```yaml
# ~/.hermes/config.yaml
memory:
  nudge_frequency: "after_complex_tasks"  # Options: always, after_complex_tasks, never
```

## Verification

After setting up memory, verify it's working:

```bash
hermes memory show
```

Then start a conversation and tell Hermes something specific:

```
you: my GitHub username is jsmith and I prefer Python over JavaScript
```

End the conversation, start a new one, and verify Hermes remembers:

```
you: what's my GitHub username?
hermes: Your GitHub username is jsmith.
```

## Troubleshooting

**Hermes doesn't remember things across sessions**
- Check that memory files exist: `ls ~/.hermes/memories/`
- Verify the memory backend is configured correctly: `hermes memory show`
- If using Honcho, check `~/.honcho/config.json` exists with `"enabled": true`

**Memory is growing too large and slowing down the system prompt**
- Edit `MEMORY.md` and remove stale or redundant entries
- Run `hermes memory compact` to deduplicate and condense entries

**Honcho is enabled but not being used**
- Check `HONCHO_API_KEY` is in `~/.hermes/.env`
- Verify `~/.honcho/config.json` contains `"enabled": true`
- Check logs: `hermes logs` for Honcho connection errors
