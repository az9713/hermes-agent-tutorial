# Autoresearch Loop: MVP Implementation Plan

**Status:** Design complete, not yet built  
**Related docs:**
- Architecture: `docs/ideas/autoresearch-loop.md`
- Coexistence analysis: `docs/analysis/skill-improvement-coexistence.md`
- Session discussion: `docs/analysis/session-2026-04-14.md`

---

## Guiding Principles

1. **Each stage ships independently.** Every stage is useful on its own and leaves the app in a better state than before.
2. **Never break existing behaviour.** The autoresearch loop is purely additive until Stage 3. Existing in-session patching, memory, and cron jobs are untouched.
3. **Coexistence first.** Source tagging (the single change to `skill_manager_tool.py`) is implemented in Stage 1 — before any automation — so the history is readable before anything automated writes to it.
4. **Build the signal before trusting it.** We validate that signal extraction produces sensible output (Stage 1) before using it to drive automated changes (Stage 3).

---

## Stage Overview

```
Stage 1 — Observe                  Zero risk   Additive only
  Signal extraction → skill_metrics.db
  Source tagging in SKILL_HISTORY.md
  Nightly report: which skills are underperforming?

Stage 2 — Hypothesize + Evaluate   Low risk    No auto-apply
  Anomaly detection → which skills need attention
  LLM patch generation → candidate patches
  Self-play evaluation → filter candidates
  Nightly report: here are the patches that would pass the gate

Stage 3 — Apply + Recover          Managed risk  Full loop
  Recency lock → don't overwrite fresh in-session patches
  Auto-apply accepted patches
  Regression watch → auto-rollback if metrics worsen
  Nightly digest delivered via configured gateway platform
```

---

## Stage 1: Observe

### Goal
Build the signal infrastructure. Extract session outcomes into a queryable database. Add source tagging to `SKILL_HISTORY.md`. Produce a human-readable nightly report. No skill files are modified.

### Risk
Zero. All new code is read-only with respect to existing app state. The one modification to `skill_manager_tool.py` is additive (new optional parameter, existing callers unchanged).

### New Files

```
cron/autoresearch/
├── __init__.py
├── signal_extractor.py      # reads state.db, extracts per-session signals
├── skill_metrics.py         # reads/writes skill_metrics.db (new SQLite file)
└── reporter.py              # generates ~/.hermes/autoresearch/nightly_report.md
```

#### `signal_extractor.py`

Reads the last 24h of sessions from `~/.hermes/state.db`. For each session, extracts:

| Field | How extracted |
|-------|--------------|
| `session_id` | Direct from sessions table |
| `task_type` | LLM call: classify session into one of N task categories. Cached per session — not re-extracted nightly |
| `skills_invoked` | Parse system prompt log or session metadata for active skill names |
| `total_tokens` | From session metadata |
| `correction_count` | Regex scan of session transcript: `\b(no|wrong|that's not|try again|not what I|incorrect)\b`, goal rephrase detection (same intent rephrased within 2 turns) |
| `completion_flag` | Session ended naturally (not mid-error-loop) AND final user message matches acknowledgment pattern (`\b(thanks|perfect|done|great|got it)\b`) |
| `tool_call_count` | Count of tool use blocks in session |
| `in_session_patches` | Parse SKILL_HISTORY.md entries tagged `[in-session]` with timestamps in session window |

#### `skill_metrics.py`

Schema for `~/.hermes/autoresearch/skill_metrics.db`:

```sql
CREATE TABLE session_signals (
    session_id        TEXT NOT NULL,
    date              DATE NOT NULL,
    task_type         TEXT,
    skills_invoked    TEXT,   -- JSON array of skill names
    total_tokens      INTEGER,
    correction_count  INTEGER,
    completion_flag   INTEGER, -- 0 or 1
    tool_call_count   INTEGER,
    extracted_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE skill_health (
    skill_name            TEXT NOT NULL,
    date                  DATE NOT NULL,
    invocation_count      INTEGER DEFAULT 0,
    avg_tokens            REAL,
    correction_rate       REAL,    -- corrections / invocations
    completion_rate       REAL,    -- completions / invocations
    in_session_patch_count INTEGER DEFAULT 0,
    PRIMARY KEY (skill_name, date)
);

CREATE TABLE autoresearch_patches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name       TEXT NOT NULL,
    patch_applied_at TIMESTAMP,
    patch_type       TEXT,           -- 'patch' | 'edit' | 'new_skill'
    baseline_tokens  REAL,
    baseline_correction_rate REAL,
    baseline_completion_rate REAL,
    status           TEXT DEFAULT 'applied'  -- 'applied' | 'rolled_back'
);
```

