# Stage 3 Autoresearch — Implementation Document

> **Stage: Apply + Recover.**
> Stage 3 reads `pending_patches.json`, applies accepted patches to skill files,
> runs a regression watch the following night, and writes `nightly_digest.md`.
> This is the first stage that modifies `HERMES_HOME/skills/`.

---

## 1. What Stage 3 Does

Stage 3 is the apply-and-recover half of the autoresearch loop. Each time it runs (nightly, after Stage 2), it:

1. Reads `pending_patches.json` (written by Stage 2).
2. For each accepted patch: checks the recency lock, verifies `old_string` is still present, applies the patch to `SKILL.md` atomically, appends a `[autoresearch]` entry to `SKILL_HISTORY.md`, and records the patch in `autoresearch_patches`.
3. Runs the regression watch: for each previously applied patch, compares the current correction_rate to the baseline. If it rose more than 15% and autoresearch was the sole writer since the patch, rolls back automatically.
4. Generates and writes `nightly_digest.md`.

This is the only stage that writes to `HERMES_HOME/skills/`. All three safety mechanisms (recency lock, stale-patch guard, regression watch) are active at all times. A `dry_run=True` flag disables all writes to skills — useful for testing or operator review.

---

## 2. File Map

```
cron/autoresearch/
├── __init__.py              — MODIFIED: run_stage3() entry point added
├── applier.py               — NEW: apply accepted patches with safety guards
├── regression_watch.py      — NEW: post-patch metric check, auto-rollback
└── digest.py                — NEW: format nightly_digest.md

~/.hermes/autoresearch/      (runtime, not in repo)
├── pending_patches.json     — read by Stage 3 (written by Stage 2)
└── nightly_digest.md        — written by Stage 3
```

### Schema change in `skill_metrics.py`:

`autoresearch_patches` gained two new columns:
```sql
old_string   TEXT,   -- the text that was replaced (needed for rollback)
new_string   TEXT    -- the replacement text (used to verify rollback target)
```

Three new DB functions added:
- `record_autoresearch_patch()` — now accepts `old_string` / `new_string`
- `get_applied_patches(since_ts)` — reads rows with `status='applied'`
- `update_patch_status(patch_id, status)` — updates `status` after watch decision

### New test files:

```
tests/cron/
├── test_applier.py              — applier unit tests (27 tests)
├── test_regression_watch.py     — regression watch unit tests (20 tests)
├── test_digest.py               — digest unit tests (24 tests)
└── test_autoresearch_stage3.py  — end-to-end integration tests (11 tests)
```

---

## 3. Components

### 3.1 Applier

**File:** `cron/autoresearch/applier.py`

Processes each entry from `pending_patches.json` where `accepted=True`. For every accepted patch, the applier runs four checks before writing anything, then writes atomically.

**Check 1 — SKILL.md exists:**
If `hermes_home/skills/<skill_name>/SKILL.md` is missing, the patch is marked `failed`. This can happen if a skill was deleted after Stage 2 ran.

**Check 2 — Recency lock:**
Scans `SKILL_HISTORY.md` with a compiled regex for `[in-session]` header entries:
```python
HISTORY_HEADER_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — (\w+) \[(.+)\]$"
)
```
If the most recent `[in-session]` entry is within `RECENCY_LOCK_HOURS = 24`, the patch is `deferred`. Only `[in-session]` entries count — `[autoresearch]` and `[autoresearch: regression-watch]` entries do not trigger the lock.

**Check 3 — `old_string` still present:**
If `old_string` is no longer in the current `SKILL.md` (e.g. the user manually edited it since Stage 2 ran), the patch is marked `failed` as stale. A stale patch applied via blind `str.replace` would silently no-op, which is worse than an explicit failure.

**Check 4 — `dry_run`:**
If `dry_run=True`, the applier returns `status="dry_run"` without touching any files or the DB.

**On success:**
1. `SKILL.md` updated atomically via `tempfile + os.replace`.
2. `SKILL_HISTORY.md` appended with `[autoresearch]` source entry.
3. Row inserted into `autoresearch_patches` with baselines for regression watch.

**ApplyResult shape:**
```python
{
    "skill_name":  "git-workflow",
    "status":      "applied",    # "applied" | "deferred" | "failed" | "dry_run"
    "reason":      "Clarify naming convention",
    "old_string":  "...",
    "new_string":  "...",
}
```

---

### 3.2 Regression Watch

**File:** `cron/autoresearch/regression_watch.py`

Runs after apply. For each row in `autoresearch_patches` with `status='applied'`, checks whether the patch made things better or worse.

**Decision tree per patch:**

