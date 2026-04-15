# Two Skill Improvement Systems: Coexistence Analysis

> **Who should read this:** Anyone implementing or debugging the autoresearch loop, or anyone confused by a `SKILL_HISTORY.md` that shows interleaved patches from two different sources.

---

## Overview: Two Systems, Same Files

Hermes has — or will have — two independent mechanisms that both modify `SKILL.md` files:

| | In-Session LLM Patching | Autoresearch Loop |
|--|------------------------|-------------------|
| **Trigger** | LLM decides to patch mid-conversation | Nightly cron, runs regardless of LLM volition |
| **Signal** | LLM's in-context observation ("step 3 is wrong") | Measured session outcomes (token count, correction rate, completion) |
| **Timing** | Immediate — same conversation turn | Deferred — next morning |
| **Scope** | Targeted patch to one section | Potentially full rewrite of structural gaps |
| **Evaluation** | None — applied immediately on LLM judgment | Self-play gate: old vs. new skill on synthetic tasks |
| **Source** | `SKILLS_GUIDANCE` prompt instructs the LLM | `cron/autoresearch/` module |
| **Implemented?** | Yes (upstream + this fork) | Proposed — not yet built |

Both systems write to the same `SKILL.md` files and append to the same `SKILL_HISTORY.md`. Without coordination, they conflict.

---

## The Conflict Scenarios

### Scenario 1 — Sequential Overwrite

```
Monday 14:23  LLM patches step 3 of `git-workflow` skill mid-session.
              Fixes a missing --rebase flag. Works correctly.

Tuesday 03:00 Autoresearch cron runs. Extracts signals from Monday's sessions.
              Monday sessions (before 14:23) show git-workflow was underperforming.
              Cron generates a patch targeting step 3.
              Cron applies the patch.

Result:       LLM's fix is silently overwritten by the cron.
              The skill now has the cron's version of step 3, not the LLM's fix.
              No error. No warning. The overwrite is invisible.
```

### Scenario 2 — Regression Watch Misattribution

```
Tuesday 03:00 Autoresearch cron patches `git-workflow`.
Tuesday 09:15 LLM patches `git-workflow` again in a new session.
              This patch makes the skill worse (LLM misjudged).
              correction_rate rises.

Wednesday 03:00 Autoresearch regression watch checks metrics.
                Sees: correction_rate rose after Tuesday 03:00 patch.
                Concludes: the autoresearch patch caused the regression.
                Rolls back the autoresearch patch.

Result:       Wrong patch rolled back. The LLM's bad Tuesday 09:15 patch survives.
              Autoresearch's good Tuesday 03:00 patch is gone.
              The skill is now in the worst possible state.
```

### Scenario 3 — Stale Signal Contamination

```
Monday 09:00  skill-X is performing badly. correction_rate = 0.45.
Monday 14:00  LLM patches skill-X mid-session. Fixes the root cause.
              correction_rate drops to 0.08 in Monday afternoon sessions.

Tuesday 03:00 Autoresearch extracts signals from all Monday sessions.
              Aggregates Monday's correction_rate = (0.45 * 5 + 0.08 * 2) / 7 = 0.35.
              Threshold is 0.30. Skill flagged as underperforming.
              Cron generates a patch — targeting the same root cause the LLM already fixed.
              Cron applies patch.

Result:       Cron patches an already-fixed skill.
              New patch may conflict with the LLM's Monday 14:00 fix.
              At best: redundant. At worst: corrupts a working fix.
```

### Scenario 4 — User Confusion Reading SKILL_HISTORY.md

A user runs `hermes skills history git-workflow` and sees:

```
Timestamp              Action    Reason
─────────────────────  ────────  ─────────────────────────────────────
2026-04-14 14:23:00Z   patch     Fixed missing --rebase flag
2026-04-15 03:00:00Z   patch     correction_rate 0.41 over 7 sessions
2026-04-15 03:01:00Z   rollback  Regression: correction_rate +18%
2026-04-15 09:15:00Z   patch     Step 3 was wrong, fixed again
```

Questions the user cannot answer:
- Why did the 03:00 patch get rolled back?
- Did the 03:01 rollback undo the 14:23 fix or the 03:00 patch?
- Which system made each entry?
- Is the current state of the skill correct?
- Who should I trust — the LLM or the cron?

---

## Root Cause

Both systems are **writers with no coordination protocol**, operating on the same files with different timing and different information:

- The LLM writes based on **in-context evidence** (what it just observed in this session).
- The autoresearch loop writes based on **historical signal aggregates** (what happened across all sessions in the past 7 days).

Neither system knows what the other has recently done. There is no lock, no message queue, no shared state beyond the files themselves.

