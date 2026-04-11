# System design

Technical architecture for developers who will work on (not just use) Hermes.

## High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Entry Points                            │
│                                                                  │
│  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │  CLI (hermes)    │   │  Gateway         │   │  ACP Server │ │
│  │  cli.py          │   │  gateway/run.py  │   │  acp_adapter│ │
│  │  HermesCLI       │   │  GatewayRunner   │   │  /entry.py  │ │
│  └────────┬─────────┘   └────────┬─────────┘   └──────┬──────┘ │
│           │                      │                     │         │
└───────────┼──────────────────────┼─────────────────────┼─────────┘
            │                      │                     │
            └──────────────────────┼─────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │   AIAgent  (run_agent.py)     │
                    │                               │
                    │  ┌─────────────────────────┐  │
                    │  │  prompt_builder.py       │  │
                    │  │  Assembles system prompt  │  │
                    │  │  each turn:               │  │
                    │  │  - SOUL.md               │  │
                    │  │  - Skills                │  │
                    │  │  - Context files         │  │
                    │  │  - MEMORY.md + USER.md   │  │
                    │  │  - Tool schemas          │  │
                    │  └───────────────┬──────────┘  │
                    │                  │              │
                    │  ┌───────────────▼──────────┐  │
                    │  │  LLM Client              │  │
                    │  │  (OpenAI-compatible API) │  │
                    │  │  Streaming response      │  │
                    │  └───────────────┬──────────┘  │
                    │                  │              │
                    │  ┌───────────────▼──────────┐  │
                    │  │  Tool Dispatch            │  │
                    │  │  model_tools.py           │  │
                    │  │  registry.dispatch()     │  │
                    │  └───────────────┬──────────┘  │
                    │                  │              │
                    │  ┌───────────────▼──────────┐  │
                    │  │  Context Compressor       │  │
                    │  │  context_compressor.py   │  │
                    │  │  (triggers at 85% limit) │  │
                    │  └─────────────────────────┘  │
                    └──────────────────────────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               │                   │                   │
    ┌──────────▼──────┐  ┌─────────▼───────┐  ┌───────▼───────┐
    │  Tool Registry   │  │  SessionDB      │  │  Skill Loader  │
    │  tools/          │  │  hermes_state.py│  │  agent/        │
    │  registry.py     │  │  SQLite + FTS5  │  │  skill_utils.py│
    │  53 tools        │  │  ~/.hermes/     │  │  skills/       │
    └──────────────────┘  │  state.db       │  └───────────────┘
                          └─────────────────┘
```

## Component breakdown

### AIAgent (`run_agent.py`)

The central class. Owns:
- Message history for the current conversation
- Session lifecycle (start, save, restore)
- Token counting and budget tracking
- The main `_run_agent_loop()` execution cycle

**Key methods:**
- `chat(message)` — single turn (used by gateway and ACP)
- `run_conversation()` — interactive loop (used by CLI)
- `_run_agent_loop()` — core: build prompt → LLM call → tool dispatch → loop

### HermesCLI (`cli.py`)

The interactive terminal UI. Built on `prompt_toolkit`. Owns:
- Multiline input with autocomplete and history
- Streaming output rendering
- Slash command dispatch
- Session display (token counts, model name, etc.)

The CLI creates an `AIAgent` and calls `run_conversation()` in a loop.

### GatewayRunner (`gateway/run.py`)

The messaging gateway process. Owns:
- Platform adapter lifecycle
- Message routing from platforms to `AIAgent` instances
- Session store (`SessionStore` from `gateway/session.py`)
- Cron scheduler integration
- Hook system for event processing

One `AIAgent` instance per active conversation session. Sessions are keyed by `(platform, chat_id, user_id)`.

### Tool Registry (`tools/registry.py`)

Central dictionary of all tools. Provides:
- Schema registration and lookup
- Handler dispatch
- Availability checking (per-tool `check_fn`)
- Toolset group management

Tools call `registry.register()` at import time. `model_tools.py` imports all tool modules to trigger registrations.

### SessionDB (`hermes_state.py`)

SQLite database with FTS5. Stores all conversation history. Provides:
- Session CRUD
- Full-text search across all conversations
- Session titles
- Compression tracking

Location: `~/.hermes/state.db` (follows `HERMES_HOME`).

### Prompt Builder (`agent/prompt_builder.py`)

Assembles the full system prompt on every API call. Order of injection:
1. Identity (SOUL.md)
2. Current date/time
3. Tool schemas (active tools only)
4. Active skill content
5. Context file references
6. Memory (MEMORY.md + USER.md)
7. Session metadata (cwd, profile, platform)

The system prompt is ephemeral — never stored in the message list. See [ADR 004](adr/004-ephemeral-system-prompts.md).

### Context Compressor (`agent/context_compressor.py`)

Triggered when token count approaches `context_length * compression_threshold`. Process:
1. Identifies "middle" turns (excludes first turn and most recent N turns)
2. Calls the auxiliary model (Gemini Flash) to summarize the middle turns
3. Replaces the middle turns with the summary in the in-memory message list
4. Original history is preserved in SessionDB

## Data flows

### CLI conversation turn

```
User types message (prompt_toolkit)
  → HermesCLI._handle_input()
  → AIAgent.chat(message)
  → prompt_builder.build_system_prompt()
  → openai_client.chat.completions.create() [streaming]
  → Stream response to terminal
  → If tool_calls: registry.dispatch() → tool handlers
  → Loop back to LLM with tool results
  → Final text response streamed to terminal
  → SessionDB.save_turn()
  → Check context size → maybe compress
