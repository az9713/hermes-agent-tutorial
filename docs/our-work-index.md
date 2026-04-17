# Our Work Index

This index covers documentation, analysis, and implementation work added in this fork.  
Upstream core docs remain indexed in [docs/index.md](index.md).

---

## Self-Improvement Analysis

| Doc | What it contains |
|-----|-----------------|
| [Self-Improvement Deep Dive](analysis/self-improvement-deep-dive.md) | Code-level audit of Hermes self-improvement claims and practical limitations |
| [From Critique to Implementation](analysis/implementation-discussion.md) | Planning record of scoped decisions and tradeoffs |
| [Autoresearch vs Karpathy](analysis/autoresearch-vs-karpathy.md) | Comparison of Hermes autoresearch and Karpathy's reference approach |
| [Measurement-Fidelity Test Report](analysis/autoresearch-measurement-fidelity-test-report.md) | Test inventory, run receipts, interpretation, and explicit "not tested" boundaries for the upgraded autoresearch loop |
| [Implementation Status](analysis/implementation-status.md) | Historical implementation record for an earlier self-improvement enhancement set (kept for audit context) |
| [Session Notes (2026-04-14)](analysis/session-2026-04-14.md) | Architecture and design-session notes from the autoresearch exploration cycle |
| [E2E Test Analysis](analysis/e2e-autoresearch-test-analysis.md) | Coverage analysis of `tests/integration/test_autoresearch_e2e.py` |
| [Golden-Answer Test](analysis/golden-answer-test.md) | Reference for `tests/cron/test_autoresearch_golden_answer.py` |

---

## Skills History and Rollback

| Doc | What it contains |
|-----|-----------------|
| [history Command Reference](analysis/history-command.md) | Feature reference for `hermes skills history <name>` |
| [history CLI Tests](analysis/history-cli-tests.md) | Test evidence and limits for history CLI behavior |
| [rollback CLI Tests](analysis/rollback-cli-tests.md) | Test evidence and limits for rollback CLI behavior |
| [Expiry Edge-Case Tests](analysis/expiry-edge-case-tests.md) | Reference for memory expiry boundary tests |

---

## Autoresearch Design and Plan

| Doc | What it contains |
|-----|-----------------|
| [Autoresearch Loop Design](ideas/autoresearch-loop.md) | High-level autoresearch design and learning-loop rationale |
| [Coexistence Analysis](analysis/skill-improvement-coexistence.md) | How in-session patching and autoresearch coexist safely |
| [Autoresearch MVP Plan](plans/autoresearch-mvp-plan.md) | Original staged implementation plan |

---

## Current Autoresearch Implementation Docs

| Doc | What it contains |
|-----|-----------------|
| [Stage 1 Implementation](implementation/stage1-autoresearch.md) | Current Stage 1 observe/label contract, enriched signal extraction, and metrics schema behavior |
| [Stage 2 Implementation](implementation/stage2-autoresearch.md) | Current Stage 2 anomaly, evaluation, and memory-proposal orchestration contract |
| [Stage 3 Implementation](implementation/stage3-autoresearch.md) | Current Stage 3 skill apply/recovery, memory apply lifecycle, and digest/KPI behavior |
| [Autoresearch Memory v1](implementation/autoresearch-memory-v1.md) | Built-in-memory two-phase staleness detection and safe apply flow |
| [Measurement-Fidelity Upgrade](implementation/autoresearch-measurement-fidelity-upgrade.md) | Full what/why/how for the measurement-fidelity upgrade across Stage 1/2/3 and operator reporting |
| [Scheduling and Delivery Implementation](implementation/scheduling-and-delivery.md) | Current runner, scheduler, CLI, and digest delivery contract |

---

## Modified Upstream Docs

| Doc | What was added |
|-----|---------------|
| [Skill System](concepts/skill-system.md) | SKILL_HISTORY.md and skill-change audit trail notes |
| [CLI Commands](reference/cli-commands.md) | `hermes skills history` and `hermes skills rollback` references |

---

## Suggested Reading Order

1. [Self-Improvement Deep Dive](analysis/self-improvement-deep-dive.md)
2. [Autoresearch Loop Design](ideas/autoresearch-loop.md)
3. [Autoresearch MVP Plan](plans/autoresearch-mvp-plan.md)
4. [Stage 1 Implementation](implementation/stage1-autoresearch.md)
5. [Stage 2 Implementation](implementation/stage2-autoresearch.md)
6. [Stage 3 Implementation](implementation/stage3-autoresearch.md)
7. [Autoresearch Memory v1](implementation/autoresearch-memory-v1.md)
8. [Measurement-Fidelity Upgrade](implementation/autoresearch-measurement-fidelity-upgrade.md)
9. [Measurement-Fidelity Test Report](analysis/autoresearch-measurement-fidelity-test-report.md)
10. [Scheduling and Delivery Implementation](implementation/scheduling-and-delivery.md)