#### `reporter.py`

Writes `~/.hermes/autoresearch/nightly_report.md`:

```markdown
# Autoresearch Nightly Report — 2026-04-15

## Sessions Analysed
- 12 sessions from last 24h
- 8 skills invoked at least once

## Skill Health Summary

| Skill            | Invocations | Avg Tokens | Correction Rate | Completion Rate | Status  |
|------------------|-------------|------------|-----------------|-----------------|---------|
| git-workflow     | 5           | 1,240      | 0.40 ⚠          | 0.60            | FLAGGED |
| web-search       | 3           | 890        | 0.00            | 1.00            | OK      |
| code-review      | 2           | 2,100      | 0.50 ⚠          | 0.50            | FLAGGED |

## Flagged Skills (above threshold)
- git-workflow: correction_rate 0.40 (threshold: 0.30)
- code-review: correction_rate 0.50 (threshold: 0.30)

## Missing Coverage (high-frequency task types with no skill)
- "database query optimisation" — appeared in 4 sessions, no matching skill

## No patches applied this cycle (Stage 1 — observe only)
```

### Modification to Existing Code

**`tools/skill_manager_tool.py` — one change only:**

```python
# BEFORE:
def _append_skill_history(skill_dir, action, reason, file_path, old_text, new_text):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = (
        f"\n## {now} — {action}\n"
        ...
    )

# AFTER:
def _append_skill_history(skill_dir, action, reason, file_path, old_text, new_text,
                           source: str = "in-session"):   # NEW parameter
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = (
        f"\n## {now} — {action} [{source}]\n"             # source tag added
        ...
    )
```

All existing callers omit `source` → default `"in-session"` → no breakage. No other existing files are modified in Stage 1.

### Wiring the Cron Job

Add a new cron job via `hermes cron add` (or directly in `~/.hermes/cron/jobs.json`):

```json
{
  "id": "autoresearch-nightly",
  "schedule": "0 3 * * *",
  "prompt": "Run the autoresearch nightly observation cycle",
  "deliver": "local",
  "enabled": true
}
```

The cron job invokes `cron/autoresearch/__init__.py:run_stage1()`. No changes to `cron/scheduler.py` — it already supports arbitrary job handlers.

### Verification Criteria for Stage 1

- [ ] `~/.hermes/autoresearch/skill_metrics.db` exists and is populated after first run
- [ ] `~/.hermes/autoresearch/nightly_report.md` is generated with correct session counts
- [ ] `SKILL_HISTORY.md` entries now show `[in-session]` tag on new patches
- [ ] Existing `hermes skills history` output is unchanged (tags display correctly in Rich table)
- [ ] No existing tests break
- [ ] Running Stage 1 cron on a day with 0 sessions produces an empty-but-valid report

---

## Stage 2: Hypothesize + Evaluate

### Goal
Use the signals from Stage 1 to detect underperforming skills, generate candidate patches via LLM, and evaluate them via self-play. Output: a `pending_patches.json` file. Nothing is applied automatically yet.

### Risk
Low. No skill files are modified. LLM calls add cost (~$0.05–0.20/night depending on volume). Agent instances spawned for self-play are isolated and write nothing to disk.

### New Files

```
cron/autoresearch/
├── anomaly_detector.py      # reads skill_metrics.db, returns flagged skills
├── hypothesis_generator.py  # LLM call: given flagged skill + sessions, propose patch
├── self_play_evaluator.py   # runs old vs new skill on synthetic tasks, scores both
└── pending_patches.py       # writes ~/.hermes/autoresearch/pending_patches.json
```

