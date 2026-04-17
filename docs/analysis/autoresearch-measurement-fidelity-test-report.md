# Autoresearch Measurement-Fidelity: Test Validation Report

**Date:** 2026-04-17  
**Environment:** Windows (PowerShell), Python 3.13.5  
**Scope:** Improved autoresearch loop (Stage 1/2/3 + memory hardening + operator KPI reporting)

---

## 1) What We Tested

We validated the upgraded contracts and behavior in four layers:

1. Deterministic unit and component tests (signal extraction, anomaly classification, evaluation gate, memory pipeline, DB metrics, digest rendering).
2. Stage orchestration tests (Stage 2 and Stage 3 behavior, status transitions, artifact writing, pass-through flags).
3. CLI/reporting tests (`hermes autoresearch status/patches` output contracts).
4. End-to-end integration tests (full skill patch flow and two-phase memory flow).

Primary suite executed:

```powershell
python -m pytest -o addopts='' tests/cron/test_signal_extractor.py tests/cron/test_anomaly_detector.py tests/cron/test_self_play_evaluator.py tests/cron/test_skill_metrics.py tests/cron/test_digest.py tests/cron/test_autoresearch_stage2.py tests/cron/test_autoresearch_stage3.py tests/cron/test_autoresearch_stage3_memory.py tests/cron/test_memory_anomaly_detector.py tests/cron/test_memory_hypothesis_generator.py tests/cron/test_memory_updater.py tests/cron/test_pending_memory_updates.py tests/cron/test_runner.py tests/hermes_cli/test_autoresearch_cli.py tests/integration/test_autoresearch_e2e.py tests/integration/test_autoresearch_memory_e2e.py -q
```

---

## 2) Test Inventory (What These Tests Are)

### Signal quality and attribution

- `tests/cron/test_signal_extractor.py`
- Verifies correction detection, completion detection, skill detection, and extracted signal schema.
- Includes graceful degradation cases (missing `state.db`, empty windows).

### Anomaly coverage and precedence

- `tests/cron/test_anomaly_detector.py`
- Verifies deterministic classification for:
  - `UNDERPERFORMING`
  - `STRUCTURALLY_BROKEN`
  - `MISSING_COVERAGE`
- Verifies rolling-window filtering, minimum-invocation gating, and aggregate ordering behavior.

### Evaluation validity (self-play + holdout + dual judge + rubric)

- `tests/cron/test_self_play_evaluator.py`
- Verifies accept/reject/hold decisions, token and quality deltas, dual-judge disagreement hold path, and holdout quality gating.

### DB schema and metric contracts

- `tests/cron/test_skill_metrics.py`
- Verifies table creation/migration behavior, session signal persistence, skill-health aggregation, holdout-case storage, eval-run persistence, memory update lifecycle, and operator-confidence KPI math.

### Stage orchestration and artifacts

- `tests/cron/test_autoresearch_stage2.py`
- `tests/cron/test_autoresearch_stage3.py`
- `tests/cron/test_autoresearch_stage3_memory.py`
- Verifies stage outputs, skip/disabled paths, mutation guards, and memory lifecycle transitions:
  - `proposed -> pending_revalidation -> applied|discarded|needs_review|failed`

### Memory contradiction hardening

- `tests/cron/test_memory_anomaly_detector.py`
- `tests/cron/test_memory_hypothesis_generator.py`
- `tests/cron/test_memory_updater.py`
- `tests/cron/test_pending_memory_updates.py`
- Verifies weighted evidence gating, ambiguity suppression, proposal normalization/validation, and apply adapter error mapping.

### Digest and CLI operator visibility

- `tests/cron/test_digest.py`
- `tests/hermes_cli/test_autoresearch_cli.py`
- Verifies `Operator confidence` digest section, memory sections, and CLI status KPI/memory queue blocks.

### End-to-end behavior

- `tests/integration/test_autoresearch_e2e.py`
- `tests/integration/test_autoresearch_memory_e2e.py`
- Verifies full loop behavior for skill patching and memory two-phase flow, including mixed skill+memory runs and profile-path behavior.

Collected test count for this targeted suite: **215 tests**.

---

## 2.1 Explicitly Not Tested in This Validation Pass

The following were intentionally not validated in the executed test commands above:

- **External memory provider mutation paths**
  - Out of scope for this feature version (built-in file memory only).
- **Production traffic calibration quality**
  - No live-traffic replay corpus or human adjudication labels were used in this pass.
