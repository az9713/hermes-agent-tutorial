# From Critique to Implementation: Discussion Summary

> This document captures the discussion that followed the self-improvement deep-dive analysis. It summarises the improvement suggestions, the proposed implementation plan, what was scoped in and out, and the reasoning behind each decision. Nothing in this document represents code that has already been written — it is a planning record.

---

## Part 1: Improvement Suggestions (from the Deep-Dive Analysis)

The deep-dive (`self-improvement-deep-dive.md`) identified five self-improvement claims made by Hermes Agent and produced five improvement suggestions for each. All 25 suggestions are restated here for completeness.

### Claim 1 — Creates Skills from Experience

1. **Outcome-gated creation.** Only create a skill if the task actually succeeded (user accepted the result, no retries, no error corrections). Currently skills are created based on task complexity (tool call count), not outcome quality.
2. **Post-hoc analysis pass.** A background cron job reviews recent sessions nightly, identifies recurring patterns across multiple sessions, and proposes skill creation to the user — rather than relying entirely on in-conversation LLM judgment.
3. **Skill versioning.** Before overwriting a skill, snapshot the previous version so there is a history of how the skill evolved. Currently an overwrite is permanent.
4. **Deduplication check.** Before creating a skill, run an embedding similarity check against existing skills and warn if a near-duplicate already exists.
5. **Skill quality scoring.** After a skill is used, log whether it was helpful (did the user accept the result?) and surface low-scoring skills for review or deletion.

### Claim 2 — Improves Skills During Use

1. **Diff-based patch logging.** Every patch to a skill should be logged to a `SKILL_HISTORY.md` file alongside `SKILL.md`, recording: timestamp, reason for the change, the old block, and the new block.
2. **Rollback command.** `hermes skills rollback <name>` restores a skill to its previous version from the patch history.
3. **Evidence-required patches.** Extend the `skill_manage` tool schema to require a `reason` field on every patch, logged alongside the change. The LLM must justify why it is modifying the skill.
4. **Cross-session improvement signal.** When `session_search` finds multiple instances of the same skill being corrected mid-use across different sessions, surface a summary to the user: "Skill X has been patched N times. Consider reviewing it."
5. **Staged patches.** For frequently-used skills, require a two-step process: write a proposed patch to `SKILL.md.proposed`, then apply only after user confirms.

### Claim 3 — Nudges Itself to Persist Knowledge

1. **Intra-session memory visibility.** After a successful `memory(action="add")` call, update the in-session system prompt snapshot so the newly saved fact is available to subsequent turns in the same conversation. Currently the snapshot is frozen at session start — new memories only appear in the next session.
2. **Memory consolidation cron.** A nightly background job reads recent session summaries and asks the LLM: "What facts from these sessions should be added to or updated in MEMORY.md?" — rather than relying solely on the LLM's in-conversation judgment.
3. **Importance tiers.** Add a `priority` field (high / normal / ephemeral) to memory entries. High-priority entries are never truncated when the char limit is approached. Ephemeral entries auto-expire after N days.
4. **Memory audit report.** `hermes memory audit` shows all entries, their age, how many times they have been referenced, and which are candidates for removal.
5. **Structured user model.** Replace freeform `USER.md` with a schema (name, preferences[], constraints[], expertise[], current_projects[]) so fields are updated in place rather than appended, and the model can detect when information is stale.

### Claim 4 — Searches Past Conversations

1. **Semantic search layer.** Add an optional embedding-based search pass (e.g. nomic-embed-text via Ollama) alongside FTS5 keyword search. Combine BM25 and semantic scores for re-ranking, so "slow API calls" finds sessions about "high latency HTTP requests."
2. **Proactive context injection.** At conversation start, automatically run a lightweight search for the last few sessions' topics and inject a brief summary — giving Hermes continuity without the user needing to ask.
3. **Provider-agnostic summarization.** Summarization should use the same provider and model configured for the main conversation, not a hardcoded auxiliary model. *(Note: on reviewing the actual code, `auxiliary_client.py` already handles this via its resolution chain — this critique was inaccurate about the current implementation.)*
4. **Episodic memory graph.** Build a lightweight graph of recurring entities (projects, people, tools) across sessions. At conversation start, retrieve the N most relevant entity summaries.
5. **Cross-session deduplication.** When the same question appears in multiple sessions, merge the answers into a single canonical entry in MEMORY.md rather than storing N separate sessions.

### Claim 5 — Builds a Deepening Model of Who You Are