#### `anomaly_detector.py`

Reads 7-day rolling metrics from `skill_metrics.db`. Returns anomalies:

```python
THRESHOLDS = {
    "correction_rate":   0.30,   # flag if > 30% of sessions have corrections
    "token_efficiency":  0.20,   # flag if > 20% above baseline for same task type
    "completion_rate":   0.50,   # flag if < 50% of sessions complete naturally
    "in_session_patches": 3,     # flag for structural rewrite if >= 3 in 7 days
}

MISSING_SKILL_THRESHOLD = 5     # flag if task cluster appears 5+ times with no skill
```

Anomaly types:
- `UNDERPERFORMING` — existing skill with bad metrics
- `STRUCTURALLY_BROKEN` — existing skill with high in-session patch rate (→ full rewrite)
- `MISSING_COVERAGE` — high-frequency task type with no matching skill

#### `hypothesis_generator.py`

For each `UNDERPERFORMING` anomaly:

```
Input:
  - Current SKILL.md content (live version, including all in-session patches)
  - Up to 5 worst sessions where this skill was invoked (transcript excerpts)
  - The anomaly type and metric values

LLM prompt:
  "This skill was invoked in the following sessions. In each session,
   the agent was corrected [N] times on these specific turns: [...].
   The current skill content is: [SKILL.md].
   Identify the specific gap or error causing corrections.
   Propose a targeted patch: old_string and new_string."

Output:
  candidate_patch = {
    "skill_name": "git-workflow",
    "action": "patch",
    "old_string": "...",
    "new_string": "...",
    "reason": "...",
    "anomaly_type": "UNDERPERFORMING",
    "trigger_metric": "correction_rate=0.41"
  }
```

For `STRUCTURALLY_BROKEN` (high in-session patch rate):

```
Input:
  - Current SKILL.md (already incorporates recent in-session patches)
  - All in-session patches from the last 7 days (their old_string / new_string)
  - 5 representative sessions

LLM prompt:
  "This skill has been patched [N] times in the last 7 days by the agent
   during sessions. These patches suggest structural gaps: [...].
   The current state (after all patches) is: [SKILL.md].
   Write a clean, comprehensive rewrite that incorporates all recent fixes
   and addresses the underlying structural problems."

Output:
  candidate_patch = {
    "action": "edit",   # full rewrite, not incremental patch
    "content": "...",   # complete new SKILL.md
    ...
  }
```

#### `self_play_evaluator.py`

For each candidate patch, runs a self-play evaluation:

**Step 1 — Generate 5 synthetic task scenarios**

Mutate real session task descriptions from the relevant task cluster. Not random generation — grounded in actual distribution:

```python
def generate_synthetic_tasks(task_cluster_sessions, n=5):
    # Take n real session opening messages
    # LLM: "Rephrase this task description slightly — same intent, different wording"
    # Returns 5 variants that represent the same task type
```

**Step 2 — Run agent twice per scenario**

```python
for task in synthetic_tasks:
    response_A = run_agent_with_skill(task, skill_content=old_skill_content, model="gemini-flash")
    response_B = run_agent_with_skill(task, skill_content=new_skill_content, model="gemini-flash")
```

Uses cheap/fast model (Gemini Flash or Haiku). Each run is fully isolated — no tool execution, no file writes, just a single-turn completion with the task + skill in context.

**Step 3 — Evaluate**

```python
# Objective metric
token_delta = (len_B_tokens - len_A_tokens) / len_A_tokens

# Subjective metric — two independent LLM judges
score_A = judge(task, response_A)   # 0–10 for correctness + completeness
score_B = judge(task, response_B)
quality_delta = score_B - score_A

# Acceptance gate
accepted = token_delta < 0 and quality_delta >= 0
           # (more efficient AND not worse quality)
```

If the two judges disagree by more than 2 points: mark as `HOLD` for human review.

**Output:**

```json
{
  "skill_name": "git-workflow",
  "action": "patch",
  "accepted": true,
  "token_delta": -0.12,
  "quality_delta": 0.4,
  "judge_scores": [[7.5, 8.0], [7.2, 7.8]],
  "old_string": "...",
  "new_string": "...",
  "reason": "autoresearch: correction_rate 0.41 (7 sessions). Self-play: -12% tokens, quality +0.4"
}
```

