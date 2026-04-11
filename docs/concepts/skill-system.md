# The skill system

Skills are units of procedural memory. They give Hermes context-specific knowledge, workflows, and examples by injecting Markdown content directly into the system prompt.

## What a skill is

A skill is a directory with at least one file: `SKILL.md`. Optionally, it includes scripts, data files, or sub-skills. When a skill is active, its `SKILL.md` content is included in the system prompt for every conversation.

Example skill structure:

```
skills/github/
├── SKILL.md          ← Instructions, examples, workflows
├── review.sh         ← Helper script the agent can call
└── INSTALL.md        ← Optional: setup instructions for the user
```

`SKILL.md` is plain Markdown. It can contain anything: slash command definitions, step-by-step workflows, code examples, tool usage patterns, API reference snippets, or agent-to-agent protocols.

## Three tiers of skills

### Bundled skills (`skills/`)

Shipped with Hermes and active by default (subject to platform and prerequisite filtering). Organized by category:

| Category | Examples |
|----------|---------|
| `apple/` | macOS Notes, Reminders, iMessage, Find My |
| `autonomous-ai-agents/` | Claude Code, Codex, Hermes-to-Hermes, OpenCode |
| `creative/` | ASCII art, video generation, p5.js, Excalidraw, songwriting |
| `data-science/` | Jupyter kernel integration |
| `devops/` | Webhook subscriptions |
| `email/` | Himalaya email client |
| `github/` | Code review, auth, repository inspection |
| `gaming/` | Minecraft, Pokémon |
| `research/` | arXiv, papers, knowledge graph |
| `security/` | Security scanning, audit tools |
| `productivity/` | Task management, calendar |
| `note-taking/` | Notion, Obsidian |

### Optional official skills (`optional-skills/`)

Official Nous Research skills that ship with Hermes but are off by default. Enable them with:

```bash
hermes skills enable <skill-name>
```

Useful for capabilities you might not want active all the time (blockchain, advanced ML, etc.).

### Hub skills (community, from [agentskills.io](https://agentskills.io))

Community-contributed skills discoverable and installable via:

```bash
hermes skills search "web scraping"
hermes skills install web-scraping-pro
```

Hub skills are installed to `~/.hermes/skills/` and activated automatically. Hermes scans hub skills for malicious content during install.

## How skills are loaded

1. At startup, `agent/skill_utils.py` scans all skill directories.
2. Each skill is checked for platform compatibility (macOS-only skills are skipped on Linux, etc.) and prerequisite tools.
3. Active skill `SKILL.md` contents are assembled into the system prompt by `agent/prompt_builder.py`.
4. Skills can define slash commands (via a specific SKILL.md syntax) that appear in `/skills` and autocomplete.

## Skill slash commands

Skills can define slash commands that invoke specific workflows. When the user types `/<skill-name>`, the skill's command handler runs. This mechanism is implemented in `agent/skill_commands.py`.

A skill defines a command like this in its `SKILL.md`:

```markdown
## /github-review

Reviews the current PR for correctness, test coverage, and security issues.

Usage: /github-review [--branch <name>]
```

Hermes parses these definitions and injects them as user messages (not system prompt), preserving prompt caching.

## Managing skills

```bash
hermes skills            # List all skills and their status
hermes skills enable <name>    # Enable a skill
hermes skills disable <name>   # Disable a skill
hermes skills install <name>   # Install from Skills Hub
hermes skills uninstall <name> # Remove a hub skill
hermes skills update     # Update all installed hub skills
```

## Creating a skill

See [add a skill](../guides/add-a-skill.md) for the full how-to.

The short version:

1. Create a directory in `~/.hermes/skills/my-skill/`
2. Write `SKILL.md` with instructions, examples, and any slash commands
3. The skill loads automatically on next `hermes` start

## Platform filtering

Skills can declare platform requirements. A skill that only works on macOS includes:

```markdown
<!-- HERMES_SKILL_PLATFORMS: macos -->
```

A skill requiring specific tools:

```markdown
<!-- HERMES_SKILL_REQUIRES: run_command, web_search -->
```

Skills that don't meet the current platform or tool availability are silently skipped — they don't appear in the system prompt.

## Self-improving skills

After Hermes completes a complex or novel task, it sometimes creates a new skill automatically, capturing the steps it figured out. On subsequent occasions, the skill is available and the task requires less effort.

Similarly, if an existing skill's instructions were ambiguous or led to a mistake, the agent may update the skill's `SKILL.md` to clarify the workflow. This is the self-improvement loop.

## Interaction with other subsystems

| Subsystem | Interaction |
|-----------|-------------|
| [Agent loop](agent-loop.md) | Skills injected into system prompt each turn |
| [Tool system](tool-system.md) | Skills may reference and guide use of specific tools |
| [Memory](memory-and-learning.md) | Skills are one layer of the learning loop; memory is another |