1. **Behavioral inference without Honcho.** Add a lightweight in-process analyzer that reads recent sessions and infers preferences from behavior patterns — which languages appear in code blocks, which commands the user retries, message length preferences — without requiring a separate service.
2. **Structured user model schema.** Replace freeform `USER.md` with a YAML schema that can be updated in place and detected for staleness.
3. **Contradiction detection.** When a new `memory(action="add", category="user")` call contradicts an existing entry, flag it and ask the user which is current rather than silently appending both.
4. **Honcho as default.** Make Honcho the default user modeling backend (with a local fallback), so the genuinely sophisticated modeling capability is on by default, not opt-in.
5. **Periodic user model review.** Monthly (via cron), generate a "user model report" summarising what Hermes has inferred and ask the user to correct outdated entries.

---

## Part 2: Tiering the Suggestions

Not all 25 suggestions are equally feasible to implement. After reading the key source files (`tools/memory_tool.py`, `tools/skill_manager_tool.py`, `tools/session_search_tool.py`, `hermes_state.py`, `hermes_cli/main.py`, `hermes_cli/skills_hub.py`), the suggestions were grouped into three tiers:

### Tier 1 — Self-contained, no new dependencies

Changes confined to one or two files, no new external dependencies, no schema migrations, no changes to the agent loop.

| Suggestion | Files touched |
|-----------|--------------|
| Intra-session memory visibility | `tools/memory_tool.py` |
| Skill patch history (SKILL_HISTORY.md) | `tools/skill_manager_tool.py` |
| Evidence-required patches (reason field) | `tools/skill_manager_tool.py` |

### Tier 2 — Moderate, new features within existing patterns

New features that follow existing patterns in the codebase but require touching more files or adding new CLI subcommands.

| Suggestion | Files touched |
|-----------|--------------|
| Rollback command | `tools/skill_manager_tool.py`, `hermes_cli/skills_hub.py`, `hermes_cli/main.py` |
| Memory importance tiers | `tools/memory_tool.py` |
| Contradiction detection | `tools/memory_tool.py` |

### Tier 3 — New infrastructure or external dependencies

These require embedding models, background daemons, external APIs, or invasive changes to existing data formats. Not practical to implement in this codebase without significant new infrastructure.

| Suggestion | Reason for Tier 3 |
|-----------|-------------------|
| Outcome-gated skill creation | Requires hooking into conversation loop to detect user acceptance |
| Post-hoc cron analysis pass | Requires background job infrastructure |
| Deduplication check (embeddings) | Requires embedding model (e.g. Ollama) |
| Skill quality scoring | Requires persistent helpfulness tracking across sessions |
| Cross-session improvement signal | Requires session_search integration into skill tooling |
| Staged patches | Adds user-confirmation round-trip; complex UX |
| Memory consolidation cron | Requires background job infrastructure |
| Memory audit report CLI | Separate CLI command; deferred |
| Structured user model | Invasive format change; breaks existing USER.md content |
| Semantic search layer | Requires embedding model |
| Proactive context injection | Requires agent loop change at session start |
| Episodic memory graph | New infrastructure |
| Cross-session deduplication | New infrastructure |
| Behavioral inference | New infrastructure |
| Honcho as default | External API dependency |
| Periodic user model review cron | Background job infrastructure |

---

## Part 3: Proposed Implementation Plan

The plan covers **7 of the 25 suggestions** — the full Tier 1 and most of Tier 2.

### What will be implemented

**Change 1 — Intra-session memory visibility** (`tools/memory_tool.py`)

After a successful `add()` call, update `_system_prompt_snapshot` to include the new entry. This means a fact saved during turn 5 is available to the LLM at turn 6 in the same session.

Trade-off accepted: one Anthropic prompt cache miss on the turn the memory is added, then stable again for subsequent turns. This is acceptable — the caching benefit on long sessions far outweighs one miss.

**Change 2 — Skill patch history** (`tools/skill_manager_tool.py`)

After every successful `_patch_skill()` or `_edit_skill()` call, append a history record to `~/.hermes/skills/<name>/SKILL_HISTORY.md`. Each record contains: timestamp, the `reason` provided by the LLM, the old text block, and the new text block.

Note: the analysis suggested storing this in SQLite. After reviewing `hermes_state.py` (schema version 6, complex migration path), the file-based approach is used instead. The benefit is identical — a human-readable, rollback-able history — with no risk of breaking the database schema.

**Change 3 — Evidence-required patches** (`tools/skill_manager_tool.py`)

Add a `reason` parameter to the `skill_manage` tool schema for the `patch` and `edit` actions. The LLM must supply a short justification when modifying a skill. This is logged to `SKILL_HISTORY.md` alongside the diff.

**Change 4 — Rollback command** (`tools/skill_manager_tool.py`, `hermes_cli/skills_hub.py`, `hermes_cli/main.py`)

A new `hermes skills rollback <name>` CLI command. Reads `SKILL_HISTORY.md`, finds the most recent previous version of `SKILL.md`, and restores it. Confirms with the user before writing.

