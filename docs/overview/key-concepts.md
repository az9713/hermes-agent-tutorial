# Key concepts

Definitions for every term that appears across the Hermes documentation.

---

**Agent loop** — The core execution cycle: build system prompt → call LLM → dispatch tool calls → loop until text response → save session. Implemented in `run_agent.py:AIAgent`.

**AIAgent** — The central class in `run_agent.py` that owns the conversation state, tool dispatch, and session lifecycle. Every conversation, whether from the CLI or the gateway, runs through an `AIAgent` instance.

**ACP (Agent Client Protocol)** — The protocol that lets Hermes run as an AI assistant inside VS Code, Zed, and JetBrains IDEs. Implemented in `acp_adapter/`.

**Approval flow** — When the agent attempts a command flagged as dangerous (e.g., `sudo rm`), it pauses and asks the user for confirmation before executing. Configured via an allowlist. Implemented in `tools/approval.py`.

**Auxiliary client** — A secondary LLM client used for lower-cost tasks that don't need the main model: vision analysis and context compression summarization. Defaults to Gemini Flash.

**Bundled skills** — Skills that ship with Hermes in the `skills/` directory and are active by default (subject to platform filtering). Contrast with *optional skills* and *hub skills*.

**Cron job** — A scheduled task that Hermes runs on a timer and delivers to a configured platform channel. Created and managed via `hermes cron` commands.

**Context compression** — Automatic summarization of middle conversation turns when the context window approaches its limit. The auxiliary model summarizes the compressed turns; the first and last turns are preserved verbatim. See [session persistence](../concepts/session-persistence.md).

**Context files** — Files injected into every conversation's system prompt. Used to give Hermes background about a project, codebase, or ongoing work. Managed with `/context add` and `/context list`. Stored as references in `~/.hermes/`.

**Credential pool** — The multi-provider credential manager in `agent/credential_pool.py`. Holds API keys for all configured LLM providers and selects the right one based on the active provider setting.

**Daytona** — A workspace-as-code platform that Hermes can use as a terminal execution backend. Like Modal, it provides isolated cloud environments that persist between sessions.

**Delegate tool** — The `delegate` tool that spawns an isolated child `AIAgent` to handle a subtask in parallel. The child agent has RPC access to the parent's tools.

**Environment (terminal backend)** — The execution environment where terminal commands run. Options: `local`, `docker`, `ssh`, `modal`, `daytona`, `singularity`. Configured in `config.yaml` under `terminal.backend`. See [terminal execution](../concepts/terminal-execution.md).

**FTS5** — Full-Text Search 5, a SQLite extension used for full-text search over past conversations. Powers the `/search` command and session recall. See [session persistence](../concepts/session-persistence.md).

**Gateway** — The subsystem that connects Hermes to messaging platforms (Telegram, Discord, Slack, WhatsApp, Signal, Email, Matrix, etc.). Runs as a long-lived process with `hermes gateway start`. See [messaging gateway](../concepts/gateway.md).

**HERMES_HOME** — The directory where Hermes stores all user data: config, sessions, memory, skills, logs. Defaults to `~/.hermes`. Override with the `HERMES_HOME` environment variable for multi-instance profiles.

**Honcho** — An open-source dialectic user modeling library by Plastic Labs. When configured, it builds a persistent, inference-based model of the user across sessions. Optional memory backend.

**Hub skill** — A community-contributed skill installable from [agentskills.io](https://agentskills.io) via `hermes skills install`. Distinct from bundled and optional skills.

**Injection (ephemeral)** — System prompts in Hermes are never stored in the conversation history — they are rebuilt from scratch at the start of each LLM call. This preserves prompt caching and prevents stale context accumulation. See [ADR 004](../architecture/adr/004-ephemeral-system-prompts.md).

**MCP (Model Context Protocol)** — A protocol for connecting external tool servers to an LLM agent. Hermes can act as both an MCP client (connecting to external servers) and server (exposing its tools). Implemented in `tools/mcp_tool.py` and `mcp_serve.py`.

**Memory** — Persistent facts about the user and their context, stored across sessions. Two files: `MEMORY.md` (factual knowledge) and `USER.md` (user profile). Multiple backends available (static files, Honcho, Mem0, etc.). See [memory and learning](../concepts/memory-and-learning.md).

**Mem0** — An optional memory platform integration. Stores memories in Mem0's cloud infrastructure instead of local files.

**Modal** — A serverless cloud execution platform. When Hermes uses the Modal terminal backend, commands run in cloud sandboxes that hibernate when idle and wake on demand.

**Optional skills** — Official skills that ship in `optional-skills/` but are not active by default. They require explicit enablement.

**Platform adapter** — The code that connects one messaging platform (e.g., Telegram) to the gateway's internal message routing. Each adapter implements `BasePlatform`. Found in `gateway/platforms/`.

**Profile** — A separate Hermes instance with its own `HERMES_HOME`, config, sessions, and credentials. Useful for running multiple bot personas or isolated environments on the same machine.

**Prompt caching** — The Anthropic API feature that caches the static portion of the system prompt across turns. Hermes explicitly marks cacheable sections to reduce cost and latency on long conversations. Managed in `agent/prompt_caching.py`.

**Provider** — An LLM API endpoint. Hermes supports: OpenRouter, Nous Portal, Anthropic (direct), OpenAI, Gemini (Google AI Studio), z.ai/GLM, Kimi/Moonshot, MiniMax, Hugging Face, OpenCode Zen/Go, and any custom OpenAI-compatible endpoint.

**Registry (tool registry)** — The central dictionary of all tools, their JSON schemas, handlers, and availability functions. Lives in `tools/registry.py`. Tools self-register at import time. See [tool system](../concepts/tool-system.md).

**Session** — One complete conversation, from the first message to the last response in a contiguous interaction. Sessions are stored in SQLite with a generated title, timestamp, and searchable transcript.

**SessionDB** — The SQLite database class in `hermes_state.py` that stores all sessions with FTS5 indexing. Location: `~/.hermes/state.db`.

**Skill** — A unit of procedural memory: a directory containing `SKILL.md` (instructions/examples) and optional scripts. Skills are injected into the system prompt, giving Hermes context-specific knowledge and behaviors. See [skill system](../concepts/skill-system.md).

**Slash command** — A command beginning with `/` that changes agent behavior or invokes a function. Examples: `/new`, `/model`, `/compress`, `/skills`. The full list is in `hermes_cli/commands.py`.

**Smart model routing** — An optional feature that uses a cheaper model for short, simple turns while routing complex turns to the main model. Configured in `config.yaml` under `smart_model_routing`.

**SOUL.md** — The persona/identity file at `~/.hermes/SOUL.md`. Injected at the top of every system prompt. Defines Hermes's name, personality, and operating style. Customizable.

**Subagent** — A child `AIAgent` instance spawned by the `delegate` tool to handle a parallel workstream. Isolated from the parent's conversation history but shares tool access via RPC.

**Toolset** — A named group of tools (e.g., `web`, `terminal`, `file`, `browser`). Toolsets allow per-platform tool enabling/disabling. For example, the Telegram platform might disable the `terminal` toolset for security.

**Trajectory** — A recorded conversation in JSON format, used for debugging, replay, and RL training data generation. Saved to `~/.hermes/logs/session_YYYYMMDD_HHMMSS_UUID.json`.

**Worktree** — When the `--worktree` / `-w` flag is used, Hermes creates an isolated git worktree for the session so multiple agents can work on the same repo concurrently without file collisions.
