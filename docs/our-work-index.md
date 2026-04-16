# Our Work Index

This index covers all documentation, analysis, and implementation work added to this fork.
It does not include the original upstream Hermes Agent docs (those are indexed in [docs/index.md](index.md)).

---

## Self-Improvement Analysis

These docs analyse how Hermes's self-improvement actually works in practice — the honest version, including what doesn't work and why.

| Doc | What it contains |
|-----|-----------------|
| [Self-Improvement Deep Dive](analysis/self-improvement-deep-dive.md) | Code-level audit of every self-improvement claim: what's real, what's LLM-driven annotation masquerading as learning, and concrete improvement suggestions |
| [From Critique to Implementation](analysis/implementation-discussion.md) | Planning record: which improvements were scoped in, which were scoped out, and the reasoning behind each decision |
| [Implementation Status](analysis/implementation-status.md) | Post-implementation record: exactly what was built, what the 179 tests cover and don't, and what conclusions they actually support |
| [Session Notes (2026-04-14)](analysis/session-2026-04-14.md) | Running notes from the codebase study and autoresearch design sessions — architecture survey, self-improvement reality check, Karpathy loop design |
| [E2E Test Analysis](analysis/e2e-autoresearch-test-analysis.md) | Honest coverage analysis of `tests/integration/test_autoresearch_e2e.py` — what runs real code, what is scripted, what is not tested, and why the tests do not prove the feature is useful |

---

## Skills History & Rollback (Feature)

These docs cover the `hermes skills history` and `hermes skills rollback` commands added to this fork.

| Doc | What it contains |
|-----|-----------------|
| [history Command Reference](analysis/history-command.md) | Full feature reference, implementation walkthrough, and test evidence for `hermes skills history <name>` |
| [history CLI Tests](analysis/history-cli-tests.md) | Complete reference for `tests/hermes_cli/test_history.py` — what passed, what it proves, what it doesn't |
| [rollback CLI Tests](analysis/rollback-cli-tests.md) | Complete reference for `tests/hermes_cli/test_rollback.py` — what passed, what it proves, what it doesn't |
| [Expiry Edge-Case Tests](analysis/expiry-edge-case-tests.md) | Complete reference for `TestExpiryEdgeCases` in `test_memory_tool.py` — coverage, interpretation, and limits |

---

## Autoresearch Loop Design

These docs cover the design of an automated research loop that improves skills nightly without conflicting with in-session patching.

| Doc | What it contains |
|-----|-----------------|
| [Autoresearch Loop Design](ideas/autoresearch-loop.md) | The full design — reward signals (token efficiency, correction absence, completion), Karpathy-style hypothesis→evaluate→update cycle, 3-stage staged MVP, coexistence with in-session patching |
| [Coexistence Analysis](analysis/skill-improvement-coexistence.md) | How in-session patching and the autoresearch loop work side-by-side without conflict — the hotfix/release mental model, 4 coordination mechanisms (recency lock, source tagging, in-session patch rate as signal, scoped regression watch) |
| [Autoresearch MVP Plan](plans/autoresearch-mvp-plan.md) | Stage-by-stage implementation plan: Stage 1 (Observe), Stage 2 (Hypothesize + Evaluate), Stage 3 (Apply + Recover) — file map, DB schemas, pseudocode, verification criteria |

---

## Autoresearch Stage 1 Implementation

| Doc | What it contains |
|-----|-----------------|
| [Stage 1 Implementation](implementation/stage1-autoresearch.md) | Component-by-component walkthrough of what was built: signal extractor, skill metrics DB, reporter, `run_stage1()` entry point. Includes all design decisions, a timezone bug caught during testing, a table of all 93 tests and what each class verifies, and the full test receipt (93/93 pass, 0.86s) |

---

## Autoresearch Stage 2 Implementation

| Doc | What it contains |
|-----|-----------------|
| [Stage 2 Implementation](implementation/stage2-autoresearch.md) | Component-by-component walkthrough of what was built: anomaly detector, hypothesis generator, self-play evaluator, pending patches I/O, `run_stage2()` entry point. Includes all design decisions (LLM injection, scope decision, self-play grounding), table of all 81 new tests and what each class verifies, combined test receipt (174/174 pass, 1.77s) |

---

## Autoresearch Stage 3 Implementation

| Doc | What it contains |
|-----|-----------------|
| [Stage 3 Implementation](implementation/stage3-autoresearch.md) | Component-by-component walkthrough of what was built: patch applier (recency lock, stale-patch guard, atomic writes), regression watch (causation check, rollback threshold), nightly digest formatter, `run_stage3()` entry point. Includes all design decisions (dry_run, source-aware recency lock, old/new in DB), table of all 82 new tests and what each class verifies, combined test receipt (256/256 pass, 3.34s), and the full 3-stage loop summary |

---

## Autoresearch Scheduling, CLI & Delivery

These components complete the autoresearch loop by wiring it into the nightly
scheduler, exposing it via the CLI, and delivering digests to Slack & Telegram.

| Doc | What it contains |
|-----|-----------------|
| [Scheduling & Delivery Implementation](implementation/scheduling-and-delivery.md) | Component-by-component walkthrough of what was built: full-loop runner (Stage 1→2→3 orchestration, graceful Stage 2 skip, atomic state.json), scheduler tick (croniter schedule check, never-ran base date, error isolation), CLI commands (run/status/schedule/patches/enable/disable), config section (enabled/schedule/dry_run/deliver). Includes all design decisions, table of all 47 tests and what each class verifies, combined test receipt (303/303 pass, 3.08s), and complete end-to-end loop diagram. |

---

## Modified Upstream Docs

These upstream docs were extended with content from our sessions:

| Doc | What was added |
|-----|---------------|
| [Skill System](concepts/skill-system.md) | Added section on SKILL_HISTORY.md — the append-only audit trail of skill patches |
| [CLI Commands](reference/cli-commands.md) | Added `hermes skills history` and `hermes skills rollback` command references |

---

## Reading Order

If you're new to this fork, the recommended reading order is:

1. [Self-Improvement Deep Dive](analysis/self-improvement-deep-dive.md) — understand what Hermes actually does today
2. [Autoresearch Loop Design](ideas/autoresearch-loop.md) — understand what we're building and why
3. [Coexistence Analysis](analysis/skill-improvement-coexistence.md) — understand how old and new systems interact
4. [Autoresearch MVP Plan](plans/autoresearch-mvp-plan.md) — understand the 3-stage roadmap
5. [Stage 1 Implementation](implementation/stage1-autoresearch.md) — signal extraction, skill metrics DB, nightly report
6. [Stage 2 Implementation](implementation/stage2-autoresearch.md) — anomaly detection, hypothesis generation, self-play evaluation, pending_patches.json
7. [Stage 3 Implementation](implementation/stage3-autoresearch.md) — patch applier, regression watch, nightly digest, `run_stage3()`
8. [Scheduling & Delivery](implementation/scheduling-and-delivery.md) — runner, scheduler tick, CLI, config, Slack/Telegram delivery