```

### Gateway message

```
Platform sends message (Telegram, Discord, etc.)
  → PlatformAdapter._handle_incoming()
  → Check allow-list
  → SessionStore.get_or_create()
  → AIAgent.chat(message)
  → [Same as CLI from here]
  → gateway/delivery.py.send_message()
  → Platform delivers response to user
```

### Cron job execution

```
cron/scheduler.py fires job at scheduled time
  → Create isolated AIAgent for job
  → AIAgent.run_single_task(job.description)
  → [Agent executes the task]
  → cron/delivery.py.deliver(result, channel)
  → Platform delivers to home channel
```

## Key design decisions

| Decision | Rationale | ADR |
|----------|-----------|-----|
| Tool self-registration | No central list to maintain | [ADR 001](adr/001-tool-self-registration.md) |
| OpenAI-compatible API layer | Provider-agnostic, easy to swap | [ADR 002](adr/002-openai-compatible-api.md) |
| SQLite + FTS5 for sessions | Zero dependencies, full-text search built in | [ADR 003](adr/003-sqlite-fts-session-storage.md) |
| Ephemeral system prompts | Enables prompt caching, prevents stale context | [ADR 004](adr/004-ephemeral-system-prompts.md) |

## Scaling characteristics

**What scales well:**
- Multiple gateway platform adapters (each is an independent async task)
- Parallel subagent delegation (each delegate is an isolated AIAgent)
- Modal/Daytona backend for compute-heavy tasks (serverless, auto-scales)

**What doesn't scale horizontally:**
- SessionDB is a local SQLite file — not suitable for multi-machine deployments
- Gateway runs as a single process — HA requires external process management (systemd, k8s)

**What limits throughput:**
- LLM API rate limits (tracked in `agent/rate_limit_tracker.py`)
- Terminal backend concurrency (Docker/Modal have limits on parallel sessions)

## External dependencies

| Service | Purpose | Required? |
|---------|---------|----------|
| LLM API (OpenRouter/Anthropic/etc.) | Language model | Yes |
| Auxiliary model (Gemini Flash) | Context compression, vision | When compression is on |
| Platform APIs (Telegram/Discord/etc.) | Messaging | For gateway only |
| Browserbase or local Camoufox | Browser automation | When browser tools are used |
| FAL.ai | Image generation | When `generate_image` is used |
| Parallel/Exa/Firecrawl | Web search | When `web_search` is used |
| Modal / Daytona | Cloud terminal backend | When those backends are used |
| Honcho / Mem0 / etc. | Memory | When cloud memory backends are used |

## File layout reference

```
run_agent.py              ← AIAgent — core conversation loop
cli.py                    ← HermesCLI — interactive TUI
hermes_state.py           ← SessionDB — SQLite + FTS5
model_tools.py            ← Tool discovery and dispatch orchestration
toolsets.py               ← Toolset presets per platform
hermes_constants.py       ← HERMES_HOME resolution
hermes_logging.py         ← Logging configuration

agent/                    ← Agent internals
  prompt_builder.py       ← System prompt assembly
  context_compressor.py   ← Auto-compression
  skill_utils.py          ← Skill discovery and loading
  skill_commands.py       ← Skill slash command registry
  memory_manager.py       ← Memory state and lifecycle
  memory_provider.py      ← Memory backend abstraction
  model_metadata.py       ← Context lengths, token estimation
  smart_model_routing.py  ← Cheap/expensive model routing

tools/                    ← Tool implementations
  registry.py             ← Central tool registry
  terminal_tool.py        ← Terminal execution
  file_tools.py           ← File operations
  web_tools.py            ← Web search and extract
  browser_tool.py         ← Browser automation (Browserbase)
  browser_camofox.py      ← Browser automation (local Camoufox)
  code_execution_tool.py  ← Python sandbox
  delegate_tool.py        ← Subagent spawning
  mcp_tool.py             ← MCP client
  cronjob_tools.py        ← Cron scheduling
  environments/           ← Terminal backends
    local.py, docker.py, ssh.py, modal.py, daytona.py, singularity.py

gateway/                  ← Messaging gateway
  run.py                  ← GatewayRunner — main loop
  session.py              ← SessionStore
  delivery.py             ← Message delivery
  platforms/              ← Platform adapters (21 platforms)

cron/                     ← Job scheduler
  scheduler.py            ← Cron scheduler
  jobs.py                 ← Job model
  delivery.py             ← Job output delivery

hermes_cli/               ← CLI commands
  main.py                 ← Entry point
  config.py               ← Config management
  commands.py             ← Command registry (single source of truth)
  setup.py                ← Setup wizard

plugins/                  ← Plugin implementations
  memory/                 ← Memory backends (Honcho, Mem0, etc.)

skills/                   ← Bundled skills
optional-skills/          ← Optional official skills
```