- **Long-horizon effectiveness**
  - No longitudinal measurement (for example, multi-week correction-rate reduction) was run.
- **Load/stress/soak behavior**
  - No high-volume or prolonged runtime performance tests were executed.
- **Real platform delivery I/O**
  - Messaging delivery behavior in runner tests is mock-driven; no live Slack/Telegram sends were validated.
- **Scheduler wall-clock runtime behavior**
  - `tests/cron/test_autoresearch_tick.py`, `tests/cron/test_scheduler.py`, and `tests/cron/test_jobs.py` were not part of the targeted 215-test command.
- **Full cross-platform permission semantics in this environment**
  - POSIX-mode assertions (`0700`/`0600`) are known to fail on Windows and were treated as environment-specific noise.
- **Full repository regression**
  - This pass focused on autoresearch-adjacent and cron surfaces, not the entire repository test matrix.

Impact:

- The pass provides strong correctness evidence for implemented autoresearch logic and contracts.
- It does **not** constitute complete production-readiness proof across scale, all platforms, and long-term outcome quality.

---

## 3) Results

## 3.1 Targeted upgrade suite

Result:

- **215 passed**, **1 warning**, **0 failed**
- Runtime: ~4.29s

Interpretation:

- The upgraded contracts and behaviors are internally consistent across unit, integration, CLI, and E2E coverage for the scoped autoresearch improvements.

## 3.2 Broad cron regression suite

Command:

```powershell
python -m pytest -o addopts='' tests/cron -q
```

Result:

- **484 passed**, **6 skipped**, **6 failed**, **1 warning**
- All 6 failures were in `tests/cron/test_file_permissions.py`.

Failure set:

- `test_ensure_dirs_sets_0700`
- `test_save_job_output_sets_0600`
- `test_save_jobs_sets_0600`
- `test_ensure_hermes_home_sets_0700`
- `test_save_config_sets_0600`
- `test_save_env_value_sets_0600`

Interpretation:

- These failures are expected on Windows when tests assert POSIX mode bits (`0700`/`0600`) exactly.
- They are environmental compatibility noise, not evidence of autoresearch logic regression.

## 3.3 Broad cron suite excluding known POSIX-mode tests

Command:

```powershell
python -m pytest -o addopts='' tests/cron -k "not file_permissions" -q
```

Result:

- **482 passed**, **6 skipped**, **8 deselected**, **0 failed**, **1 warning**

Interpretation:

- With known cross-platform permission assertions excluded, the cron regression surface is green.

---

## 4) How to Interpret These Results

## 4.1 What the results prove

- Core measurement-fidelity logic works as designed in code:
  - richer Stage 1 signals,
  - multi-class anomaly detection,
  - composite Stage 2 evaluation (`accepted|rejected|hold`),
  - hardened memory contradiction/proposal/apply flow,
  - operator KPI reporting in digest + CLI.
- New DB contracts are functioning and backward-compatible in tested paths.
- Stage wiring and artifacts (`pending_patches.json`, memory queue artifacts, digest/status outputs) are stable under seeded scenarios.

## 4.2 What the results do **not** prove

- They do not prove long-term production lift (fewer user corrections over weeks).
- They do not prove threshold optimality for real traffic.
- They do not provide human-labeled precision/recall for anomaly attribution or memory proposal quality.

These tests are strong implementation validation, not full production-effectiveness validation.

---

## 5) What This Says About Autoresearch Quality

Overall quality status: **high engineering confidence for correctness and safety in the implemented scope**.

Why:

- Broad deterministic coverage on critical decision points and status transitions.
- End-to-end tests confirm skill and memory loops coexist correctly.
- Safety behavior is explicitly tested (gating, hold paths, non-destructive review states).
- Operator visibility exists and is test-covered (digest + CLI KPIs).

Remaining quality risk is mostly **calibration risk**, not implementation risk:

- thresholds/weighting may need tuning on real-world traffic,
- attribution confidence may drift with different usage patterns,
- holdout/rubric strictness may require per-domain adjustment.

---

## 6) Recommended Ongoing Validation

For each release containing autoresearch changes:

1. Run the targeted 215-test suite (contract gate).
2. Run `tests/cron -k "not file_permissions"` on Windows CI.
3. Run full `tests/cron` on Linux CI to keep POSIX permission assertions meaningful.
4. Track operator KPIs (30-day windows) after deploy for calibration decisions:
   - patch stability ratio
   - acceptance-to-regression ratio
   - memory precision proxy
   - holdout pass rate
