# Hermes Autoresearch Loop

## Problem Statement

How might we design a background process that automatically measures whether Hermes's skills and memory actually work, generates improvement hypotheses, evaluates them without human-in-the-loop, and closes a genuine learning loop — borrowing from Karpathy's autoresearch loop pattern?

## Context: Why the Current Design Falls Short

All five self-improvement claims in Hermes reduce to the same pattern:

> A prompt string tells the LLM to call a tool. The tool writes to disk. The disk reloads in future sessions.

No background process. No outcome measurement. No evaluation function. No loop. Whether improvement happens depends entirely on whether the LLM decides to call the tool on that turn. It's **LLM-driven self-annotation**, not a learning loop.

The gap: a background process (not dependent on the LLM's in-context volition) that reviews sessions, measures outcomes, and applies improvements automatically.

## What Karpathy's Autoresearch Loop Requires

1. A **hypothesis representation** → skills (SKILL.md files)
2. An **experiment runner** → conversations (sessions in state.db)
3. An **automatic evaluator** → the hard part; no humans
4. A **hypothesis updater** → skill patcher
5. **Fast iteration** → nightly, not monthly

The bottleneck is always evaluation speed. If evaluation requires humans, you learn at human speed. If evaluation is automated, you learn 100x faster.

## Reward Signal

Three-part combined signal (all three required):

| Signal | Measurement | Source |
|--------|-------------|--------|
| Token efficiency | `(session_tokens - baseline_tokens) / baseline_tokens` | Session metadata |
| Absence of correction | Regex detection of "no", "wrong", "try again", goal rephrase | Session transcript |
| Task completion | Session ended naturally + final message is acknowledgment | Session transcript |

## Recommended Direction

Full Karpathy loop: real session signals identify underperforming skills → LLM generates candidate patches → self-play evaluation gates application → auto-apply winners → regression watch rolls back losers.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              HERMES AUTORESEARCH LOOP                │
│                  (nightly cron)                      │
└──────────────────────────────────────────────────────┘

INPUT LAYER
───────────
• ~/.hermes/state.db          → last 24h sessions (FTS5)
• ~/.hermes/skills/           → current skill content
• ~/.hermes/skill_metrics.db  → rolling 30-day skill health
• ~/.hermes/MEMORY.md         → current memory state
```

### Phase 1: Observe

For each session in last 24h, extract:

- `task_type` — LLM-clustered category
- `skills_invoked` — from prompt injection log
- `total_tokens` — from session metadata
- `correction_count` — regex: "no", "wrong", "try again", goal rephrase
- `completion_flag` — session ended naturally? final msg = acknowledgment?
- `tool_call_count` — from tool call log

**Output:** `skill_health_report.json`

Metrics per skill (rolling 7-day):

| Metric | Definition |
|--------|-----------|
| `invocation_rate` | How often skill is used |
| `token_efficiency` | Avg tokens vs. same task type without skill |
| `correction_rate` | % sessions with correction when skill invoked |
| `completion_rate` | % sessions completing naturally |
| `mid_session_patch_rate` | Was skill patched during a session? (gap signal) |

---

### Phase 2: Analyze

Detect anomalies:

**Underperforming skill:**
```
correction_rate > 0.3
OR token_efficiency > +20%
OR completion_rate < 0.5
```

**Missing skill:**
```
task_type cluster with ≥5 sessions in 7 days
AND no matching skill
(match: skill description vs. task_type embedding cosine similarity < 0.6)
```

**Stale memory:**
```
MEMORY.md entry referenced in session
AND then contradicted in later turn
(agent overwrites or ignores the memory fact)
```

**Regressed skill:**
```
correction_rate increased >15% vs. 7-day prior
(patch made it worse — trigger rollback candidate)
```

**Output:** `anomaly_report.json`

---

### Phase 3: Hypothesize

For each anomaly, LLM generates a candidate improvement:

**Underperforming → propose patch**
- Input: current `SKILL.md` + 5 worst sessions where skill was invoked
- Prompt: "This skill was used in these sessions. These corrections occurred. Identify what's missing or wrong. Propose a targeted patch."
- Output: `candidate_patch` (old_string, new_string, reason)

**Missing → propose new skill**
- Input: 5 best sessions in the uncovered task cluster
- Prompt: "These sessions all involve the same task type. Extract the successful approach and write a SKILL.md."
- Output: `candidate_skill` (name, content)

**Stale memory → propose memory update**
- Input: contradicting session turns
- Output: `candidate_memory_replace` (old_entry, new_entry)

---

### Phase 4: Self-Play Evaluation ← the Karpathy gate

For each candidate patch or new skill:

**Step 1 — Generate 5 synthetic task scenarios**
Mutate real session task descriptions from the relevant cluster.
Not random — grounded in actual task distribution.

**Step 2 — Run agent twice per scenario**
- Run A: system prompt with OLD skill
- Run B: system prompt with NEW skill
- Use cheap/fast model (Gemini Flash or Haiku)

**Step 3 — Evaluate each pair**
- Objective: `token_delta = (B_tokens - A_tokens) / A_tokens`
- Subjective: LLM judge rates A and B on correctness + completeness (0–10). Two independent judges, require agreement within 2 points.

**Step 4 — Accept / reject**
```
Accept if:  token_delta < 0   (more efficient)
       AND  quality_delta ≥ 0  (not worse quality)
Reject if:  quality drops even if tokens improve
Hold if:    judges disagree → flag for human review
```

**Output:** `evaluated_patches.json` (accepted / rejected / held)

---

### Phase 5: Apply (Fully Autonomous)

Before applying, check recency lock:

```python
last_patch = get_last_patch_timestamp(skill_name)  # reads SKILL_HISTORY.md
if last_patch and (now - last_patch) < timedelta(hours=24):
    defer_to_next_night(skill_name)  # in-session patch too recent — skip
    continue
```

For each accepted patch that passes the recency lock:
```python
skill_manage(action="patch", reason="autoresearch: <anomaly_type>",
             source="autoresearch", ...)   # source tag in SKILL_HISTORY.md
# Record baseline_metrics snapshot for regression detection tomorrow
```

For each accepted new skill:
```python
skill_manage(action="create", ...)
```

For memory updates:
```python
memory(action="replace", ...)
```

**Output:** `nightly_digest.md` — sent to user via configured platform (Telegram, Discord, etc.)

---

### Phase 6: Regression Watch (next night)

Compare today's skill metrics vs. snapshot taken at patch time. **Only roll back when autoresearch was the sole writer since its patch:**

```
If in-session patches occurred since autoresearch patch:
  → causation is ambiguous
  → flag in digest for human review — do NOT auto-rollback

If autoresearch was sole writer AND correction_rate increased >15%:
  → auto-rollback via SKILL_HISTORY.md (tagged [autoresearch: regression-watch])
  → flag in digest: "Skill X auto-rolled back — patch degraded performance"
```

See `docs/analysis/skill-improvement-coexistence.md` for why this scoping matters.

---

## The Feedback Loop

```
Real sessions (experiments)
        │
        ▼
Signal extraction
(tokens, corrections, completion)
        │
        ▼
Anomaly detection
(underperforming / missing / stale)
        │
        ▼
Hypothesis generation
(LLM proposes patches)
        │
        ▼
Self-play evaluation          ← Karpathy gate
(synthetic tasks, LLM judge)
        │
      Pass?
      /    \
    Yes     No ──→ discard or hold for human review
      │
      ▼
Auto-apply patch
        │
        ▼
Better skills → better real sessions
        │
        └─────────────────────────────┐
                                      │
            (loop repeats, faster each night
             as skill quality and signal fidelity rise)
```

---

## Key Assumptions to Validate

- [ ] **Token count is a valid proxy for efficiency** — risk: terse-but-wrong beats verbose-but-correct. Mitigation: only count token efficiency when correction_rate is simultaneously low.
- [ ] **Self-play synthetic tasks represent real task distribution** — risk: LLM-generated tasks are easier than real ones, making self-play evaluation overconfident. Mitigation: mutate real session descriptions rather than generating from scratch.
- [ ] **LLM-as-judge is calibrated** — risk: judge model prefers newer/longer responses regardless of quality. Mitigation: two independent judges + objective token metric as tiebreaker.
- [ ] **Fully autonomous patching can't degrade skills** — risk: a bad patch passes self-play on easy synthetic tasks, then degrades real performance. Mitigation: SKILL_HISTORY.md rollback + regression watch in Phase 6.
- [ ] **Correction signal is detectable from transcript** — risk: user corrections are implicit (rephrasing) not just explicit ("wrong"). Mitigation: detect goal-rephrase pattern (same intent, different words, within 2 turns).

## MVP Scope

Minimum version that tests the core hypothesis (does automated skill patching improve real session metrics?):

1. Phase 1 (Observe) — session signal extraction to `skill_metrics.db`
2. Phase 2 (Analyze) — underperforming skill detection only (skip missing/stale for now)
3. Phase 3 (Hypothesize) — LLM patch generation for worst-performing skill only
4. Phase 4 (Self-play) — 3 synthetic tasks, 1 judge, simple token+quality gate
5. Phase 5 (Apply) — auto-apply with SKILL_HISTORY.md logging
6. Phase 6 (Regression) — next-night rollback if correction_rate rises

**What's out of MVP:**
- Missing skill detection (Pattern Miner direction)
- Memory staleness repair
- ELO tournament across skill versions
- Multi-day trend analysis
- Semantic embedding similarity for skill deduplication

## Coexistence with In-Session LLM Patching

The autoresearch loop is not the only system that modifies `SKILL.md` files. The existing in-session LLM patching mechanism does too, and without coordination they conflict. See **`docs/analysis/skill-improvement-coexistence.md`** for the full conflict analysis.

The short version: four coordination mechanisms prevent conflicts.

| Mechanism | What it does |
|-----------|-------------|
| **Recency lock** | Autoresearch skips skills patched in-session within last 24h |
| **In-session patch rate as signal** | ≥3 in-session patches in 7 days → autoresearch proposes full rewrite, not another patch |
| **Source tagging** | Every `SKILL_HISTORY.md` entry tagged `[in-session]` or `[autoresearch]` — always auditable |
| **Scoped regression watch** | Phase 6 only rolls back its own patches; flags ambiguous cases for human review |

The mental model: in-session patching = hotfixes. Autoresearch = releases. Releases build on hotfixes, never over them.

---

## Not Doing (and Why)

- **Gradient updates / fine-tuning** — not needed; skills are the weight updates. The LLM itself doesn't change. Adding fine-tuning requires GPU infrastructure and resets on model upgrade.
- **Semantic embedding search on sessions** — useful but orthogonal. FTS5 is sufficient for Phase 2 pattern mining.
- **User model inference from behavior** — valuable but Phase 2 of this roadmap. Fix skill quality first, then tackle user modeling.
- **ELO tournament across all skill versions** — correct long-term direction but needs 30+ days of history to be meaningful. Build after core loop is validated.
- **Human approval gate** — adds friction that defeats the purpose of an automated loop. Regression watch + SKILL_HISTORY.md rollback handles the safety concern instead.

## Open Questions

- How do we detect `task_type` clusters without LLM embedding cost on every session? (candidate: cache task_type classification per session at session-end time, not at cron time)
- What's the right synthetic task mutation strategy? Pure paraphrase? Add noise? Introduce edge cases?
- Should `skill_metrics.db` be a separate SQLite file or a new table in `state.db`?
- How do we handle skills with very low invocation_rate (< 3 uses in 30 days)? Not enough data for statistical comparison — skip or use self-play only?
- What platform does the `nightly_digest.md` get sent to? Should it go to the same channel as the user's primary gateway, or a separate admin channel?
