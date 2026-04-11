# Deep Dive: Hermes Agent's Self-Improvement Claims

> **This is a unique contribution of this fork.** What follows is a careful, code-level analysis of every self-improvement claim made by Hermes Agent. For each claim we locate the implementing code, explain how it works, identify the limitations, and suggest concrete improvements.

---

## The Claim

From the README:

> "The self-improving AI agent built by Nous Research. It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions."

Five claims:
1. Creates skills from experience
2. Improves skills during use
3. Nudges itself to persist knowledge
4. Searches past conversations
5. Builds a deepening model of who you are

---

## The Architectural Reality

Before diving into each claim, the key finding: **all five behaviors reduce to prompt instructions.** Every "self-improvement" feature is a string in `agent/prompt_builder.py` that tells the LLM to call a tool. There is no out-of-band learning process, no background consolidation, no outcome measurement, and no gradient updates. Whether improvement happens depends entirely on whether the LLM decides to call the tool.

This is not a criticism of the design — it's a pragmatic choice that works well in practice. But understanding it accurately is important for knowing what you're building on.

---

## Claim 1: Creates Skills from Experience

### The Code

**`agent/prompt_builder.py`** — `SKILLS_GUIDANCE` constant:

```python
SKILLS_GUIDANCE = """
After completing a complex task (5+ tool calls), fixing a tricky error, or discovering
a non-trivial workflow, save the approach as a skill using skill_manage(action="create", ...).
...
"""
```

**`tools/skill_manager_tool.py`** — `_create_skill()`:

```python
def _create_skill(name: str, content: str, ...) -> str:
    skill_dir = _get_skill_dir(name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    _atomic_write_text(skill_file, content)
    _security_scan(skill_file)          # regex scan for dangerous patterns
    clear_skills_system_prompt_cache(clear_snapshot=True)
    return f"Skill '{name}' created successfully."
```

**What "experience" means here:** A conversation turn in which the LLM happened to call 5+ tools and the SKILLS_GUIDANCE prompt caused it to also call `skill_manage(action="create")`. There is no observation of outcomes — the skill is created based on the *process* of the task, not whether the task succeeded or produced good results.

### How It Works

1. On every turn, `prompt_builder.py` injects `SKILLS_GUIDANCE` into the system prompt.
2. After a complex turn, the LLM decides (probabilistically) to call `skill_manage(action="create", name="...", content="...")`.
3. The tool writes `~/.hermes/skills/<name>/SKILL.md` atomically.
4. The skill cache is invalidated; on the next turn, the new skill appears in the system prompt.
5. Future conversations will see the skill as a slash command and inject its content into context.

### Critique

**What works well:**
- The atomic write + security scan + cache invalidation pipeline is solid.
- Skills are persistent Markdown files — human-readable, versionable, editable.
- The security scanner (`tools/skills_guard.py`) blocks prompt injection patterns before they become persistent.

**What's missing:**
- **No success signal.** The LLM creates skills based on task complexity (tool call count), not task outcome. A skill can be created from a failed or partially-correct workflow.
- **No deduplication.** If the LLM solves the same problem twice, it may create two similar skills with slightly different names.
- **No quality gate.** Skills are never evaluated after creation. A created skill stays forever unless manually deleted.
- **No versioning.** Overwriting a skill loses the previous version. There's no git history of skill evolution.
- **LLM volition is the only trigger.** If the model doesn't feel like creating a skill on a given turn, none is created, even for highly reusable workflows.

### Suggested Improvements