---

## The Resolution: Hotfix vs. Release Model

The cleanest mental model that eliminates the conflict:

```
In-session LLM patches  =  hotfixes   (applied immediately to production)
Autoresearch patches    =  releases   (systematic, incorporate hotfixes, data-driven)
```

**The rule in software:** releases never overwrite hotfixes — releases build on them. The next release picks up all prior hotfixes and supersedes them with a more complete solution.

**Applied to Hermes:**

1. LLM patches `SKILL.md` immediately when it notices something wrong (hotfix).
2. Autoresearch reads the **current** `SKILL.md` (including all hotfixes) when generating its patches — never a stale snapshot.
3. If the LLM patched a skill in the last 24 hours, autoresearch **skips it** — the hotfix hasn't accumulated enough real session signal to evaluate yet.
4. High in-session patch frequency (≥3 patches in 7 days) is a **signal to autoresearch** that the skill has structural gaps — autoresearch targets a comprehensive rewrite, not another incremental patch.
5. The two systems use the same `SKILL_HISTORY.md` format, but entries are **tagged by source** so users can always tell which system made which change.

---

## The Four Coordination Mechanisms

### Mechanism 1 — Recency Lock

Before applying any autoresearch patch, check when the skill was last modified:

```python
last_patch_time = get_last_patch_timestamp(skill_name)  # reads SKILL_HISTORY.md
if (now - last_patch_time) < timedelta(hours=24):
    defer_to_next_night(skill_name, candidate_patch)
    continue  # skip — let the in-session fix accumulate real signal first
```

**Effect:** Autoresearch never races with a fresh in-session hotfix. It waits until the hotfix has been live for at least one full day before deciding whether further improvement is needed.

### Mechanism 2 — In-Session Patch Rate as Signal

```python
in_session_patch_rate = count_tagged_patches(skill_name, tag="[in-session]", days=7)

if in_session_patch_rate == 0:
    strategy = "patch"        # small targeted fix
elif in_session_patch_rate < 3:
    strategy = "patch"        # still incremental
else:
    strategy = "edit"         # structural rewrite incorporating all hotfixes
    # The rewrite reads ALL recent in-session patches and synthesises them
    # into a coherent updated SKILL.md — not a conflict, a consolidation.
```

**Effect:** When the LLM has been hotfixing the same skill repeatedly, autoresearch interprets this as a structural problem and proposes a comprehensive rewrite that incorporates and supersedes all the hotfixes. The two systems inform each other.

### Mechanism 3 — Source Tagging in SKILL_HISTORY.md

Every entry in `SKILL_HISTORY.md` carries a source tag. This requires a one-line change to `_append_skill_history()` in `tools/skill_manager_tool.py`:

```
## 2026-04-14T14:23:00Z — patch [in-session]
Reason: Fixed missing --rebase flag
...

## 2026-04-15T03:00:00Z — patch [autoresearch]
Reason: correction_rate 0.41 over 7 sessions (threshold: 0.30). Self-play: -12% tokens, quality +0.4
...

## 2026-04-15T03:01:00Z — rollback [autoresearch: regression-watch]
Reason: correction_rate rose +18% after autoresearch patch on 2026-04-15T03:00:00Z
...
```

**Effect:** `hermes skills history` becomes unambiguous. The user always knows which system made which change and why. The reason field for autoresearch entries includes the metric that triggered the change.

### Mechanism 4 — Scoped Regression Watch

Phase 6 (regression watch) only rolls back patches it made, and only when causation is unambiguous:

```python
# Only watch skills that autoresearch patched in the last cycle
for skill in autoresearch_patched_skills:

    # Check if in-session patches happened AFTER the autoresearch patch
    in_session_patches_since = count_in_session_patches_since(skill, autoresearch_patch_time)

    if in_session_patches_since > 0:
        # Ambiguous cause — can't know if autoresearch or LLM patch caused regression
        flag_for_human_review(skill, reason="mixed sources since last autoresearch patch")
        continue  # do NOT auto-rollback

    # Only auto-rollback if autoresearch was the sole writer since its patch
    if correction_rate_delta > 0.15:
        rollback(skill, tag="[autoresearch: regression-watch]")
```

**Effect:** The regression watch never incorrectly rolls back autoresearch patches when the LLM has been the real cause. When causation is ambiguous, it flags for human review and takes no action.

---

## The Resulting Relationship

With these four mechanisms in place, the two systems are **complementary, not competing**:

