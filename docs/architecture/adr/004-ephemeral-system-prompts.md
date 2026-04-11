# ADR 004: Ephemeral system prompts (rebuilt every turn)

**Status:** Accepted

## Context

The system prompt is the static context injected before every LLM call: identity (SOUL.md), tool schemas, active skills, memory files, and context files. There are two ways to handle this:

1. **Persistent injection**: Store the system prompt in the message list once at conversation start. Subsequent turns include it as the first message.
2. **Ephemeral injection**: Rebuild the system prompt on every API call, pass it separately, and never store it in the message list.

## Decision

System prompts are ephemeral: rebuilt fresh on every LLM API call by `agent/prompt_builder.py` and never stored in the `messages` list. The API call signature passes `system` (or the first-message position for OpenAI format) separately from the conversation history.

## Alternatives considered

### Option A: Persistent injection (store system prompt in messages)

At conversation start, insert the system prompt as the first message. Include it in every API call as part of the normal message history.

**Pros:** Simple — the message list is self-contained and portable.
**Cons:**
1. **No prompt caching benefit.** Anthropic's prompt caching works on the system prompt specifically because it's outside the message list. Storing it inside the message list means it gets re-hashed every time the conversation grows, eliminating cache hits.
2. **Stale context.** If a skill is updated or memory changes mid-session, the stored system prompt doesn't reflect the change until the session restarts.
3. **Context bloat.** The system prompt (often 20,000+ tokens) counts against the context window as a conversation message.

### Option B: Ephemeral injection (chosen)

Rebuild the system prompt on every call. The message list contains only the actual conversation turns (user + assistant + tool). The system prompt is a separate parameter.

**Pros:**
1. **Prompt caching works.** The system prompt is the same "document" on every call — stable content in the stable position the caching mechanism requires. Cache hit rates are high, reducing cost and latency significantly on long conversations.
2. **Always current.** Changes to MEMORY.md, skill files, or context files take effect on the next turn without restarting.
3. **Context window accuracy.** The context window budget is measured against actual conversation content, not bloated by a re-injected system prompt.

**Cons:** System prompt must be rebuilt on every call. For a conversation with 100 turns, the system prompt is assembled 100 times. This is fast (filesystem reads + string assembly, typically <10ms) but not zero-cost.

## Rationale

Anthropic prompt caching is the decisive factor. For long conversations with expensive models, caching the system prompt (which is often larger than the actual conversation history at conversation start) reduces API costs by 60–90%. This is not a marginal optimization — it's a core economic feature of running Hermes on high-quality models.

The "always current" property is a bonus that turns out to be significant: users expect that when they update their MEMORY.md or install a new skill, the change takes effect immediately. With persistent injection, they'd have to start a new session.

The assembly cost (reading skill files, formatting memory) is genuinely fast. Skill files are small Markdown documents. Memory files are typically a few KB. The bottleneck is always the LLM API call, not the prompt assembly.

## Trade-offs

- **What we gave up:** Message list self-containedness. A saved message list doesn't contain the system prompt, so you can't fully reconstruct a session from the message list alone.
- **What we accepted:** The system prompt is stateful but not in the message list. This is a conceptual mismatch with some LLM libraries that expect the system prompt to be the first message.
- **What this makes harder:** Debugging sessions using only the raw message list — you'd need to also reconstruct the system prompt from the files at the time.

## Consequences

- Prompt caching is enabled by default for Anthropic models via `agent/prompt_caching.py`.
- Memory and skill updates take effect immediately without restarting a conversation.
- Trajectory logs include the reconstructed system prompt for each turn (for accurate replay and training data).
- The `agent/prompt_builder.py` must handle all edge cases: missing SOUL.md, empty memory files, platform-filtered skills.
