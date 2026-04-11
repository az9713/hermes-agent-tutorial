# Hermes Agent

A self-improving AI agent built by Nous Research. It learns from experience, talks to you from Telegram while working on a cloud VM, and runs on any infrastructure from a $5 VPS to a GPU cluster.

---

## Documentation

| Section | What's inside |
|---------|--------------|
| [Overview](overview/what-is-this.md) | Mental model, architecture, what Hermes is and is not |
| [Key concepts](overview/key-concepts.md) | Glossary of every important term |
| [Prerequisites](getting-started/prerequisites.md) | Exact dependencies with verify commands |
| [Quickstart](getting-started/quickstart.md) | Install → first conversation in under 10 minutes |
| [Onboarding](getting-started/onboarding.md) | Zero-to-hero conceptual walkthrough for newcomers |
| [Agent loop](concepts/agent-loop.md) | How the core conversation → tool → response cycle works |
| [Tool system](concepts/tool-system.md) | 40+ tools, registry, toolsets, availability |
| [Skill system](concepts/skill-system.md) | Procedural memory, bundled/optional/hub skills |
| [Messaging gateway](concepts/gateway.md) | Telegram, Discord, Slack, WhatsApp, Signal, Email |
| [Memory and learning](concepts/memory-and-learning.md) | Persistent memory, user modeling, learning loop |
| [Terminal execution](concepts/terminal-execution.md) | Local, Docker, SSH, Modal, Daytona, Singularity backends |
| [Session persistence](concepts/session-persistence.md) | SQLite FTS5, session search, trajectory logs |
| [Add a tool](guides/add-a-tool.md) | Implement and register a new tool |
| [Add a skill](guides/add-a-skill.md) | Create a new skill for the skills system |
| [Add a platform](guides/add-a-platform.md) | Integrate a new messaging platform into the gateway |
| [Configure memory](guides/configure-memory.md) | Set up and tune persistent memory |
| [Deploy with Docker](guides/deploy-with-docker.md) | Run Hermes in a container |
| [Use cron scheduling](guides/use-cron-scheduling.md) | Schedule tasks with platform delivery |
| [Configuration reference](reference/configuration.md) | Every config.yaml field with defaults |
| [Environment variables](reference/env-vars.md) | All environment variables |
| [CLI commands](reference/cli-commands.md) | Every slash command and subcommand |
| [Tools reference](reference/tools.md) | All tools, schemas, and availability |
| [System design](architecture/system-design.md) | Architecture diagram, data flows, component breakdown |
| [ADR 001: Tool self-registration](architecture/adr/001-tool-self-registration.md) | Why tools register themselves at import time |
| [ADR 002: OpenAI-compatible API layer](architecture/adr/002-openai-compatible-api.md) | Why every provider speaks OpenAI protocol |
| [ADR 003: SQLite + FTS5 session storage](architecture/adr/003-sqlite-fts-session-storage.md) | Why SQLite with full-text search |
| [ADR 004: Ephemeral system prompts](architecture/adr/004-ephemeral-system-prompts.md) | Why system prompts are rebuilt every turn |
| [Troubleshooting](troubleshooting/common-issues.md) | Top issues and fixes |

> **New here?** Start with [what is Hermes](overview/what-is-this.md), then follow the [quickstart](getting-started/quickstart.md).