```
In-session LLM patching          Autoresearch loop
────────────────────────         ─────────────────────────────────
Reactive                         Proactive
Fixes problems now               Finds structural patterns
Works on in-context evidence     Works on measured session outcomes
Runs during conversations        Runs nightly
Tactical hotfix                  Strategic release

          ↕  signals flow both ways

LLM hotfix rate      ──→  Autoresearch detects structural gaps
LLM hotfix content   ──→  Autoresearch builds on, not over, hotfixes
Better autoresearch  ──→  Fewer LLM hotfixes needed (validates the loop)
```

**The virtuous cycle:** When autoresearch correctly rewrites a structurally broken skill, the LLM stops needing to hotfix it. The in-session patch rate for that skill drops. The autoresearch loop sees this as confirmation that the rewrite succeeded. This is measurable validation of the loop's effectiveness.

---

## Impact on SKILL_HISTORY.md Readability

With source tagging, a user reading `hermes skills history git-workflow` now sees:

```
#   Timestamp              Action    Source                    Reason
──  ─────────────────────  ────────  ────────────────────────  ─────────────────────────────────────
1   2026-04-14 14:23:00Z   patch     [in-session]              Fixed missing --rebase flag
2   2026-04-15 03:00:00Z   patch     [autoresearch]            correction_rate 0.41 (7 sessions)
3   2026-04-15 03:01:00Z   rollback  [autoresearch: regwatch]  correction_rate rose +18%
4   2026-04-15 09:15:00Z   patch     [in-session]              Step 3 incomplete, added --ff-only
```

The user can now answer all their questions:
- Entry 2 was autoresearch. Entry 3 rolled back entry 2 because metrics worsened.
- Entry 1 and entry 4 were both in-session hotfixes by the LLM.
- The current skill state = entry 1 content + entry 4 patch applied on top.
- Entry 3 rolled back entry 2 only (not entry 1).

---

## Required Code Changes

Only **one existing file** needs modification to enable full coexistence:

### `tools/skill_manager_tool.py` — add `source` parameter to `_append_skill_history()`

```python
def _append_skill_history(
    skill_dir: Path,
    action: str,
    reason: str,
    file_path: str,
    old_text: str,
    new_text: str,
    source: str = "in-session",  # NEW: "in-session" | "autoresearch" | "autoresearch: regression-watch"
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = (
        f"\n## {now} — {action} [{source}]\n"   # source tag in header
        f"**Reason:** {reason or '(no reason given)'}\n"
        ...
    )
```

All existing callers pass no `source` argument, so they default to `"in-session"` — no breakage. The autoresearch module passes `source="autoresearch"` explicitly.

Everything else in the autoresearch loop lives in new files under `cron/autoresearch/` — zero modification to existing code paths beyond this one parameter addition.

---

## FAQ

**Q: What if autoresearch and an in-session patch try to patch the same skill at literally the same time?**

Both go through `_atomic_write_text()` which uses `os.replace()`. The last writer wins atomically — no corruption, but one write is silently lost. The recency lock (24h gap) makes this race astronomically unlikely in practice. If it's a concern, a file lock can be added to `skill_manage()`.

**Q: Can autoresearch roll back an in-session patch?**

No. The regression watch only rolls back patches it tagged as `[autoresearch]`. In-session patches tagged `[in-session]` are never auto-rolled-back by the cron. Manual rollback via `hermes skills rollback` still works for both.

**Q: What if the LLM has been patching a skill daily for a week — won't the autoresearch loop never get a chance to run (always blocked by recency lock)?**

The recency lock uses the last patch timestamp. If sessions happen daily and the LLM patches daily, autoresearch will always be blocked for that skill. This is intentional — a skill being patched daily is either actively being refined (leave it alone) or is fundamentally broken (the high patch rate is a signal for a manual review flag, not a cron-driven fix). In the latter case, autoresearch surfaces it in the nightly digest as "Skill X: 7 in-session patches in 7 days — manual review recommended."

**Q: Should users run `hermes skills rollback` on autoresearch patches?**

Yes, the rollback command works on any entry in `SKILL_HISTORY.md` regardless of source. The `[autoresearch]` tag is purely informational. If the user disagrees with an autoresearch patch, rollback restores the previous state and records the rollback as a new `[manual-rollback]` entry. The autoresearch loop will see the rollback in the history and treat it as a signal that its patch was wrong — it will not re-apply the same patch.

---

## Related Files

| File | Role |
|------|------|
| `tools/skill_manager_tool.py` | `_append_skill_history()` — needs `source` parameter added |
| `docs/ideas/autoresearch-loop.md` | Full autoresearch architecture |
| `docs/plans/autoresearch-mvp-plan.md` | Staged implementation plan |
| `docs/analysis/session-2026-04-14.md` | Session notes including original discussion |
| `docs/analysis/self-improvement-deep-dive.md` | Code-level analysis of all self-improvement claims |
