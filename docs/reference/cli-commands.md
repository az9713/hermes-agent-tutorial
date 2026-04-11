# CLI commands reference

All commands available in Hermes. Slash commands (`/new`, `/model`, etc.) work in both the CLI and the messaging gateway.

---

## Top-level `hermes` subcommands

Run from your shell (not inside a conversation):

| Command | What it does |
|---------|-------------|
| `hermes` | Start an interactive conversation |
| `hermes setup` | Run the interactive setup wizard |
| `hermes model` | Open the model selector |
| `hermes tools` | Show and configure available tools |
| `hermes config set <key> <value>` | Set a single config value |
| `hermes config get <key>` | Get a config value |
| `hermes config show` | Show full configuration |
| `hermes login` | Authenticate with a provider (OAuth) |
| `hermes login --provider openai-codex` | Authenticate with OpenAI Codex |
| `hermes update` | Update Hermes to the latest version |
| `hermes doctor` | Run diagnostics and troubleshoot issues |
| `hermes search <query>` | Search past conversations |
| `hermes sessions` | List recent sessions |
| `hermes memory show` | Show current memory files |
| `hermes memory edit` | Edit MEMORY.md in your default editor |
| `hermes memory clear` | Clear all memory |
| `hermes logs` | Browse gateway and session logs |
| `hermes dump` | Dump debug info for bug reports |
| `hermes uninstall` | Remove Hermes (with confirmation) |
| `hermes --version` | Print version |

### hermes gateway subcommands

| Command | What it does |
|---------|-------------|
| `hermes gateway setup` | Interactive setup for all platforms |
| `hermes gateway start` | Start the messaging gateway |
| `hermes gateway stop` | Stop the running gateway |
| `hermes gateway restart` | Restart the gateway gracefully |
| `hermes gateway status` | Show platform connection status |

### hermes cron subcommands

| Command | What it does |
|---------|-------------|
| `hermes cron list` | List all scheduled jobs |
| `hermes cron create "<schedule>" "<description>"` | Create a new job |
| `hermes cron delete <job-id>` | Delete a job |
| `hermes cron run <job-id>` | Run a job immediately |

### hermes skills subcommands

| Command | What it does |
|---------|-------------|
| `hermes skills` | List all skills and their status |
| `hermes skills enable <name>` | Enable a skill |
| `hermes skills disable <name>` | Disable a skill |
| `hermes skills install <name>` | Install a skill from the Skills Hub |
| `hermes skills uninstall <name>` | Remove an installed hub skill |
| `hermes skills update` | Update all hub skills |
| `hermes skills search <query>` | Search the Skills Hub |

### hermes claw subcommands (OpenClaw migration)

| Command | What it does |
|---------|-------------|
| `hermes claw migrate` | Migrate from OpenClaw (interactive) |
| `hermes claw migrate --dry-run` | Preview migration |
| `hermes claw migrate --preset user-data` | Migrate without secrets |
| `hermes claw migrate --overwrite` | Overwrite existing conflicts |

### CLI flags

| Flag | What it does |
|------|-------------|
| `--model <id>` | Override default model for this session |
| `--provider <name>` | Override provider for this session |
| `-w`, `--worktree` | Create an isolated git worktree for this session |
| `--no-memory` | Disable memory loading for this session |
| `--terminal-backend <name>` | Override terminal backend |
| `--profile <name>` | Use a specific Hermes profile |
| `--cwd <path>` | Override working directory |

---

## Slash commands (in-conversation)

Available in both the CLI and the messaging gateway.

### Conversation management

| Command | What it does |
|---------|-------------|
| `/new` or `/reset` | Start a fresh conversation (clears history) |
| `/retry` | Retry the last user message |
| `/undo` | Remove the last exchange (user + assistant) |
| `/stop` | Interrupt the currently running task (gateway) |

### Model and persona

| Command | What it does |
|---------|-------------|
| `/model [provider:model]` | Change the model (interactive picker if no arg) |
| `/personality [name]` | Set a personality/persona |
| `/soul` | Show or edit the current SOUL.md |

### Context and memory

| Command | What it does |
|---------|-------------|
| `/context add <path>` | Add a file to the conversation context |
| `/context list` | List all context files |
| `/context remove <path>` | Remove a context file |
| `/memory` | Show current memory |
| `/memory update` | Trigger a memory update now |

### Session management

| Command | What it does |
|---------|-------------|
| `/sessions` | List recent sessions |
| `/search <query>` | Search past conversations |
| `/title <title>` | Set the current session title |
| `/save` | Save the current session (auto-save is always on) |

### Usage and diagnostics

| Command | What it does |
|---------|-------------|
| `/usage` | Show token usage and cost for the current session |
| `/insights [--days N]` | Show usage insights across sessions |
| `/compress` | Manually compress the conversation context |
| `/platforms` (CLI) | Show connected gateway platforms |
| `/status` (gateway) | Show gateway and platform status |

### Skills

| Command | What it does |
|---------|-------------|
| `/skills` | Browse and manage skills |
| `/<skill-name>` | Invoke a skill's slash command |

### Cron (in gateway)

| Command | What it does |
|---------|-------------|
| `/cron list` | List scheduled jobs |
| `/cron create` | Create a job interactively |
| `/cron delete <id>` | Delete a job |
| `/sethome` | Set this chat as the home channel |

### Help

| Command | What it does |
|---------|-------------|
| `/help` | Show all available commands |

---

## Keyboard shortcuts (CLI)

| Shortcut | Action |
|----------|--------|
| `Enter` | Submit message |
| `Shift+Enter` or `Alt+Enter` | Newline (multiline input) |
| `Ctrl+C` | Interrupt current task (cancels mid-response) |
| `Ctrl+D` | Exit Hermes |
| `Up` / `Down` | Scroll message history |
| `Tab` | Autocomplete slash commands |
| `Ctrl+R` | Search command history |
