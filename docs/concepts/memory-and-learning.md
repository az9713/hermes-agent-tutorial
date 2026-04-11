# Memory and learning

Hermes accumulates knowledge across sessions: factual memory about you and your environment, a model of your preferences, and a growing library of procedural skills.

## The three layers of memory

### Layer 1: MEMORY.md (factual knowledge)

`~/.hermes/memories/MEMORY.md` stores facts Hermes has learned about you and your context:

```markdown
- You prefer tabs over spaces in TypeScript projects
- Your main dev machine is Ubuntu 22.04
- The production database is PostgreSQL 15 at db.example.com
- Your GitHub username is jsmith
```

This file is injected into the system prompt on every turn. Hermes is instructed to add entries when it learns something worth remembering, and to ask before adding sensitive information.

### Layer 2: USER.md (user profile)

`~/.hermes/memories/USER.md` contains a richer model of who you are: your role, communication style, working preferences, and goals. Hermes builds this over time, especially if Honcho dialectic modeling is enabled.

### Layer 3: Skills (procedural memory)

Skills in `~/.hermes/skills/` capture *how to do things* — workflows, procedures, tool usage patterns, API quirks. See [skill system](skill-system.md) for a full description.

---

## How Hermes updates its memory

Hermes uses a "memory nudge" mechanism: at the end of certain conversations, the system prompt instructs it to consider whether anything worth remembering was discussed. If so, it appends to `MEMORY.md`.

You can also directly instruct Hermes:

```
you: remember that the deploy script is at scripts/deploy.sh and needs PROD_KEY set
hermes: [appends to MEMORY.md]
```

To see current memory:

```bash
hermes memory show
```

To clear memory:

```bash
hermes memory clear
```

## Memory backends

Hermes supports multiple memory backends via a plugin architecture in `plugins/memory/` and `agent/memory_provider.py`.

| Backend | Description | Requires |
|---------|-------------|---------|
| **Local files** (default) | `MEMORY.md` and `USER.md` on disk | Nothing |
| **Honcho** | Dialectic user modeling by Plastic Labs | `HONCHO_API_KEY` + `~/.honcho/config.json` |
| **Mem0** | Cloud memory platform | `MEM0_API_KEY` |
| **OpenViking** | Alternative memory platform | `OPENVIKING_API_KEY` |
| **RetainDB** | Memory database | `RETAINDB_API_KEY` |
| **SuperMemory** | Memory platform | `SUPERMEMORY_API_KEY` |
| **Byterover** | Memory platform | `BYTEROVER_API_KEY` |
| **Hindsight** | Memory platform | `HINDSIGHT_API_KEY` |
| **Holographic** | Memory platform | `HOLOGRAPHIC_API_KEY` |

To configure a non-default backend, see [configure memory](../guides/configure-memory.md).

## Honcho dialectic user modeling

Honcho (by Plastic Labs) is the most sophisticated memory backend. Rather than storing raw facts, it builds an *inference-based* model of the user — tracking beliefs, preferences, and context over time, resolving contradictions, and making predictions.

Honcho operates as a background process that receives signals from conversations and maintains its own internal representation. Hermes queries it at the start of each conversation to get the current best model of the user.

To enable Honcho:

1. Get an API key at [app.honcho.dev](https://app.honcho.dev)
2. Set `HONCHO_API_KEY` in `~/.hermes/.env`
3. Create `~/.honcho/config.json` with `{"enabled": true}`

## Session search

Hermes can search its own past conversations. The `search_sessions` tool uses SQLite FTS5 to find relevant turns from previous sessions:

```
you: what was that nginx config we set up last month?
hermes: [searches sessions for "nginx config", finds the relevant turn, summarizes it]
```

You can also search manually:

```bash
hermes search "nginx config"
/search nginx config    # from inside a conversation
```

## The learning loop

The full learning cycle:

1. **Experience** — Hermes completes a complex task (writes code, debugs an issue, sets up a service).
2. **Memory update** — Relevant facts are appended to `MEMORY.md` (`remember that the staging DB is at...`).
3. **Skill creation** — If the task involved a novel procedure, Hermes may create a skill (`~/.hermes/skills/my-nginx-setup/`).
4. **Future reuse** — On the next related conversation, both the memory and the skill are in the system prompt. The task goes faster.
5. **Skill improvement** — If the skill's instructions were slightly wrong or incomplete, Hermes updates `SKILL.md` after figuring out the right approach.

This loop is why Hermes compounds in value over time — each session is an investment in future efficiency.

## What to keep in MEMORY.md

Good entries:
- Infrastructure details (server IPs, database connections, service names)
- Tool preferences (which CLI tools you like, code style preferences)
- Ongoing project context (current sprint goals, known blockers)
- Personal preferences (communication style, how much detail you want)

What NOT to put in MEMORY.md:
- Passwords or secrets (use `~/.hermes/.env` for those)
- Temporary facts that will be stale in a week
- Information that's already in the codebase or docs

## Privacy notes

Memory files are stored locally in `~/.hermes/memories/` and are never sent to Nous Research. They are sent to the LLM provider (OpenRouter, Anthropic, etc.) as part of the system prompt on every turn.

If you use cloud memory backends (Honcho, Mem0, etc.), data is sent to those providers. Review their privacy policies.