#### `pending_patches.py`

Writes `~/.hermes/autoresearch/pending_patches.json`. The nightly report now includes a section:

```markdown
## Candidate Patches (Stage 2 — pending approval)

### git-workflow
- Action: patch
- Trigger: correction_rate 0.41 (threshold 0.30) over 7 sessions
- Self-play result: -12% tokens, quality +0.4 (2/2 judges agree)
- Status: ACCEPTED (would be applied in Stage 3)

### code-review
- Action: patch
- Trigger: correction_rate 0.50 over 5 sessions
- Self-play result: -3% tokens, quality -0.8
- Status: REJECTED (quality would drop)
```

### Verification Criteria for Stage 2

- [ ] `pending_patches.json` is generated with `accepted`/`rejected`/`hold` status per patch
- [ ] Self-play uses current `SKILL.md` (including recent in-session patches), not a cached version
- [ ] Rejected patches are logged with reason in nightly report
- [ ] `HOLD` patches appear in report with note "judges disagreed — manual review needed"
- [ ] Running on a day with no anomalies produces an empty pending_patches.json and logs "No anomalies detected"
- [ ] No skill files are modified

---

## Stage 3: Apply + Recover

### Goal
Auto-apply accepted patches with recency lock (skip skills patched in-session within 24h). Run regression watch the following night. Deliver nightly digest via configured gateway platform.

### Risk
Managed. Skill files are now modified automatically. Risks are bounded by:
- Recency lock prevents racing with in-session patches
- Self-play gate (Stage 2) filtered bad patches before they reach here
- SKILL_HISTORY.md rollback provides manual escape hatch
- Regression watch auto-rolls-back if metrics worsen

### New Files

```
cron/autoresearch/
├── applier.py               # applies accepted patches with recency lock
├── regression_watch.py      # next-night: check metrics post-patch, rollback if worse
└── digest.py                # formats and delivers nightly digest via gateway
```

#### `applier.py`

```python
def apply_accepted_patches(pending_patches):
    for patch in pending_patches:
        if patch["status"] != "accepted":
            continue

        # Recency lock: skip if patched in-session within 24h
        last_patch = get_last_patch_timestamp(patch["skill_name"])
        if last_patch and (now() - last_patch) < timedelta(hours=24):
            log(f"Skipping {patch['skill_name']}: in-session patch within 24h, deferring")
            patch["status"] = "deferred"
            continue

        # Apply via skill_manage (goes through security scan + atomic write)
        result = skill_manage(
            action=patch["action"],
            name=patch["skill_name"],
            old_string=patch.get("old_string"),
            new_string=patch.get("new_string"),
            content=patch.get("content"),
            reason=patch["reason"],
            source="autoresearch",              # source tag in SKILL_HISTORY.md
        )

        if result["success"]:
            # Record baseline metrics for regression watch
            record_autoresearch_patch(
                skill_name=patch["skill_name"],
                baseline_tokens=patch["baseline_tokens"],
                baseline_correction_rate=patch["trigger_metric_value"],
            )
```

#### `regression_watch.py`

Runs as part of the nightly cycle, one night after patches were applied:

```python
def check_regression():
    for patch_record in get_autoresearch_patches_from_last_cycle():
        skill_name = patch_record["skill_name"]
        baseline_correction_rate = patch_record["baseline_correction_rate"]

        # Check if in-session patches happened since autoresearch patch
        in_session_since = count_in_session_patches_since(
            skill_name, patch_record["patch_applied_at"]
        )

        if in_session_since > 0:
            # Causation ambiguous — can't attribute metric change to autoresearch
            flag_for_human_review(skill_name,
                reason=f"{in_session_since} in-session patch(es) occurred after autoresearch patch — cannot determine cause of metric change")
            continue

        # Only auto-rollback if autoresearch was sole writer
        current_correction_rate = get_current_correction_rate(skill_name)
        delta = current_correction_rate - baseline_correction_rate

        if delta > 0.15:
            skill_manage(
                action="patch",
                name=skill_name,
                # Restore old_string/new_string from SKILL_HISTORY.md
                reason=f"autoresearch regression-watch: correction_rate rose +{delta:.0%} after patch",
                source="autoresearch: regression-watch",
            )
        else:
            log(f"{skill_name}: stable post-patch (delta={delta:.0%})")
```

