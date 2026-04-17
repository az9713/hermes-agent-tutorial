# Autoresearch Scheduling, Runner, CLI, and Delivery (Current)

This document describes how the autoresearch loop is scheduled, executed, surfaced to operators, and optionally delivered to messaging platforms in the current codebase.

---

## 1) Runner Contract (`cron/autoresearch/runner.py`)

`run_full_loop(...)` is the orchestration entrypoint used by scheduler and CLI.

Current signature includes pass-through controls for upgraded Stage 2 and Stage 3 behavior, including:

- `judge_llm_call`
- `enable_holdout_eval`
- `holdout_days`
- `holdout_tasks_per_skill`
- `enable_memory_updates`
- `run_memory_apply`
- `pending_memory_updates_path`
- `dry_run`

Behavioral guarantees:

- Stage failures are isolated and persisted in run state.
- Stage 2 import/config absence can be skipped without crashing loop execution.
- `state.json` is written atomically after each run.

---

## 2) Scheduler Tick (`cron/scheduler.py`)

`_tick_autoresearch()` is evaluated each scheduler beat and decides whether to run the loop from `config.autoresearch` + run-state history.

Scheduler responsibilities:

- honor `enabled` and cron schedule fields,
- invoke `run_full_loop(...)` when due,
- optionally deliver digest to configured platforms,
- avoid hard failures propagating into scheduler loop.

---

## 3) CLI Surface (`hermes_cli/autoresearch.py`)

Supported subcommands:

- `hermes autoresearch run [--dry-run]`
- `hermes autoresearch status`
- `hermes autoresearch schedule <expr>`
- `hermes autoresearch patches`
- `hermes autoresearch enable`
- `hermes autoresearch disable`

Current status output now includes:

- run-state summary,
- memory queue/outcome counters,
- operator confidence KPIs (default 30-day window).

`patches` output includes both skill patch queue and memory update queue/outcomes.

---

## 4) Config and Runtime Artifacts

Config keys (from `DEFAULT_CONFIG["autoresearch"]`):

- `enabled`
- `schedule`
- `dry_run`
- `deliver`

Runtime files under `HERMES_HOME/autoresearch/`:

- `state.json`
- `skill_metrics.db`
- `pending_patches.json`
- `pending_memory_updates.json`
- `nightly_digest.md`

All default paths are profile-safe through `get_hermes_home()`.

---

## 5) Delivery (`deliver_digest`)

Delivery remains platform-config driven and tolerant of per-platform failure:

- each configured target is attempted,
- errors are captured per platform,
- one platform failure does not abort loop completion.

---

## 6) Tests Covering This Layer

Primary files:

- `tests/cron/test_runner.py`
- `tests/cron/test_autoresearch_tick.py`
- `tests/hermes_cli/test_autoresearch_cli.py`

For latest execution receipts and interpretation, see:

- `docs/analysis/autoresearch-measurement-fidelity-test-report.md`

