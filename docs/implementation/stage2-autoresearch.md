# Stage 2 Autoresearch (Current)

**Stage purpose:** Detect anomalies, generate hypotheses, run composite evaluation, and queue patch/memory proposals.  
**Writes:** `pending_patches.json`, `pending_memory_updates.json`, evaluation/holdout/memory rows in `skill_metrics.db`.  
**Does not write:** live skill files.

---

## 1) What Stage 2 Does Now

`run_stage2(...)` performs two coupled pipelines:

1. **Skill patch pipeline**
   - detect anomaly classes,
   - generate patch hypotheses,
   - evaluate with self-play + holdout + rubric + optional dual-judge,
   - write candidate outcomes to `pending_patches.json`.
2. **Memory update pipeline (built-in memory only)**
   - detect contradiction-driven stale memory signals,
   - generate constrained proposals (`replace|remove`),
   - write proposals to `pending_memory_updates.json`,
   - persist proposals to `autoresearch_memory_updates`.

---

## 2) Current Public Contract

Entrypoint:

- `cron/autoresearch/__init__.py::run_stage2(...)`

Key parameters currently supported:

- `judge_llm_call: Optional[LlmCall] = None`
- `enable_holdout_eval: bool = True`
- `holdout_days: int = 30`
- `holdout_tasks_per_skill: int = 20`
- `enable_memory_updates: bool = True`
- `memory_min_confidence: float = 0.7`
- `memory_min_evidence: int = 2`
- `memory_min_evidence_score: float = 1.25`

---

## 3) Skill-Patch Evaluation Behavior

Anomaly detector now emits deterministic, mutually exclusive classes:

- `UNDERPERFORMING`
- `STRUCTURALLY_BROKEN`
- `MISSING_COVERAGE`

Evaluation output status is:

- `accepted`
- `rejected`
- `hold` (dual-judge disagreement)

`evaluate_candidate(...)` preserves legacy core fields and can add:

- holdout deltas/pass flags,
- rubric pass-rate fields,
- dual-judge disagreement signal,
- primary/secondary quality deltas.

`pending_patches.json` includes those extended fields when present.

---

## 4) Memory Proposal Behavior

Memory contradiction detection is weighted by:

- recency,
- session source,
- contradiction strength,
- lexical overlap.

Ambiguous snippets are suppressed before scoring (hedging/uncertain/low-specificity patterns).

Only validated proposals are queued:

- action constrained to `replace|remove`,
- confidence and evidence gates enforced,
- unsupported/weak/ambiguous proposals dropped.

---

## 5) Persistence and Traceability

Stage 2 now records operational artifacts in `skill_metrics.db`:

- `autoresearch_holdout_cases`
- `autoresearch_eval_runs`
- `autoresearch_memory_updates` (with `evidence_score`)

This supports later KPI reporting and post-hoc adjudication without schema redesign.

---

## 6) Tests Covering Stage 2 Contracts

Primary files:

- `tests/cron/test_anomaly_detector.py`
- `tests/cron/test_self_play_evaluator.py`
- `tests/cron/test_autoresearch_stage2.py`
- `tests/cron/test_skill_metrics.py`
- `tests/cron/test_memory_anomaly_detector.py`
- `tests/cron/test_memory_hypothesis_generator.py`
- `tests/cron/test_pending_memory_updates.py`
- `tests/integration/test_autoresearch_e2e.py`
- `tests/integration/test_autoresearch_memory_e2e.py`

For current run receipts and interpretation, see:

- `docs/analysis/autoresearch-measurement-fidelity-test-report.md`

---

## 7) Notes on Historical Docs

Older Stage 2 documentation described only `UNDERPERFORMING` anomaly handling and self-play-only gating. Current runtime behavior includes expanded anomaly coverage, holdout/rubric/dual-judge evaluation, and memory proposal generation.

