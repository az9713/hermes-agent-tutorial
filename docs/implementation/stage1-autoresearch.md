# Stage 1 Autoresearch (Current)

**Stage purpose:** Observe and label behavior only.  
**Writes:** `skill_metrics.db` aggregates and nightly report artifacts.  
**Does not write:** `HERMES_HOME/skills/`.

---

## 1) What Stage 1 Does Now

`run_stage1(...)` extracts session signals from `state.db`, persists them to `skill_metrics.db`, computes daily `skill_health`, and writes the nightly report.

Current signal extraction is richer than the initial Stage 1 release and includes:

- correction labels (`explicit_wrong`, `retry_request`, etc.),
- correction intensity score,
- completion confidence score,
- session source (`sessions.source`),
- per-skill causal attribution map,
- memory attribution overlap map.

This means Stage 2 is no longer fed only by basic correction/completion counts.

---

## 2) Current Runtime Contract

Entrypoint:

- `cron/autoresearch/__init__.py::run_stage1(...)`

Main behavior:

1. `extract_signals(...)` reads recent sessions from `state.db`.
2. New sessions are inserted into `session_signals` (idempotent on `session_id`).
3. `compute_and_store_skill_health(...)` writes daily skill aggregates.
4. `get_skill_health_summary(...)` builds rolling summary (default 7 days).
5. `generate_report(...)` writes `nightly_report.md`.

Path resolution is profile-safe through `get_hermes_home()` and stage-level defaults derived from effective `hermes_home`.

---

## 3) Data Model (Current)

`skill_metrics.db` now includes expanded Stage 1-era schema used by later stages:

- `session_signals` (expanded fields):
  - `correction_snippets`, `correction_labels`, `correction_intensity`,
  - `completion_confidence`, `session_source`,
  - `skill_attribution`, `memory_attribution`.
- `skill_health` (expanded aggregates):
  - `avg_tool_calls`, `avg_correction_intensity`,
  - `avg_completion_confidence`, `avg_skill_causal_confidence`.
- additional tables used downstream by Stage 2/3:
  - `autoresearch_holdout_cases`,
  - `autoresearch_eval_runs`,
  - `autoresearch_memory_updates`.

Migrations are additive via `_ensure_columns(...)`, so older DBs are upgraded in place.

---

## 4) Tests Covering Stage 1 Contracts

Primary files:

- `tests/cron/test_signal_extractor.py`
- `tests/cron/test_skill_metrics.py`
- `tests/cron/test_autoresearch_stage2.py` (Stage 1-fed Stage 2 behavior)
- `tests/integration/test_autoresearch_e2e.py`

Key guarantees validated:

- missing/empty input DB paths degrade safely,
- signal extraction persists required enriched fields,
- aggregation remains idempotent and window-aware,
- schema migrations remain backward compatible.

For the latest consolidated validation receipt and interpretation, see:

- `docs/analysis/autoresearch-measurement-fidelity-test-report.md`

---

## 5) Notes on Historical Docs

Earlier versions of this document described the initial Stage 1 implementation before measurement-fidelity upgrades. This file now reflects current runtime behavior in the codebase.

