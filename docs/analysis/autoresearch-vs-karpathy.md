# Autoresearch: Hermes vs Karpathy

## Purpose
This document compares Hermes autoresearch with [karpathy/autoresearch](https://github.com/karpathy/autoresearch), then critiques how effective the Hermes approach is for real-world self-improvement.

It is written for readers with no prior context on either project.

## Scope and Sources
This comparison is grounded in code and docs, not assumptions.

- Karpathy reference repo:
  - Commit: `228791fb499afffb54b46200aca536f79142f117`
  - Files: `README.md`, `program.md`, `prepare.py`, `train.py`
- Hermes repo:
  - Commit: `44140eb8a9ff74c6018ab76a3270dbc5a22cb435`
  - Files: `cron/autoresearch/*`, `hermes_cli/autoresearch.py`, and autoresearch tests under `tests/cron`, `tests/integration`, `tests/hermes_cli`

---

## 1) What Karpathy's Autoresearch Is Optimizing

Karpathy's implementation is a tight autonomous research loop for one objective: improve model quality (`val_bpb`) under a fixed 5-minute train budget.

Core properties:
- Single mutable file (`train.py`) and one fixed evaluator (`prepare.py`).
- Agent loop is explicit in `program.md`: edit -> commit -> run -> parse metric -> keep or reset.
- Ground-truth metric is produced by training/eval, not by LLM judgment.
- Very low guardrails by design: maximize experiment velocity.

In short: it is an autonomous experiment runner over a measurable ML objective.

## 2) What Hermes Autoresearch Is Optimizing

Hermes autoresearch is a production operations loop for agent behavior quality and safety, not ML training quality.

Core properties:
- Three staged pipeline:
  - Stage 1 (`cron/autoresearch/signal_extractor.py`, `skill_metrics.py`): extract user-session signals and aggregate health metrics.
  - Stage 2 (`anomaly_detector.py`, `hypothesis_generator.py`, `self_play_evaluator.py`): propose and evaluate skill patches; also detect stale memory and propose memory updates.
  - Stage 3 (`applier.py`, `regression_watch.py`, `memory_updater.py`, `digest.py`): apply accepted changes with safeguards, monitor regressions, produce digest.
- Risk controls:
  - Recency lock, dry-run mode, stale-patch checks, atomic writes, rollback/watch.
  - Memory updates use two-phase apply (`proposed -> pending_revalidation -> applied|discarded|needs_review|failed`), with delay and revalidation.
- Persistent auditability:
  - SQLite tables (`autoresearch_patches`, `autoresearch_memory_updates`) and nightly digests.

In short: Hermes is a safety-first continuous improvement system for prompts/skills and built-in memory state.

---

## 3) Side-by-Side Comparison

| Dimension | Karpathy Autoresearch | Hermes Autoresearch |
|---|---|---|
| Primary objective | Minimize `val_bpb` | Improve skill + memory behavior while controlling risk |
| Evaluation signal | Direct training/eval metric | Proxy production signals + LLM self-play scoring |
| Mutation target | `train.py` only | Skill markdown + built-in memory files (`MEMORY.md`, `USER.md`) |
| Loop shape | Fast inner loop, indefinite | Scheduled staged loop (nightly style) |
| Keep/discard mechanism | Hard metric gate + git reset | Acceptance gate + apply safeguards + regression watch |
| Safety model | Minimal by design | Strong operational safeguards and explicit failure states |
| Human role | Set `program.md`, then mostly let run | Observe status/digest; intervene on `needs_review` |
| Auditability | `results.tsv`, git history | DB lifecycle, digests, CLI status, history files |
| Generalization style | Single measurable domain (training) | Multi-signal behavior optimization in live agent usage |
| Determinism | Higher (metric-defined) | Lower (heuristics + LLM judgement in loop) |

---

## 4) Critique of Hermes Effectiveness

## Where Hermes is strong

1. Production hardening is materially better than Karpathy's setup.
- Hermes has non-destructive phases, explicit lifecycle states, and rollback/review paths.
- Two-phase memory updates reduce accidental destructive writes.
- Profile-safe paths and CLI visibility make operations practical in multi-profile environments.

2. It addresses a broader real problem.
- Karpathy improves a model on one benchmark metric.
- Hermes targets real user-facing behavior defects, including stale memory.

3. Test surface is strong for this stage.
- There are unit and integration tests for memory anomaly/proposal/apply and mixed skill+memory flows.

## Where Hermes is weak

1. Objective quality is weaker than Karpathy's objective.
- Karpathy has a single hard metric from real training.
- Hermes uses proxy signals (`correction_count`, completion heuristics, snippets) and LLM-judged self-play, which are noisier and easier to game.

2. Stage 2 anomaly coverage is narrow.
- `anomaly_detector.py` currently flags only `UNDERPERFORMING` via threshold rules.
- Structural failures and missing coverage are deferred, so detection recall is limited.

3. Evaluation rigor is limited.
- Token efficiency proxy is response length, not true cost/latency.
- Candidate generation and judging both depend on LLM behavior, creating correlated bias.

4. Memory staleness detection is still heuristic.
- Current contradiction detection relies on negation + token overlap.
- This will miss subtle stale facts and can still trigger false positives for semantically similar but valid entries.

## Net assessment

Hermes is effective as a cautious production self-improvement loop, but not yet as reliable as Karpathy-style metric-driven research for proving real quality gains. Its current bottleneck is signal and evaluation fidelity, not orchestration.

---

## 5) Showstopper Assessment

For the statement "memory staleness/update is designed in docs but not implemented in runtime code":

- Current status: this is no longer true in this codebase.
- Runtime implementation exists in:
  - `cron/autoresearch/memory_anomaly_detector.py`
  - `cron/autoresearch/memory_hypothesis_generator.py`
  - `cron/autoresearch/memory_updater.py`
  - Stage wiring in `cron/autoresearch/__init__.py` and `cron/autoresearch/runner.py`
- Persistence/digest/CLI surface exists in:
  - `cron/autoresearch/skill_metrics.py`
  - `cron/autoresearch/digest.py`
  - `hermes_cli/autoresearch.py`
- Tests exist, including e2e memory flow:
  - `tests/integration/test_autoresearch_memory_e2e.py`

Conclusion: no architectural showstopper remains for "implementing memory staleness/update"; the main remaining work is quality tuning (precision/recall of detection and evaluation fidelity).

---

## 6) Practical Recommendations (Priority Order)

1. Increase signal quality before adding scope.
- Improve session labeling beyond regex-only correction detection.
- Add stronger attribution for which skill/memory caused failure.

2. Strengthen evaluation validity.
- Add holdout replay datasets and offline adjudication for accepted patches.
- Use independent judge model or rubric checks to reduce same-model bias.

3. Raise anomaly coverage.
- Implement deferred anomaly classes (`STRUCTURALLY_BROKEN`, `MISSING_COVERAGE`) with explicit tests.

4. Harden memory contradiction logic.
- Add temporal and source-aware evidence weighting.
- Introduce suppression rules for known ambiguous phrasings.

5. Add operator confidence metrics.
- Track acceptance-to-regression ratio and memory proposal precision over time in digest/status output.

If these are completed, Hermes moves closer to Karpathy's decisive optimization style while keeping its production safety advantages.