**Change 5 — Memory importance tiers** (`tools/memory_tool.py`)

Add a `priority` parameter to `memory(action="add")`: `high`, `normal` (default), or `ephemeral`. Entries are prefixed in the memory file (`[HIGH]`, `[EPHEMERAL: expires YYYY-MM-DD]`). In `format_for_system_prompt`, high-priority entries sort to the top. Ephemeral entries include an expiry date and are dropped if past it when the memory file is read.

**Change 6 — Contradiction detection** (`tools/memory_tool.py`)

On every `add()` call, check the new content for word overlap against existing entries. If significant overlap is found (>40% of meaningful words in common), the tool returns a warning alongside success: "This may overlap with an existing entry — consider reviewing." Does not block the add; informs the LLM so it can decide whether to replace instead.

### What is not being implemented, and why

| Suggestion | Reason not implemented |
|-----------|----------------------|
| Provider-agnostic summarization | Already implemented in `auxiliary_client.py` — critique was inaccurate |
| Structured user model (YAML) | Invasive format change; would silently break existing USER.md content for all current users |
| Skill versioning via SQLite | Replaced by SKILL_HISTORY.md file approach — same benefit, no migration risk |
| Memory consolidation cron | Background job; no cron infrastructure in this codebase to hook into cleanly |
| Memory audit report CLI | Useful but deferred; does not affect the core gaps |
| Cross-session improvement signal | Requires session_search integration into skill tool — significant scope expansion |
| Staged patches | Adds a two-turn confirmation round-trip; UX complexity outweighs benefit for a tutorial fork |
| All Tier 3 items | New infrastructure, external dependencies, or agent loop changes — out of scope |

---

## Part 4: Clarifications from Discussion

**On "fully implemented":** This phrase was used ambiguously in an earlier exchange and caused confusion. To be explicit: as of this document being written, **zero code changes have been made**. The implementation plan describes what *will* be written. Nothing has been implemented yet.

**On the relationship between the critique and the plan:** The 25 suggestions in the critique remain valid as written. The implementation plan does not modify or retract any of them. It implements 7, defers 17, and corrects 1 (provider-agnostic summarization, which already exists). The critique document (`self-improvement-deep-dive.md`) is accurate as a critique — it describes the state of the original Hermes Agent codebase.

**On what the plan changes vs. what the critique says:** The critique describes what *should* be done. The plan describes what *will* be done given time, complexity, and dependency constraints. The gap between the two is intentional and documented — it would form a realistic roadmap for a dedicated maintainer.

---

## Part 5: Coverage Map

| # | Suggestion | Critique claim | Plan status |
|---|-----------|----------------|-------------|
| 1 | Outcome-gated skill creation | Claim 1 | Not implemented — Tier 3 |
| 2 | Post-hoc cron analysis | Claim 1 | Not implemented — Tier 3 |
| 3 | Skill versioning | Claim 1 | Partially — via SKILL_HISTORY.md |
| 4 | Deduplication check | Claim 1 | Not implemented — Tier 3 |
| 5 | Skill quality scoring | Claim 1 | Not implemented — Tier 3 |
| 6 | Patch logging | Claim 2 | Will implement |
| 7 | Rollback command | Claim 2 | Will implement |
| 8 | Evidence-required patches | Claim 2 | Will implement |
| 9 | Cross-session signal | Claim 2 | Not implemented — scope |
| 10 | Staged patches | Claim 2 | Not implemented — UX complexity |
| 11 | Intra-session visibility | Claim 3 | Will implement |
| 12 | Memory consolidation cron | Claim 3 | Not implemented — Tier 3 |
| 13 | Importance tiers | Claim 3 | Will implement |
| 14 | Memory audit CLI | Claim 3 | Not implemented — deferred |
| 15 | Structured user model | Claim 3 / 5 | Not implemented — format breakage |
| 16 | Semantic search | Claim 4 | Not implemented — Tier 3 |
| 17 | Proactive context injection | Claim 4 | Not implemented — Tier 3 |
| 18 | Provider-agnostic summarization | Claim 4 | Already exists in codebase |
| 19 | Episodic memory graph | Claim 4 | Not implemented — Tier 3 |
| 20 | Cross-session deduplication | Claim 4 | Not implemented — Tier 3 |
| 21 | Behavioral inference | Claim 5 | Not implemented — Tier 3 |
| 22 | Structured user model schema | Claim 5 | Not implemented — format breakage |
| 23 | Contradiction detection | Claim 5 | Will implement |
| 24 | Honcho as default | Claim 5 | Not implemented — external dep |
| 25 | Periodic user model review | Claim 5 | Not implemented — Tier 3 |

**Total: 7 will be implemented, 1 already exists, 17 not in scope for this session.**
