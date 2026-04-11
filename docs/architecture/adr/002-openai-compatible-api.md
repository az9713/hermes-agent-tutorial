# ADR 002: OpenAI-compatible API as the universal provider abstraction

**Status:** Accepted

## Context

Hermes supports many LLM providers: OpenRouter (200+ models), Anthropic direct, Google Gemini, Qwen, GLM, Kimi, MiniMax, Hugging Face, and custom local endpoints. Each provider originally had its own SDK and request format. We needed a single abstraction that works with all of them.

The OpenAI Python SDK supports a `base_url` parameter, and virtually every LLM provider now offers an OpenAI-compatible endpoint. This created an obvious candidate.

## Decision

All LLM calls go through the OpenAI Python SDK with a configurable `base_url`. Provider-specific behavior is handled by mapping config values to the correct `base_url`. The SDK's message format, tool calling schema, and streaming protocol are used universally.

## Alternatives considered

### Option A: Per-provider SDK adapters

Write an adapter class for each provider that translates our internal format to their SDK:

```python
class AnthropicAdapter:
    def chat(self, messages, tools): ...

class GeminiAdapter:
    def chat(self, messages, tools): ...
```

**Pros:** Access to provider-specific features (Anthropic's extended thinking, Gemini's code execution sandbox, etc.).
**Cons:** Every new provider requires a new adapter class. Maintenance scales with the number of providers. Feature parity across adapters is hard to maintain.

### Option B: LiteLLM or similar unified library

Use a third-party library that abstracts provider differences.

**Pros:** We don't maintain the abstraction.
**Cons:** Adds a dependency with its own versioning and bugs. We'd be at the mercy of LiteLLM's abstractions and release cadence.

### Option C: OpenAI SDK with base_url (chosen)

```python
client = openai.AsyncOpenAI(
    api_key=api_key,
    base_url="https://openrouter.ai/api/v1",  # or any compatible endpoint
)
response = await client.chat.completions.create(
    model=model_name,
    messages=messages,
    tools=tool_schemas,
    stream=True,
)
```

Change provider: change `base_url` and `api_key`. No code changes.

**Pros:** Zero per-provider code. Switching providers is a config change. Any new provider that supports the OpenAI format works immediately.
**Cons:** Lose access to provider-specific features not in the OpenAI API format.

## Rationale

The provider ecosystem has standardized on OpenAI compatibility. Anthropic, Google, Mistral, and virtually every new provider offer an OpenAI-compatible endpoint. The tradeoff — losing provider-specific features — is acceptable because:

1. The core features (streaming chat completions with tool calling) are in the OpenAI format.
2. Provider-specific features (extended thinking, code execution sandboxes) are available through OpenRouter's model-specific parameters, which can be passed as extra kwargs.
3. Provider lock-in is a significant risk. The OpenAI layer keeps Hermes portable.

For the one provider that needed special handling (Anthropic's native prompt caching headers), we added a thin adapter in `agent/anthropic_adapter.py` that wraps the OpenAI client and injects the cache-control headers.

## Trade-offs

- **What we gave up:** First-class access to provider-specific APIs (Anthropic artifacts, Google code execution, etc.)
- **What we accepted:** All providers are second-class citizens of their own API — you use them via an OpenAI compatibility layer.
- **What this makes harder:** Taking advantage of genuinely unique provider features.

## Consequences

- New provider support: usually just a new `base_url` entry in the provider config — no code.
- All providers behave identically from the agent loop's perspective.
- `agent/anthropic_adapter.py` is the exception: Anthropic native API (for prompt caching headers) with an OpenAI-compatible wrapper on top.