```
1. In-session patches since the autoresearch patch?
   YES → causation ambiguous → status = "needs_review" (DO NOT rollback)
   NO  → check metrics
2. correction_rate delta > ROLLBACK_THRESHOLD (0.15)?
   YES → rollback → status = "rolled_back"
   NO  → status = "stable"
```

**Causation check** (`_count_in_session_patches_since`):
Scans `SKILL_HISTORY.md` for `[in-session]` entries written strictly after the `patch_applied_at` timestamp. If any exist, it's impossible to know whether the metric change was caused by the autoresearch patch or the subsequent in-session edits. Manual review is required.

**Rollback** (`_rollback_skill`):
Applies the inverse patch: replaces `new_string` with `old_string` in the current `SKILL.md`. Appends a `[autoresearch: regression-watch]` entry to `SKILL_HISTORY.md`. If `new_string` is no longer in the file (already manually fixed), rollback gracefully returns `False` and the status becomes `needs_review` instead of crashing.

**`patches_since_ts` parameter:**
Allows Stage 3 to pass yesterday's run timestamp so only last-night's patches are examined, not the full history.

**WatchResult shape:**
```python
{
    "skill_name":             "git-workflow",
    "patch_id":               42,
    "status":                 "rolled_back",  # "stable" | "rolled_back" | "needs_review"
    "correction_rate_delta":  0.30,
    "reason":                 "autoresearch regression-watch: correction_rate rose +30%...",
}
```

---

### 3.3 Digest

**File:** `cron/autoresearch/digest.py`
**Output:** `~/.hermes/autoresearch/nightly_digest.md`

Formats five sections from the Stage 3 data:

| Section | Source | Fallback if empty |
|---------|--------|-------------------|
| Applied | `apply_results` where `status="applied"` | "_No patches applied this cycle._" |
| Deferred (recency lock) | `apply_results` where `status="deferred"` | "_No patches deferred._" |
| Rejected by self-play | `pending_patches` where `status="rejected"` | "_No patches rejected by self-play._" |
| Regression watch | `watch_results` | "_No patches under regression watch._" |
| Needs your attention | `apply_results` failed + `watch_results` needs_review | "_Nothing needs your attention._" |

**Regression watch symbols:**
- `✓` stable
- `↩` rolled_back
- `⚠` needs_review

These symbols make the digest scannable at a glance without reading every line.

**Sample digest:**
```markdown
# Hermes Autoresearch — Nightly Digest 2026-04-15

## Applied
- **git-workflow**: Clarify branch naming convention

## Deferred (recency lock)
- **web-search**: in-session patch within 24h

## Rejected by self-play
- **code-review**: token_delta=+5%, quality_delta=-0.8 — token_delta >= 0

## Regression watch
- ✓ **git-workflow**: correction_rate stable (delta=+2%)

## Needs your attention
_Nothing needs your attention._
```

---

### 3.4 Entry Point

**File:** `cron/autoresearch/__init__.py`

`run_stage3()` orchestrates the pipeline in 4 steps:

```python
def run_stage3(metrics_db_path, patches_path, digest_path, hermes_home,
               dry_run=False, run_regression_watch=True):
    # 1. Read pending_patches.json
    patches = read_pending_patches(path=patches_path)

    # 2. Apply accepted patches
    apply_results = apply_patches(patches, metrics_conn, hermes_home, dry_run)

    # 3. Regression watch (optional)
    watch_results = check_regressions(metrics_conn, hermes_home) if run_regression_watch else []

    # 4. Generate digest
    return generate_digest(apply_results, watch_results, patches, report_path=digest_path)
```

**`dry_run` flag:** Passed through to `apply_patches()`. When `True`, no SKILL.md files are written, no SKILL_HISTORY.md entries are appended, and no DB rows are inserted. The digest is still generated and shows "No patches applied."

**`run_regression_watch=False`:** Useful on the very first run after Stage 3 is deployed — there are no previously applied autoresearch patches in the DB yet, so the watch would silently return `[]`. Saves one DB scan.

---

## 4. Tests

### 4.1 Test Philosophy

Same as Stages 1 and 2: real SQLite in `tmp_path`, no mocks, filesystem operations on real files. The applier tests additionally verify file-level invariants (mtime, content equality) to prove nothing was written when it shouldn't be.

### 4.2 `test_applier.py` (27 tests)

