# What is Hermes Agent?

Hermes is an autonomous AI agent that learns from experience, runs anywhere, and reaches you from any messaging platform.

## The problem it solves

Most AI assistants forget everything when you close the tab. They can't act on your behalf while you're away. They're tied to a browser and can't run code, browse the web, or operate your computer. Hermes is the opposite: a persistent agent that accumulates knowledge, executes tasks using real tools, and remains reachable via Telegram (or Discord, Slack, WhatsApp, Signal, or email) even when your laptop is closed.

## How it works — the mental model

Think of Hermes as a capable contractor who:

1. **Lives on infrastructure you control** — a $5 VPS, a Modal serverless sandbox, a Docker container, or your laptop. You choose.
2. **Carries a growing knowledge base** — memory of facts, a deepening model of your preferences, and a library of skills it has learned or created.
3. **Can use real tools** — terminal commands, web search, file operations, browser automation, code execution, image generation, and more.
4. **Stays reachable** — you talk to it from Telegram while it executes commands on a remote VM. You never have to be at the terminal.
5. **Improves over time** — after completing a complex task, it often writes a skill capturing what it learned. Those skills get injected into future conversations automatically.

## Architecture overview

```
                        ┌─────────────────────────────────────┐
                        │           Entry Points               │
                        │  CLI (hermes)  │  Gateway (hermes    │
                        │                │   gateway start)    │
                        └────────┬───────┴──────────┬──────────┘
                                 │                  │
                        ┌────────▼──────────────────▼──────────┐
                        │           AIAgent (run_agent.py)      │
                        │  ┌─────────────────────────────────┐  │
                        │  │  Prompt Builder                  │  │
                        │  │  (identity + skills + context    │  │
                        │  │   + memory + tool schemas)       │  │
                        │  └──────────────┬──────────────────┘  │
                        │                 │                      │
                        │  ┌──────────────▼──────────────────┐  │
                        │  │     LLM API (OpenAI-compatible)  │  │
                        │  │  OpenRouter / Anthropic / Gemini │  │
                        │  │  Qwen / GLM / Kimi / MiniMax     │  │
                        │  └──────────────┬──────────────────┘  │
                        │                 │                      │
                        │  ┌──────────────▼──────────────────┐  │
                        │  │       Tool Registry              │  │
                        │  │  web · terminal · file · browser │  │
                        │  │  code · delegate · MCP · cron   │  │
                        │  └──────────────┬──────────────────┘  │
                        │                 │                      │
                        │  ┌──────────────▼──────────────────┐  │
                        │  │   Terminal Backends              │  │
                        │  │  local │ docker │ ssh │ modal   │  │
                        │  │  daytona │ singularity           │  │
                        │  └─────────────────────────────────┘  │
                        └──────────────────────────────────────┘
                                          │
                        ┌─────────────────▼─────────────────┐
                        │         Session DB (SQLite FTS5)   │
                        │   conversations · search · memory  │
                        └───────────────────────────────────┘
```

## How the pieces fit together

A typical conversation:

1. You send a message — from the CLI terminal, Telegram, Discord, or anywhere else.
2. Hermes builds the system prompt: your identity file (SOUL.md), active skills, context files, memory, and tool schemas.
3. It calls your chosen LLM with the full context.
4. If the model returns tool calls, Hermes dispatches them to the tool registry, collects results, and loops back to the model.
5. When the model returns text, Hermes delivers it to you (streaming in the CLI, as a message on your platform) and saves the conversation to SQLite.
6. Periodically, Hermes nudges itself to update its memory file with things worth remembering across sessions.

If the conversation grows long, an automatic compression step summarizes the middle turns using a fast auxiliary model (typically Gemini), keeping the context window manageable without losing important history.

## What Hermes is not

- **Not a cloud service.** You run it yourself. There is no Hermes-hosted endpoint.
- **Not a chatbot widget.** It's a full agent framework with a terminal interface, persistent state, and tool execution.
- **Not locked to one model.** Any OpenAI-compatible API works. Switch models with `/model` — no code changes needed.
- **Not opinionated about infrastructure.** It runs on your laptop with `hermes`, in a Docker container with one config line, or on serverless Modal that costs nothing when idle.

## Key subsystems

| Subsystem | What it does |
|-----------|-------------|
| [Agent loop](../concepts/agent-loop.md) | Conversation → LLM → tool dispatch → response cycle |
| [Tool system](../concepts/tool-system.md) | Self-registering tools, toolsets, availability checks |
| [Skill system](../concepts/skill-system.md) | Procedural memory injected into every conversation |
| [Messaging gateway](../concepts/gateway.md) | Multi-platform adapter (Telegram, Discord, Slack, etc.) |
| [Memory and learning](../concepts/memory-and-learning.md) | Cross-session persistence, user modeling |
| [Terminal execution](../concepts/terminal-execution.md) | Pluggable backend (local, Docker, SSH, Modal, etc.) |
| [Session persistence](../concepts/session-persistence.md) | SQLite FTS5 session database, search, trajectories |
