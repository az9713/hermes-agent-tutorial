# Stage 3 Autoresearch (Current)

**Stage purpose:** Apply safe improvements, run regression safeguards, process due memory updates, and publish operator digest.  
**Writes:** skill files (when allowed), memory files (two-phase due updates), patch/memory status rows, nightly digest.  
**Safety mode:** `dry_run=True` disables file mutation.

---

## 1) What Stage 3 Does Now

`run_stage3(...)` orchestrates four tasks:

1. Read Stage 2 patch queue (`pending_patches.json`).
2. Apply accepted skill patches through safety guards (recency lock + stale guard + atomic writes).
3. Run regression watch for applied patches (optional).
4. Process due memory updates (`proposed/pending_revalidation`) with revalidation before apply.
5. Compute operator confidence KPIs and generate `nightly_digest.md`.

---

## 2) Current Public Contract

Entrypoint:

- `cron/autoresearch/__init__.py::run_stage3(...)`

Key parameters:

- `dry_run: bool = False`
- `run_regression_watch: bool = True`
- `run_memory_apply: bool = True`

When `dry_run=True`, skill and memory mutation paths are suppressed while digest/reporting still runs.

---

## 3) Skill Apply + Regression Safeguards

Skill patch application behavior:

- accepts only Stage 2 candidates marked `accepted`,
- refuses stale `old_string` replacements,
- applies file writes atomically,
- records patch metadata to `autoresearch_patches`.

Regression watch behavior:

- evaluates applied patches against baseline signals,
- marks `stable` / `rolled_back` / `needs_review`,
- avoids destructive rollback when causation is ambiguous.

---

## 4) Memory Two-Phase Apply (Current)

Memory updates are not applied immediately in Stage 2.

Stage 3 memory flow:

1. Load due open proposals (`proposed`, `pending_revalidation`) where `apply_after <= now`.
2. Re-run memory anomaly detection for fresh support.
3. Transition status:
   - `proposed -> pending_revalidation -> applied|discarded|needs_review|failed`
4. Apply via built-in `memory_tool` only.
5. Route ambiguous/no-match outcomes to `needs_review` (non-destructive path).

Scope remains built-in file memory (`MEMORY.md`, `USER.md`) only.

---

## 5) Digest and Operator Confidence

Digest generator now includes:

- patch sections (applied/deferred/rejected/regression),
- memory sections (`Proposed memory`, `Applied memory`, `Needs review`),
- `Operator confidence` KPI section.

KPI block is computed from `skill_metrics.db` (30-day default window):

- patch stability ratio,
- acceptance-to-regression ratio,
- memory precision proxy,
- holdout pass rate.

---

## 6) Tests Covering Stage 3 Contracts

Primary files:

- `tests/cron/test_autoresearch_stage3.py`
- `tests/cron/test_autoresearch_stage3_memory.py`
- `tests/cron/test_memory_updater.py`
- `tests/cron/test_digest.py`
- `tests/cron/test_runner.py`
- `tests/integration/test_autoresearch_memory_e2e.py`

For current validation receipts and interpretation, see:

- `docs/analysis/autoresearch-measurement-fidelity-test-report.md`

---

## 7) Notes on Historical Docs

Earlier Stage 3 docs predated memory two-phase apply and operator KPI reporting. This file reflects current runtime behavior in the codebase.