| Class | What it checks |
|-------|---------------|
| `TestSuccessfulApply` | SKILL.md updated; unrelated lines untouched; status="applied"; `[autoresearch]` in history; history entry contains old/new string; history header parseable by `HISTORY_HEADER_RE`; DB row written; DB row has correct baselines |
| `TestRecencyLock` | Recent in-session patch → deferred; deferred SKILL.md unchanged; old in-session patch (>24h) → applied; no history file → no lock; `[autoresearch]` entries don't trigger lock; `[autoresearch: regression-watch]` entries don't trigger lock |
| `TestFailureCases` | Missing SKILL.md → "failed"; stale old_string → "failed"; stale patch doesn't modify file |
| `TestDryRun` | SKILL.md unchanged; no history entry; no DB row; status="dry_run" |
| `TestNonAccepted` | Rejected patch ignored; mixed list only processes accepted |
| `TestLastInSessionPatchTs` | No history file → None; in-session entry detected; only autoresearch entries → None; returns most recent of multiple entries |

**Key regression: `[autoresearch]` does not trigger lock.**
Without this, the regression watch's own rollback entry (tagged `[autoresearch: regression-watch]`) would lock the skill for 24h after every rollback, preventing Stage 2's next accepted patch from being applied. Both source variants are tested explicitly.

### 4.3 `test_regression_watch.py` (20 tests)

| Class | What it checks |
|-------|---------------|
| `TestRollbackTriggered` | Bad delta → rollback; SKILL.md restored to old_string; `[autoresearch: regression-watch]` in history; DB status = "rolled_back"; delta value in result |
| `TestStable` | Small delta → stable; stable SKILL.md unchanged; improved metrics → stable; DB status = "stable" |
| `TestNeedsReview` | In-session patch since autoresearch → needs_review; needs_review SKILL.md not rolled back; `[autoresearch]` entries post-patch don't count |
| `TestEdgeCases` | No applied patches → empty list; no health data → silently skipped; rollback graceful when new_string gone; `patches_since_ts` filter works |
| `TestCountInSessionPatchesSince` | No history → 0; counts entries after cutoff; ignores entries before cutoff; ignores `[autoresearch]` entries |

**Key invariant: causation before rollback.**
The test `test_needs_review_skill_md_not_rolled_back` verifies that when in-session patches exist since the autoresearch patch, the rollback is suppressed even when metrics worsened significantly. Blind rollback in this case would undo the user's manual fix.

### 4.4 `test_digest.py` (24 tests)

| Class | What it checks |
|-------|---------------|
| `TestHeader` | Date in header; "Hermes Autoresearch" in header |
| `TestAppliedSection` | Applied skill in Applied section; reason included; empty fallback |
| `TestDeferredSection` | Deferred skill in Deferred section; applied skill not in deferred; empty fallback |
| `TestRejectedSection` | Rejected patch in section; token_delta formatted as %; accepted not in rejected; empty fallback |
| `TestRegressionWatchSection` | Stable shows ✓; rolled_back shows ↩; needs_review shows ⚠; skill name present; empty fallback |
| `TestNeedsAttention` | needs_review in attention section; failed apply in attention section; nothing needing attention fallback; stable not in attention section |
| `TestFileOutput` | File written to disk; returned text matches file; parent dirs created |

### 4.5 `test_autoresearch_stage3.py` (11 tests)

| Test | What it checks |
|------|---------------|
| `test_empty_patches_no_crash` | Empty pending_patches.json → no crash, valid digest |
| `test_digest_always_written` | Digest file exists after any run |
| `test_digest_text_returned` | run_stage3() returns a non-empty string |
| `test_accepted_patch_applied_to_skill_md` | NEW_STRING appears in SKILL.md after apply |
| `test_applied_patch_has_autoresearch_history_entry` | `[autoresearch]` in SKILL_HISTORY.md |
| `test_recency_locked_patch_deferred` | SKILL.md unchanged when recent in-session patch present |
| `test_deferred_appears_in_digest` | Digest "Deferred" section contains skill name |
| `test_dry_run_skill_md_unchanged` | SKILL.md unchanged with dry_run=True |
| `test_dry_run_digest_says_no_patches_applied` | Digest says "No patches applied" in dry_run |
| `test_regression_watch_skipped_when_disabled` | "No patches under regression watch" when disabled |
| `test_regression_rollback_appears_in_digest` | Full cycle: apply → worsen metrics → rollback in next run's digest |

---

## 5. Test Receipt

```
Platform: Windows 11, Python 3.13.5, pytest 8.4.2
Date: 2026-04-15
Command: pytest tests/cron/test_skill_manager_source_tag.py
                tests/cron/test_skill_metrics.py
                tests/cron/test_signal_extractor.py
                tests/cron/test_reporter.py
                tests/cron/test_autoresearch_stage1.py
                tests/cron/test_anomaly_detector.py
                tests/cron/test_hypothesis_generator.py
                tests/cron/test_self_play_evaluator.py
                tests/cron/test_pending_patches.py
                tests/cron/test_autoresearch_stage2.py
                tests/cron/test_applier.py
                tests/cron/test_regression_watch.py
                tests/cron/test_digest.py
                tests/cron/test_autoresearch_stage3.py
                -q --override-ini="addopts="

Result: 256 passed, 0 failed, 1 warning (3.34s)
```