#### `digest.py`

Formats the nightly digest and delivers via the gateway's configured delivery platform:

```markdown
# Hermes Autoresearch — Nightly Digest 2026-04-15

## Applied
- ✓ git-workflow: patch applied (-12% tokens, quality +0.4)

## Skipped (recency lock)
- web-search: patched in-session 6h ago — deferred to tomorrow

## Rejected by self-play
- code-review: quality would drop (-0.8) — discarded

## Regression watch
- git-workflow: stable (+2% correction rate delta — within threshold)

## Needs your attention
- (none)
```

### Verification Criteria for Stage 3

- [ ] Recency lock correctly skips skills patched in-session within 24h
- [ ] Applied patches appear in `SKILL_HISTORY.md` with `[autoresearch]` tag
- [ ] Applied patches appear in `autoresearch_patches` table in `skill_metrics.db`
- [ ] Regression watch does NOT roll back when in-session patches occurred since autoresearch patch
- [ ] Regression watch DOES roll back when autoresearch was sole writer and correction_rate rose >15%
- [ ] Rollback entries appear in `SKILL_HISTORY.md` as `[autoresearch: regression-watch]`
- [ ] Nightly digest is delivered via the gateway platform configured in `~/.hermes/config.yaml`
- [ ] Manual `hermes skills rollback` still works on autoresearch-applied patches

---

## File Map

### New files (all additive)

```
cron/autoresearch/
├── __init__.py
├── signal_extractor.py
├── skill_metrics.py
├── anomaly_detector.py
├── hypothesis_generator.py
├── self_play_evaluator.py
├── pending_patches.py
├── applier.py
├── regression_watch.py
└── digest.py

~/.hermes/autoresearch/           (runtime, not in repo)
├── skill_metrics.db
├── nightly_report.md
└── pending_patches.json
```

### Modified files (minimal)

| File | Change | Stage |
|------|--------|-------|
| `tools/skill_manager_tool.py` | Add `source` parameter to `_append_skill_history()` | 1 |
| `tools/skill_manager_tool.py` | Pass `source` through `skill_manage()` public interface | 3 |

### Untouched files

Everything else. In particular:
- `run_agent.py` — no changes
- `agent/prompt_builder.py` — no changes (in-session guidance strings unchanged)
- `cron/scheduler.py` — no changes (autoresearch added as a normal cron job)
- `hermes_cli/skills_hub.py` — no changes (history/rollback CLI unchanged, source tag renders in existing Rich table)
- All gateway files — unchanged

---

## Open Questions (to answer before starting Stage 2)

1. **task_type classification:** Extract at session-end (requires hook in `run_agent.py`) or at cron time from transcript (no existing hook needed, but slower)? Session-end extraction is cleaner and amortises cost but touches `run_agent.py`.

2. **`skill_metrics.db` location:** Under `~/.hermes/autoresearch/` (separate from `state.db`) or a new table in `state.db`? Separate file is cleaner isolation; shared file simplifies queries that join sessions with metrics.

3. **Self-play model:** Which model runs the self-play agent instances? Gemini Flash is cheapest; Haiku is available without separate API key if user already has Anthropic configured. Should be configurable in `~/.hermes/config.yaml`.

4. **Skills with low invocation rate:** If a skill has <3 invocations in 30 days, there is no statistical signal. Strategy options: (a) skip entirely, (b) self-play only (no real-session signal needed), (c) flag in report as "insufficient data."

5. **Digest delivery:** Should the digest go to the user's primary gateway platform (Telegram, Discord, etc.) or a separate admin channel? A misconfigured delivery could spam a shared channel. Safest default: write to `~/.hermes/autoresearch/nightly_report.md` only; delivery is opt-in via explicit config.
