# Quickstart

Install Hermes and have your first conversation in under 10 minutes.

See [prerequisites](prerequisites.md) before starting.

---

## 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

The installer:
- Installs `uv` if not present
- Clones the repo to `~/.hermes-agent/` (or your chosen location)
- Creates a virtual environment and installs all Python dependencies
- Adds `hermes` to your PATH via `~/.bashrc` / `~/.zshrc`

Expected output (abbreviated):

```
[hermes] Detected: Ubuntu 22.04
[hermes] Installing uv...
[hermes] Cloning hermes-agent...
[hermes] Installing dependencies...
[hermes] Done! hermes is now available in your PATH.
```

## 2. Reload your shell

```bash
source ~/.bashrc    # or: source ~/.zshrc  (macOS with zsh)
```

Verify the install:

```bash
hermes --version
# hermes 0.8.0
```

## 3. Run setup

```bash
hermes setup
```

The interactive wizard walks you through:
1. Choosing your LLM provider (OpenRouter recommended)
2. Entering your API key
3. Selecting your default model
4. Optionally configuring tools (web search, browser, etc.)

Expected output:

```
Welcome to Hermes setup!
> Choose provider: OpenRouter
> API key: sk-or-...
> Default model: anthropic/claude-opus-4.6
✓ Config saved to ~/.hermes/config.yaml
```

If you're migrating from OpenClaw, the wizard detects `~/.openclaw` and offers to import your settings, skills, and memories automatically.

## 4. Start chatting

```bash
hermes
```

Expected output:

```
Hermes 0.8.0 — anthropic/claude-opus-4.6 via OpenRouter
Type a message or /help for commands. Ctrl+C to cancel.

>
```

Type anything and press Enter. Hermes streams the response. Press `Ctrl+C` to interrupt mid-response and redirect.

## What just happened

When you ran `hermes`:
1. Hermes built a system prompt from your SOUL.md (persona), active skills, and any context files.
2. It started an interactive TUI powered by `prompt_toolkit` with autocomplete and multiline editing.
3. Your message was sent to the LLM. Tool calls (if any) were dispatched and results fed back to the model.
4. The conversation was saved to `~/.hermes/state.db`.

## Next steps

- [Onboarding walkthrough](onboarding.md) — understand the mental model before going further
- [Agent loop concepts](../concepts/agent-loop.md) — how the conversation cycle works
- [CLI commands reference](../reference/cli-commands.md) — all slash commands
- [Configuration reference](../reference/configuration.md) — every config option
- [Messaging gateway](../concepts/gateway.md) — set up Telegram, Discord, Slack