1. **Outcome-gated creation:** After task completion, evaluate whether the final output was accepted (user didn't retry, no error correction). Only create a skill if the workflow succeeded.

2. **Post-hoc analysis pass:** Add a background job (`hermes cron`) that reviews recent sessions nightly, identifies recurring patterns across multiple sessions, and proposes skill creation to the user.

3. **Skill versioning:** Store skill history in SQLite (linked to `state.db`). Before overwriting a skill, snapshot the previous version.

4. **Deduplication check:** Before creating a skill, run an embedding similarity check against existing skills and warn if a similar one already exists.

5. **Skill quality scoring:** After a skill is used, log whether it was helpful (did the user accept the result?) and surface low-scoring skills for review.

---

## Claim 2: Improves Skills During Use

### The Code

**`agent/prompt_builder.py`** — within `SKILLS_GUIDANCE`:

```python
"""
...If you discover a better approach while using a skill, update it with
skill_manage(action="edit", ...) or patch specific sections with
skill_manage(action="patch", ...).
"""
```

**`tools/skill_manager_tool.py`** — `_patch_skill()`:

```python
def _patch_skill(name: str, old_text: str, new_text: str) -> str:
    skill_file = _get_skill_dir(name) / "SKILL.md"
    content = skill_file.read_text()
    # Fuzzy match: find best matching block for old_text
    match = _fuzzy_find_block(content, old_text, threshold=0.6)
    if match:
        content = content[:match.start] + new_text + content[match.end:]
        _atomic_write_text(skill_file, content)
        _security_scan(skill_file)
        clear_skills_system_prompt_cache(clear_snapshot=True)
        return f"Skill '{name}' patched successfully."
    raise ToolError(f"Could not find matching block in skill '{name}'")
```

### How It Works

"Improvement during use" means: while executing a skill in a conversation, if the LLM notices the skill's instructions are wrong or incomplete, it calls `skill_manage(action="patch")` to update the skill in-place. The patch takes effect on the *next turn* (because the system prompt is rebuilt ephemerally). The improvement is not visible in the current turn.

### Critique

**What works well:**
- The fuzzy match in `_patch_skill()` is a nice detail — the LLM doesn't need to reproduce the exact original text.
- Atomic writes ensure no corruption if the process dies mid-write.
- Cache invalidation ensures the improved skill propagates immediately.

**What's missing:**
- **No "improvement" signal.** The LLM patches a skill when it believes the existing instructions are wrong. It has no mechanism to verify this belief — it's purely the model's in-context judgment.
- **Patches don't require evidence.** The model can patch a skill that has been working fine, potentially making it worse.
- **No patch history.** Overwrite is destructive. If a patch makes a skill worse, there's no rollback.
- **"During use" is misleading.** The improvement takes effect on the *next* conversation turn, not during the current execution of the skill. The current turn still runs on the old skill.
- **No cross-session improvement signal.** If the same skill fails in three different sessions, there's no mechanism to aggregate that feedback and trigger a patch.

### Suggested Improvements

1. **Diff-based patch logging:** Every patch should append to a `SKILL_HISTORY.md` file alongside `SKILL.md`, recording: timestamp, old block, new block, conversation context.

2. **Rollback command:** `hermes skills rollback <name>` to restore the previous version.

3. **Evidence-required patches:** Require the LLM to include a `reason` field in the patch schema, and log it alongside the change.

4. **Cross-session signal:** When session_search finds multiple instances of the same skill being corrected mid-use, surface a summary to the user: "Skill X has been patched 5 times. Consider reviewing it."

5. **Staged patches:** For skills used frequently, require a two-step process: propose a patch (write to `SKILL.md.proposed`), then apply after the user confirms.

---

## Claim 3: Nudges Itself to Persist Knowledge

### The Code

**`agent/prompt_builder.py`** — `MEMORY_GUIDANCE`:

```python
MEMORY_GUIDANCE = """
You have access to persistent memory. Use memory(action="add") to save important
facts the user tells you (name, preferences, goals, constraints). Use memory(action="add")
with category="user" for personal details. Save facts that will be useful in future
sessions. Avoid saving transient facts.
...
"""
```

The "nudge" is this constant being injected into every system prompt. There is no periodic timer, no background process, no automatic trigger — the LLM is simply always-reminded to use memory.

**`tools/memory_tool.py`** — `MemoryStore`:

```python
class MemoryStore:
    memory_char_limit = 2200    # MEMORY.md budget
    user_char_limit = 1375      # USER.md budget

    def load_from_disk(self):
        # Called once at session start
        self._system_prompt_snapshot = self._read_files()  # FROZEN

    def add(self, entry: str, category: str = "general") -> str:
        # Writes to disk immediately
        # But _system_prompt_snapshot is NOT updated
        ...
```

### How It Works

"Nudging" = the MEMORY_GUIDANCE string is present in every system prompt. The LLM is persistently reminded to call `memory(action="add")` when it encounters memorable facts. When it does, `memory_tool.py` writes the fact to `~/.hermes/MEMORY.md` or `~/.hermes/USER.md` immediately. **However, the system prompt snapshot for the current session is frozen at session start — the new memory entry only appears in the next session's system prompt.**

### Critique

**What works well:**
- The persistent reminder pattern actually works — LLMs reliably call memory tools when instructed.
- The char limits (2200 + 1375) prevent memory bloat.
- Writing to disk immediately means the memory survives even if the session crashes.

**What's missing:**
- **Mid-session blindness.** If the user reveals important context at turn 5, and at turn 15 asks a related question, Hermes won't have that context in its system prompt. The frozen snapshot means new memories only help in *future* sessions.
- **No consolidation.** Memory is additive — entries are added but never merged or reorganized. Over time, MEMORY.md becomes a list of disconnected facts rather than a coherent user model.
- **Char limits cause silent truncation.** When MEMORY.md exceeds 2200 chars, new entries may overwrite old ones with no user notification.
- **No importance weighting.** "User prefers dark mode" and "User is presenting to the board next Thursday" get the same treatment.
- **The "nudge" is a prompt, not a schedule.** True self-nudging would be a background process that reviews sessions and identifies what should have been remembered but wasn't.

### Suggested Improvements

1. **Intra-session memory visibility:** After each `memory(action="add")` call, update the in-session `_system_prompt_snapshot` so subsequent turns in the same conversation have access to newly saved facts.

2. **Memory consolidation cron:** A nightly job that reads all recent session summaries and asks the LLM: "What facts from these sessions should be added to or updated in MEMORY.md?"

3. **Importance tiers:** Add a `priority` field (high/normal/ephemeral) to memory entries. Ephemeral entries auto-expire after N days. High-priority entries are never truncated.

4. **Memory audit report:** `hermes memory audit` — shows entries, their age, how many times they've been referenced, and which are candidates for removal.

5. **Structured user model:** Instead of freeform MEMORY.md, use a schema (name, preferences[], constraints[], expertise[], current_projects[]) and update fields in place rather than appending.

---

## Claim 4: Searches Past Conversations

### The Code

**`tools/session_search_tool.py`** — `session_search()`:

```python
async def session_search(query: str = "", limit: int = 5) -> str:
    if not query:
        # Recent mode: return metadata of last N sessions
        return _format_recent_sessions(limit)

    # Keyword mode: FTS5 search → LLM summarization
    rows = db.execute(
        "SELECT session_id, content FROM sessions_fts WHERE sessions_fts MATCH ? LIMIT ?",
        (query, limit * 3)  # fetch 3x for re-ranking
    ).fetchall()

    # Resolve delegation chains to parent sessions
    rows = [_resolve_to_parent(row) for row in rows]

    # Truncate each session around the match positions
    truncated = [_truncate_around_matches(row, query, window=100_000) for row in rows]

    # Parallel LLM summarization (Gemini Flash)
    summaries = await asyncio.gather(*[_summarize_session(s) for s in truncated[:limit]])
    return _format_results(summaries)
```

**`hermes_state.py`** — SQLite schema:

```sql
CREATE VIRTUAL TABLE sessions_fts USING fts5(
    session_id UNINDEXED,
    content,
    tokenize='porter ascii'
);
```

### How It Works

1. Sessions are stored in `~/.hermes/state.db` using SQLite FTS5 with Porter stemming.
2. `session_search` is available as a tool in the LLM's toolkit.
3. The `SESSION_SEARCH_GUIDANCE` prompt instructs the LLM to call `session_search` when the user references past events ("last time we did X", "remember when...").
4. Results are truncated around match positions (100k-char window) and sent to Gemini Flash for summarization.
5. Summaries (5-point structured format) are returned to the main LLM as tool results.

### Critique

**What works well:**
- FTS5 with Porter stemming handles stemming/morphology correctly (searching "configure" finds "configured", "configuring").
- The delegation chain resolver (`_resolve_to_parent`) correctly surfaces user-facing context rather than tool-call internals.
- Parallel summarization using asyncio.gather is efficient — 5 sessions summarized in parallel.
- The `_truncate_around_matches` function is thoughtful: it centers the context window around the actual query match, not just the beginning of the session.

**What's missing:**
- **Reactive only.** Session search is never called proactively. If the user asks a question that was answered 3 sessions ago, Hermes won't know to look unless the LLM is explicitly triggered by phrasing like "last time."
- **Keyword-only search.** FTS5 is keyword/phrase search — it has no semantic understanding. Searching "slow API calls" won't find a session where you diagnosed "high latency HTTP requests."
- **No automatic cross-session context injection.** The search tool exists, but there's no background process that proactively scans recent sessions before each conversation starts and injects relevant context.
- **Gemini Flash dependency.** Summarization uses Gemini Flash specifically — if that key isn't configured, session search degrades to metadata-only mode without a clear error message.
- **5-session limit.** The summarization limit is hardcoded at 5. For users with thousands of sessions, relevant sessions beyond position 5 in the FTS5 ranking are never surfaced.

### Suggested Improvements

1. **Semantic search layer:** Add an optional embedding-based search pass using a local model (e.g., nomic-embed-text via Ollama). Combine BM25 (FTS5) and semantic scores for re-ranking.

2. **Proactive context injection:** At conversation start, automatically run a lightweight search for the last 3 sessions' topics and inject a summary. This gives Hermes continuity without the user needing to ask.

3. **Provider-agnostic summarization:** Summarization should use the same provider/model configured for the main conversation, not a hardcoded Gemini Flash call.

4. **Episodic memory graph:** Build a lightweight graph of recurring entities (projects, people, tools) across sessions. At conversation start, retrieve the N most relevant entity summaries.

5. **Cross-session deduplication:** When the same question appears in multiple sessions, merge the answers into a single canonical entry in MEMORY.md rather than storing N separate sessions.

---

## Claim 5: Builds a Deepening Model of Who You Are

### The Code

There are two mechanisms:

**Default — MEMORY.md + USER.md (freeform files):**

From `tools/memory_tool.py`:

```python
# USER.md stores personal/preference facts
# MEMORY.md stores general task/project facts
# Both are char-limited freeform text files
# Updated by LLM calling memory(action="add", category="user")
```

**Optional — Honcho dialectic modeling:**

From `docs/concepts/memory-and-learning.md` and `docs/guides/configure-memory.md`:

```python
# ~/.honcho/config.json must exist with {"enabled": true}
# Honcho (by Plastic Labs) maintains an inference-based user model
# Updated via API calls when configured
```

### How It Works

**Default path:** The "deepening model" is `~/.hermes/USER.md` — a plain text file with char limit 1375. The LLM is instructed via `MEMORY_GUIDANCE` to call `memory(action="add", category="user")` when it learns something about the user. There is no inference, no model updates, no probabilistic reasoning — it's a text file that grows until it hits the char limit.

**Honcho path (opt-in):** Honcho uses a separate backend that performs inference-based modeling — it observes conversations and infers preferences, communication styles, and goals without the user explicitly stating them. This is the genuinely interesting part, but it's disabled by default and requires separate installation.

### Critique

**Default path:**

- **"Deepening model" overstates it.** `USER.md` is a static notes file, not a model. There's no inference, no updating of existing beliefs, no contradiction detection.
- **Char limit creates a ceiling.** The model stops deepening at 1375 characters.
- **No structured schema.** Facts are stored as freeform text. There's no representation of confidence, recency, or relationship between facts.
- **No proactive inference.** A true user model would infer unstated preferences from behavior (e.g., "user always adds type hints to Python → strong typing preference"). The default path only records what the user explicitly states.

**Honcho path:**

- Honcho is genuinely doing dialectic modeling — much closer to the claim.
- But it's opt-in, requires a separate API key, and is not the default experience.
- The claim in the README describes it as a universal feature, not an opt-in enhancement.

### Suggested Improvements

1. **Behavioral inference without Honcho:** Add a lightweight in-process analyzer that reads recent sessions and infers preferences from behavior patterns. Examples:
   - Language preference: count which languages appear in code blocks
   - Tool preference: track which commands the user accepts vs. retries
   - Communication style: count message lengths, question frequency

2. **Structured user model schema:**
   ```yaml
   # ~/.hermes/user_model.yaml
   name: Alice
   expertise:
     python: advanced
     kubernetes: intermediate
   preferences:
     code_style: type_hints
     verbosity: concise
   projects:
     - name: payment-api
       last_active: 2024-01-15
   ```

3. **Contradiction detection:** When a new `memory(action="add", category="user")` call contradicts an existing entry, flag it and ask the user which is current.

4. **Honcho as default:** Make Honcho the default user modeling backend (with local fallback), and surface its insights directly in the session start context.

5. **Periodic user model review:** Monthly (via cron), generate a "user model report" that summarizes what Hermes has inferred and asks the user to correct any outdated entries.

---

## Summary Assessment

| Claim | Reality | Rating |
|-------|---------|--------|
| Creates skills from experience | LLM calls `skill_manage` tool when prompted | Partial ✓ |
| Improves skills during use | LLM calls `skill_manage(action="patch")` when prompted | Partial ✓ |
| Nudges itself | `MEMORY_GUIDANCE` string in every system prompt | Weak ✓ |
| Searches past conversations | FTS5 search + Gemini Flash summarization | Strong ✓ |
| Builds a deepening user model | Freeform text file; Honcho is opt-in | Weak ✓ |

**The common pattern across all five features:** A prompt string instructs the LLM to call a tool. The tool writes to disk. The disk state is reloaded in future sessions. This is a coherent, working design — but it's better described as "LLM-driven self-annotation" than "a built-in learning loop."

**The gap that would most improve the system:** A background scheduled process (not dependent on the LLM's in-context decision to act) that reviews sessions, identifies patterns, and proposes or applies improvements. This would transform the current reactive annotation system into a genuine learning loop.

---

## Code References

| Component | File | Key Function |
|-----------|------|--------------|
| Skill creation prompt | `agent/prompt_builder.py` | `SKILLS_GUIDANCE` |
| Skill creation tool | `tools/skill_manager_tool.py` | `_create_skill()` |
| Skill patching tool | `tools/skill_manager_tool.py` | `_patch_skill()` |
| Memory persistence | `tools/memory_tool.py` | `MemoryStore.add()` |
| Memory prompt | `agent/prompt_builder.py` | `MEMORY_GUIDANCE` |
| Session FTS5 storage | `hermes_state.py` | `sessions_fts` table |
| Session search | `tools/session_search_tool.py` | `session_search()` |
| Search prompt | `agent/prompt_builder.py` | `SESSION_SEARCH_GUIDANCE` |
| Context compression | `agent/context_compressor.py` | `ContextCompressor` |
| User model (default) | `~/.hermes/USER.md` | freeform text |
| User model (opt-in) | Honcho integration | `HONCHO_APP_ID` config |
