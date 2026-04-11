# The agent loop

The agent loop is the core execution cycle in Hermes: it takes a user message and produces a response, dispatching tool calls along the way.

## What it is

The agent loop is an async loop that repeatedly calls the LLM until the model stops requesting tool calls. It lives in `run_agent.py` in the `AIAgent` class, primarily in `_run_agent_loop()` and `run_conversation()`.

## Why it exists as its own subsystem

The agent loop is the bridge between static LLM calls (which just return text) and dynamic tool execution (which produces real side effects and new information). Without this loop, the model could only suggest actions — not take them.

## How it works

```
User message arrives
       │
       ▼
Build system prompt
  ├── SOUL.md (identity)
  ├── Active skills (procedural memory)
  ├── Context files (project background)
  ├── MEMORY.md + USER.md
  └── Tool schemas (JSON)
       │
       ▼
Call LLM (streaming)
       │
       ├── Response has tool_calls?
       │         │
       │         ▼
       │   Dispatch each tool call
       │   via registry.dispatch()
       │         │
       │         ▼
       │   Collect tool results
       │         │
       │         └──► Append to messages, loop back to LLM
       │
       └── Response is text only?
                 │
                 ▼
           Deliver to user
           Save to SessionDB
           Check for context compression
```

## Key data structures

**Message list** — The conversation history passed to the LLM on every call. Format matches the OpenAI messages API:

```python
[
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool",      "content": "...", "tool_call_id": "..."},
    ...
]
```

The system prompt is prepended before each API call but is never stored in the message list (see [ADR 004](../architecture/adr/004-ephemeral-system-prompts.md)).

**Tool call** — A structured request from the model to invoke a tool, with a name and JSON arguments:

```json
{
    "id": "call_abc123",
    "type": "function",
    "function": {
        "name": "run_command",
        "arguments": "{\"command\": \"ls -la\"}"
    }
}
```

**Tool result** — The response from tool dispatch, added to the message list as a `tool` role message:

```json
{
    "role": "tool",
    "tool_call_id": "call_abc123",
    "content": "total 48\ndrwxr-xr-x  5 user group..."
}
```

## Prompt construction

Each API call assembles the system prompt fresh via `agent/prompt_builder.py:build_system_prompt()`. The assembled prompt includes, in order:

1. **Identity** — Contents of `~/.hermes/SOUL.md`
2. **Current date/time** — Injected so the model has temporal awareness
3. **Tool documentation** — JSON schemas for all active tools
4. **Active skills** — Markdown content of every enabled skill
5. **Context file references** — Any files added with `/context add`
6. **Memory** — Contents of `MEMORY.md` and `USER.md`
7. **Session metadata** — Working directory, profile name, active platform

The entire prompt is rebuilt on every turn. This is intentional — it means changes to skills or memory take effect immediately, and Anthropic's prompt caching can cache the stable portions. See [ADR 004](../architecture/adr/004-ephemeral-system-prompts.md).

## Context compression

When the conversation history approaches the model's context limit (default threshold: 85%), the middle turns are automatically summarized:

1. A range of "middle" messages is identified (preserving the first turn and the most recent N turns).
2. The auxiliary model (default: Gemini Flash) summarizes those middle turns.
3. The summary replaces the compressed turns in the message list.
4. The conversation continues with the freed-up context.

Compression is transparent to the user. You can trigger it manually with `/compress`. Configuration is in `config.yaml` under `compression:`.

## Token counting and budget

Before each API call, `agent/model_metadata.py` estimates the total token count. If the estimate exceeds the configured `context_length`, compression triggers before the call. After each response, token usage is accumulated in `agent/usage_pricing.py` for the `/usage` command.

## Error handling and retries

Network errors, rate limit responses (429), and transient server errors trigger retries via `agent/retry_utils.py`. Rate limits are tracked per-provider in `agent/rate_limit_tracker.py` to avoid hammering a provider that's throttling you.

## Interaction with other subsystems

| Subsystem | Interaction |
|-----------|-------------|
| [Tool system](tool-system.md) | Dispatches tool calls, receives results |
| [Skill system](skill-system.md) | Injects skill content into system prompt |
| [Memory](memory-and-learning.md) | Injects MEMORY.md and USER.md into system prompt |
| [Session persistence](session-persistence.md) | Saves every turn to SQLite |
| [Gateway](gateway.md) | Receives messages from, delivers responses to platforms |
| Context compressor | Triggered when context window fills |

## Common gotchas

**Tool calls that never resolve** — If a tool hangs (e.g., an SSH connection that drops), the loop blocks. The terminal tool has a configurable `timeout` (default: 60 seconds) that prevents indefinite blocking.

**Max iterations** — To prevent runaway loops, `AIAgent` has a configurable max iterations cap. If the model keeps calling tools without producing text, the loop terminates after this limit and reports what happened.

**Parallel tool calls** — Some models support requesting multiple tool calls in a single response. Hermes handles these by dispatching them in parallel and collecting all results before looping back.
