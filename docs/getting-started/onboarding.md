# Onboarding

## What is an "agent" anyway?

If you've used ChatGPT, you've used a stateless chatbot — it answers questions but forgets everything when you close the browser. Hermes is different: it's an *agent*, which means it can take actions (run terminal commands, search the web, write files), it persists state across sessions (memory, conversation history), and it keeps running after you close your terminal.

Think of Hermes as a capable contractor you've hired. You brief them, they work, you check in. They remember your preferences, build skills over time, and can work while you sleep.

## The four big ideas

### 1. Hermes runs on infrastructure you control

Unlike ChatGPT or Claude.ai, Hermes runs on *your* machine or a machine you designate. Your laptop, a $5 VPS, a cloud VM, or a serverless Modal sandbox. You control what it can access, what keys it holds, and where its data lives. Nothing is stored on Nous Research servers.

### 2. Hermes uses real tools

When you ask Hermes to "set up a Python project with tests," it doesn't just give you instructions — it actually runs the commands. The terminal tool executes shell commands in a configurable backend (local, Docker, SSH, or cloud). Other tools handle web search, file operations, browser automation, code execution, and more.

If Hermes can't do something with its current tools, you can add a tool (see [add a tool](../guides/add-a-tool.md)) or install a skill that teaches it a new capability.

### 3. Hermes learns from experience

Each session is saved to a searchable SQLite database. When Hermes completes a complex task, it often writes a *skill* — a Markdown file of instructions — that captures what it learned. Future conversations automatically load relevant skills. Over time, Hermes gets better at the kinds of tasks you give it.

Memory works similarly: Hermes maintains `MEMORY.md` (factual knowledge about your setup, preferences, ongoing projects) and optionally a `USER.md` profile. It nudges itself to update these files after relevant conversations.

### 4. Hermes reaches you anywhere

You're not tied to a terminal window. Run `hermes gateway start` and Hermes connects to Telegram, Discord, Slack, WhatsApp, Signal, or Email. You can send it a task from your phone while it executes commands on a cloud VM. Cross-platform conversation continuity means you can start a conversation on Telegram and continue it in the CLI.

## A realistic scenario

You're writing a Go service. Here's what a day with Hermes looks like:

**Morning, from your terminal:**
```
> Add endpoint GET /api/users/:id with proper error handling
```
Hermes reads the existing codebase, writes the handler, updates routing, adds tests, and runs the test suite — all in one turn.

**Afternoon, from Telegram:**
```
you: did the CI pass?
hermes: Yes, all 47 tests passing. I also noticed the database pool size in config.go is set to 5 — want me to increase it to 20 for production?
```

**Evening, Hermes runs a cron job you set up:**
```
hermes → Telegram: Nightly summary: 12 commits merged, 3 open PRs, test coverage at 84%. One PR has been open 5 days without review — want me to ping the author?
```

This is what a learning, persistent agent looks like in practice.

## Why does Hermes work this way?

### Why rebuild the system prompt every turn?

The system prompt (identity, skills, tools, memory) is reconstructed at the start of every LLM call rather than stored in the conversation. This is intentional: it enables Anthropic prompt caching (huge cost savings on long conversations), prevents stale context, and lets skills and memory updates take effect immediately without restarting. See [ADR 004](../architecture/adr/004-ephemeral-system-prompts.md).

### Why a self-registering tool registry?

Tools don't need to be registered anywhere manually — each tool file calls `registry.register()` at import time. This makes adding a new tool trivial (one file, no registry updates) and makes the tool list always in sync with what's actually implemented. See [ADR 001](../architecture/adr/001-tool-self-registration.md).

### Why OpenAI-compatible API as the abstraction layer?

Hermes uses the OpenAI client format to talk to every provider, regardless of who built them. This means the code never changes when you switch from OpenRouter to Gemini to a local Ollama server. The tradeoff is losing access to provider-specific features (like Anthropic's extended thinking). See [ADR 002](../architecture/adr/002-openai-compatible-api.md).

## Your learning path

1. **Run the quickstart** — [getting-started/quickstart.md](quickstart.md). Have a real conversation before reading more.
2. **Understand the agent loop** — [concepts/agent-loop.md](../concepts/agent-loop.md). This is the core of how everything works.
3. **Explore tools** — [concepts/tool-system.md](../concepts/tool-system.md). Know what tools are available and how they're organized.
4. **Set up memory** — [concepts/memory-and-learning.md](../concepts/memory-and-learning.md). This is where Hermes's value compounds over time.
5. **Try the gateway** — [concepts/gateway.md](../concepts/gateway.md). Set up Telegram so you can reach Hermes from anywhere.
6. **Read the CLI reference** — [reference/cli-commands.md](../reference/cli-commands.md). Learn the slash commands that power your workflow.