Breakdown by stage:
- Stage 1 (93 tests): all pass, unchanged
- Stage 2 (81 tests): all pass, unchanged
- Stage 3 new tests (82 tests): 27 + 20 + 24 + 11 = 82

---

## 6. Verification Criteria

Stage 3 is complete when:

- [x] Recency lock correctly skips skills patched in-session within 24h
- [x] Applied patches appear in SKILL_HISTORY.md with `[autoresearch]` tag
- [x] Applied patches recorded in `autoresearch_patches` table with old_string/new_string
- [x] Regression watch does NOT roll back when in-session patches occurred since autoresearch patch
- [x] Regression watch DOES roll back when autoresearch was sole writer and correction_rate rose >15%
- [x] Rollback entries appear in SKILL_HISTORY.md as `[autoresearch: regression-watch]`
- [x] dry_run=True leaves all skill files unchanged
- [x] nightly_digest.md written with all five sections
- [x] 256/256 tests pass (Stages 1 + 2 + 3 combined)

---

## 7. Design Decisions

### dry_run First

The `dry_run` flag was added from the start rather than retrofitted. The cost is one extra parameter and one branch per file operation. The benefit is that operators can verify Stage 3's behaviour on real data before enabling auto-apply. Running `run_stage3(dry_run=True)` in a cron job for a few nights, then reading the digest, gives confidence that the acceptance logic is working as expected before any skill files are touched.

### Recency Lock is Source-Aware

The lock only triggers on `[in-session]` entries, not on `[autoresearch]` or `[autoresearch: regression-watch]`. This is deliberate:

- If an autoresearch patch triggered a rollback last night, we don't want to lock the skill for another 24h — Stage 2 might have a better hypothesis ready.
- If Stage 3 applied a patch earlier tonight, we don't want the lock to block a second patch in the same cycle (though that's unlikely in practice).

Naively locking on any recent SKILL_HISTORY.md entry would create a self-locking loop where every autoresearch write blocks the next one.

### old_string/new_string in the DB

The regression watch needs `old_string` to perform a rollback. The original plan said "restore from SKILL_HISTORY.md", but parsing multi-line code blocks from Markdown reliably is fragile. Storing the strings in `autoresearch_patches` is one extra INSERT column — a much safer approach.

This is a schema change from Stage 2. Existing `autoresearch_patches` DBs won't have the new columns (SQLite ignores columns not in the `CREATE TABLE IF NOT EXISTS` schema). In practice this only matters during the transition night — any pre-Stage-3 rows simply can't be rolled back (they have `old_string=None`), which is acceptable since Stage 3 wasn't deployed when those patches were applied.

### Regression Watch Causation Check

The rule "only auto-rollback if autoresearch was the sole writer" is the most subtle safety guard in Stage 3. Without it, consider this scenario:

1. Autoresearch patches `git-workflow` at 3am.
2. User uses `git-workflow` all day. Correction rate rises to 0.60 (bad day).
3. User opens a session and manually patches `git-workflow` to fix it.
4. Regression watch runs at 3am the next night. correction_rate = 0.60, delta = +19% > threshold.
5. Without the causation check: regression watch rolls back the autoresearch patch AND the user's manual fix (because it applies `new_string → old_string` blindly).
6. With the causation check: watches sees the in-session patch, marks `needs_review`, and does nothing.

The in-session check prevents the watch from destroying user work.

---

## 8. Full Autoresearch Loop: What's Now Built

All three stages are implemented. The complete nightly cycle is:

```
Stage 1 (Observe, 00:00):
  extract_signals → record_session_signal → compute_skill_health → nightly_report.md

Stage 2 (Hypothesize + Evaluate, 01:00):
  detect_anomalies → generate_hypothesis → evaluate_candidate → pending_patches.json

Stage 3 (Apply + Recover, 02:00):
  apply_patches (recency lock + stale guard + dry_run)
    → SKILL.md updated atomically
    → SKILL_HISTORY.md tagged [autoresearch]
    → autoresearch_patches DB row
  check_regressions (causation check + rollback threshold)
    → SKILL.md restored if regression
    → SKILL_HISTORY.md tagged [autoresearch: regression-watch]
  generate_digest → nightly_digest.md
```

Total: 256 tests across 14 test files, all passing.
