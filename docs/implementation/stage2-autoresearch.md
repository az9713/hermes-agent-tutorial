# Stage 2 Autoresearch — Implementation Document

> **Stage: Hypothesize + Evaluate.**
> Stage 2 reads `skill_metrics.db`, calls an LLM to propose patches, evaluates
> them via self-play, and writes `pending_patches.json`.
> Nothing in `HERMES_HOME/skills/` is ever modified.

---

## 1. What Stage 2 Does

Stage 2 is the hypothesis-and-evaluation half of the autoresearch loop. Each time it runs (nightly, after Stage 1), it:

1. Reads 7-day rolling skill health from `skill_metrics.db`.
2. Runs anomaly detection — identifies skills whose correction rate or completion rate exceeds the threshold.
3. For each anomaly, reads the current `SKILL.md` and calls an LLM to propose a targeted patch.
4. Evaluates each patch via self-play: run the old skill and the patched skill on synthetic tasks, score both with an LLM judge.
5. Writes `pending_patches.json` — every candidate with its status (`accepted`, `rejected`, or `hold`).

No skill files are read, edited, or patched by Stage 2. The only write target is `pending_patches.json`.

---

## 2. File Map

```
cron/autoresearch/
├── __init__.py              — MODIFIED: run_stage2() entry point added
├── anomaly_detector.py      — NEW: reads skill_health, returns UNDERPERFORMING anomalies
├── hypothesis_generator.py  — NEW: LLM call → CandidatePatch or None
├── self_play_evaluator.py   — NEW: self-play → EvalResult (accepted/rejected/hold)
└── pending_patches.py       — NEW: writes/reads ~/.hermes/autoresearch/pending_patches.json
```

### New files in tests:

```
tests/cron/
├── test_anomaly_detector.py     — anomaly detection unit tests (19 tests)
├── test_hypothesis_generator.py — hypothesis generation unit tests (17 tests)
├── test_self_play_evaluator.py  — self-play evaluation unit tests (13 tests)
├── test_pending_patches.py      — pending patches read/write unit tests (25 tests)
└── test_autoresearch_stage2.py  — end-to-end integration tests (7 tests)
```

---

## 3. Components

### 3.1 Anomaly Detector

**File:** `cron/autoresearch/anomaly_detector.py`

Reads `skill_health` from `skill_metrics.db` using the same 7-day rolling window query as `get_skill_health_summary()`. Returns a list of `Anomaly` dicts.

**Scope decision:** Stage 2 only implements `UNDERPERFORMING` anomalies — skills whose correction or completion rates exceed the statistical gate. `STRUCTURALLY_BROKEN` (high in-session patch rate) and `MISSING_COVERAGE` (frequent task type with no skill) are deferred to later work.

**Thresholds:**
```python
CORRECTION_RATE_THRESHOLD = 0.30   # flag if > 30%
COMPLETION_RATE_THRESHOLD = 0.50   # flag if < 50%
MIN_INVOCATIONS = 3                # skip skills with too little data
```

These are the same thresholds as Stage 1's reporter, so a skill flagged in the nightly report is exactly the set of skills Stage 2 will attempt to improve.

**Threshold semantics:** Strict inequality. A skill at exactly 30% correction rate is `OK`, not `UNDERPERFORMING`. This matches the reporter's gate.

**Weighted aggregation:** Anomaly detection uses the same weighted average formula as `get_skill_health_summary()`:

```sql
SUM(correction_rate * invocation_count) / NULLIF(SUM(invocation_count), 0)
```

This ensures a skill with 9 good sessions and 1 bad session isn't penalised the same as a skill with 9 bad sessions and 1 good session.

**Output shape (`Anomaly` dict):**
```python
{
    "skill_name":      "git-workflow",
    "anomaly_type":    "UNDERPERFORMING",
    "trigger_metric":  "correction_rate=0.41, completion_rate=0.42",
    "correction_rate": 0.41,
    "completion_rate": 0.42,
    "avg_tokens":      1500.0,
    "invocation_count": 7,
}
```

---

### 3.2 Hypothesis Generator

**File:** `cron/autoresearch/hypothesis_generator.py`

For each `UNDERPERFORMING` anomaly, sends an LLM prompt asking for a targeted patch.

**LLM injection:** The LLM is passed as a `Callable[[list[dict]], str]` parameter. This means:
- Tests use deterministic lambda stubs — no live API calls.
- `run_stage2()` injects `call_llm` from `agent.auxiliary_client` at runtime.

**Prompt structure:**
- System: role description + rules (old_string must appear verbatim; patch must be minimal; no full rewrites)
- User: anomaly metrics, current SKILL.md, up to 5 session correction excerpts

**Validation after LLM call:**
1. Parse JSON (handles markdown-fenced output and whitespace noise).
2. If `"patch"` is `null` — return `None` (LLM says no fix possible).
3. If `"patch"` is missing entirely — return `None`.
4. If `old_string` is empty or not found in the actual SKILL.md — return `None` (safety gate against hallucinated text).

This last check is the critical safety guard: Stage 3 uses `old_string` for a `str.replace()` on the live skill file. If `old_string` is not in the current content, the replace would silently no-op or, in the worst case, corrupt the file if Stage 3 used a different approach.

**Output shape (`CandidatePatch` dict):**
```python
{
    "skill_name":      "git-workflow",
    "anomaly_type":    "UNDERPERFORMING",
    "trigger_metric":  "correction_rate=0.41",
    "old_string":      "Always create a feature branch.",
    "new_string":      "Always create a feature branch named feat/<ticket-id>.",
    "reason":          "Clarify naming convention to reduce corrections",
    "raw_llm_output":  '{"patch": {...}}',   # for debugging
}
```

---

### 3.3 Self-Play Evaluator

**File:** `cron/autoresearch/self_play_evaluator.py`

For each `CandidatePatch`, runs a three-step self-play evaluation.

**Step 1 — Synthetic tasks:**
Generates `N_SYNTHETIC_TASKS = 5` task descriptions. If enough real session excerpts are available (≥ 5), uses them directly. Otherwise, rephrases available excerpts via LLM to reach 5.

**Step 2 — Agent self-play:**
For each synthetic task, makes two LLM calls:
- Old skill in context → `response_old`
- Patched skill in context → `response_new`

No tool execution, no file writes — single-turn completion only.

**Step 3 — Evaluation:**
```python
token_delta    = (total_len_new - total_len_old) / max(total_len_old, 1)
quality_delta  = avg(judge_scores_new) - avg(judge_scores_old)

# Judge scores each response 0-10 for correctness + completeness
score_old = judge(task, response_old)
score_new = judge(task, response_new)
```

**Acceptance gate:**
```python
accepted = token_delta < 0 and quality_delta >= 0
# More efficient AND at least as good quality
```

**HOLD condition:**
When the judge score for old and new differ by more than `JUDGE_DISAGREEMENT_THRESHOLD + 2 = 4` points (large disagreement suggesting the judge is unreliable), a second judge call is made. If the two calls disagree by more than `JUDGE_DISAGREEMENT_THRESHOLD = 2.0` points, the patch is `HOLD` for human review.

**Output shape (`EvalResult` dict):**
```python
{
    "accepted":          True,
    "status":            "accepted",   # "accepted" | "rejected" | "hold"
    "token_delta":       -0.12,
    "quality_delta":     0.40,
    "judge_scores":      [[7.0, 8.0], [6.5, 7.5], ...],  # one pair per task
    "hold_reason":       "",
    "rejection_reason":  "",
}
```

---

### 3.4 Pending Patches

**File:** `cron/autoresearch/pending_patches.py`
**Output:** `~/.hermes/autoresearch/pending_patches.json`

Merges each `(CandidatePatch, EvalResult)` pair into a single entry and writes the array to JSON.

**All required fields in each entry:**
```python
{
    "skill_name":       "git-workflow",
    "anomaly_type":     "UNDERPERFORMING",
    "trigger_metric":   "correction_rate=0.41",
    "action":           "patch",
    "status":           "accepted",
    "accepted":         True,
    "token_delta":      -0.12,
    "quality_delta":    0.40,
    "judge_scores":     [[7.0, 8.0], ...],
    "old_string":       "Always create a feature branch.",
    "new_string":       "Always create a feature branch named feat/<ticket-id>.",
    "reason":           "Clarify naming convention",
    "hold_reason":      "",
    "rejection_reason": "",
    "generated_at":     "2026-04-15T03:00:00Z",
}
```

**`read_pending_patches(path)`** returns an empty list if the file is missing or malformed — never raises. This makes it safe for Stage 3 to call unconditionally.

**`write_pending_patches(pairs, path)`** always writes, even if `pairs` is empty. Stage 3 can detect "nothing to do" from an empty array rather than a missing file.

---

### 3.5 Entry Point

**File:** `cron/autoresearch/__init__.py`

`run_stage2()` orchestrates the pipeline in 4 steps:

```python
def run_stage2(metrics_db_path, patches_path, hermes_home, llm_call, days=7):
    # 1. Detect anomalies
    anomalies = detect_anomalies(conn, days=days)
    if not anomalies:
        write_pending_patches([], path=patches_path)
        return []

    # 2-3. For each anomaly: generate hypothesis + evaluate
    pairs = []
    for anomaly in anomalies:
        skill_content = _read_skill_content(anomaly["skill_name"], hermes_home)
        if skill_content is None: continue          # SKILL.md missing → skip

        candidate = generate_hypothesis(anomaly, skill_content, excerpts, llm_call)
        if candidate is None: continue             # LLM can't propose patch → skip

        eval_result = evaluate_candidate(candidate, skill_content, excerpts, llm_call)
        pairs.append({"candidate": candidate, "eval_result": eval_result})

    # 4. Write pending_patches.json
    write_pending_patches(pairs, path=patches_path)
    return pairs
```

**LLM resolution:** If `llm_call` is not injected, `run_stage2()` imports `call_llm` from `agent.auxiliary_client` and wraps it to return `response.choices[0].message.content`. Tests always inject a lambda stub — the import never runs in tests.

---

## 4. Tests

### 4.1 Test Philosophy

All tests use real SQLite in `tmp_path` (no mocks for DB). All LLM calls are replaced by injected lambda stubs — no live API calls in any test. This combination catches:
- Schema mismatches (DB tests)
- JSON parsing edge cases (hypothesis generator tests)
- Gate logic errors (self-play evaluator tests)
- File I/O failures (pending patches tests)
- Integration failures invisible to unit tests (Stage 2 integration tests)

### 4.2 `test_anomaly_detector.py` (19 tests)

| Class | What it checks |
|-------|---------------|
| `TestNoAnomalies` | Empty DB → empty list; within-threshold skill not flagged; exact threshold is OK (strict >/<) |
| `TestUnderperformingFlag` | High correction rate → UNDERPERFORMING; low completion rate → UNDERPERFORMING; both exceeded → both in `trigger_metric`; trigger metric contains numeric value; anomaly fields populated |
| `TestMinInvocations` | Below MIN_INVOCATIONS not flagged; exactly MIN_INVOCATIONS is flagged; custom min_invocations respected |
| `TestSortOrder` | Anomalies sorted by correction_rate descending; OK skills excluded from result |
| `TestRollingWindow` | Stale rows excluded; recent rows included; boundary day (exactly `days` ago) included; custom days window |
| `TestMultipleSkills` | Only flagged skills returned; multi-day weighted aggregate (weighted by invocation_count) |

### 4.3 `test_hypothesis_generator.py` (17 tests)

| Class | What it checks |
|-------|---------------|
| `TestSuccessfulPatch` | Valid JSON → CandidatePatch; anomaly fields propagated; raw LLM output included; markdown-fenced JSON parsed |
| `TestNoneReturns` | `"patch": null` → None; unparseable → None; old_string not in skill → None; empty old_string → None; missing `"patch"` key → None |
| `TestLlmMessages` | System message sent; user message sent; skill name in user message; trigger metric in user message; skill content in user message |
| `TestSessionExcerpts` | Excerpts included in user message; empty excerpts handled gracefully |

**Key design note — `_extract_json` parsing pipeline:**

The function tries three strategies in order:
1. Direct `json.loads(text.strip())`
2. Strip markdown fences (```` ``` ```` or ```` ```json ````), then `json.loads`
3. Regex to find first `{...}` block, then `json.loads`

This makes the function resilient to common LLM output formatting habits without being brittle about exact format.

### 4.4 `test_self_play_evaluator.py` (13 tests)

| Class | What it checks |
|-------|---------------|
| `TestAcceptanceGate` | Shorter + better → accepted; longer → rejected; worse quality → rejected; same quality + shorter → accepted (delta = 0) |
| `TestEmptyTasks` | No session tasks → rejected with explanation |
| `TestPatchApplication` | Patched skill content used for new-skill agent runs; original skill content used for old-skill runs |
| `TestDeltaComputation` | `token_delta = (new - old) / old`; `quality_delta = avg(new_scores) - avg(old_scores)`; `judge_scores` has one pair per task |
| `TestResultFields` | All 7 required fields present; `hold_reason` empty when accepted; `rejection_reason` empty when accepted |

**`_CallCounter` stub:** Tests use a `_CallCounter` class that cycles through a pre-defined response sequence. This is more reliable than a function with a mutable counter because the call order is deterministic given the evaluator's fixed pipeline structure.

### 4.5 `test_pending_patches.py` (25 tests)

| Class | What it checks |
|-------|---------------|
| `TestWritePendingPatches` | File created; returns JSON text; text matches file; parent dirs created; empty list → empty JSON array; multiple entries written |
| `TestEntryFields` | All 15 required fields present; skill_name/status/accepted/old_string/new_string/reason/token_delta/quality_delta/judge_scores/action preserved; `generated_at` matches UTC ISO-8601 regex |
| `TestReadPendingPatches` | Missing file → empty list; reads written file; returns list of dicts; corrupted file → empty list; skill_name round-trips |

### 4.6 `test_autoresearch_stage2.py` (7 tests)

| Test | What it checks |
|------|---------------|
| `test_no_anomalies_returns_empty_list` | No flagged skills → `[]` returned; empty `pending_patches.json` written |
| `test_missing_skill_md_skipped_gracefully` | Anomaly detected but SKILL.md missing → no crash, skip silently |
| `test_null_hypothesis_skipped_gracefully` | LLM can't generate patch → skip, no entry in patches |
| `test_accepted_patch_in_output` | Full happy path: anomaly → candidate → entry in patches file |
| `test_rejected_patch_in_output` | Rejected candidate appears in patches file |
| `test_patches_file_always_written` | pending_patches.json written even when all skills skipped |
| `test_skill_md_not_modified` | SKILL.md unchanged after run_stage2() (mtime and content both checked) |

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
                -v --override-ini="addopts="

Result: 174 passed, 0 failed, 1 warning (1.77s)
```

The 1 warning is the pre-existing asyncio deprecation in `conftest.py` — unrelated to autoresearch code.

Breakdown by stage:
- Stage 1 tests (93 tests): all pass, unchanged from prior session
- Stage 2 tests (81 new tests): 19 + 17 + 13 + 25 + 7 = 81

---

## 6. Verification Criteria

Stage 2 is complete when:

- [x] `pending_patches.json` generated with `accepted`/`rejected`/`hold` status per patch
- [x] Self-play uses patched skill content for new runs, original for old runs
- [x] Rejected patches logged with reason (`rejection_reason` field)
- [x] `HOLD` patches have `hold_reason` populated
- [x] Running on a day with no anomalies produces empty `pending_patches.json` and logs "No anomalies detected"
- [x] No skill files modified (mtime unchanged in integration test)
- [x] 174/174 tests pass (Stage 1 + Stage 2 combined)

---

## 7. Design Decisions

### LLM Injection

The LLM callable is injected as a parameter (`Callable[[list[dict]], str]`) rather than imported at the module level. This means:

- **Tests never touch live APIs.** Every test uses a lambda stub. No environment variables, no network calls, no API keys needed.
- **`run_stage2()` resolves the callable at call time**, not at import time. This avoids import errors when the module is loaded in environments without Anthropic credentials.
- **The interface is stable.** Any callable that takes a list of `{"role": ..., "content": ...}` dicts and returns a string works. This makes it straightforward to swap providers (Anthropic → OpenAI → local model) without changing the component code.

### Stage 2 Scope: UNDERPERFORMING Only

The plan described three anomaly types. Stage 2 implements only `UNDERPERFORMING`.

**Why:** `STRUCTURALLY_BROKEN` requires reading SKILL_HISTORY.md entries (parsing the `[in-session]` tagged headers), which adds complexity but no new component types. `MISSING_COVERAGE` requires task classification (an LLM call per session that isn't currently stored in `session_signals`). Both are clear extensions of the Stage 2 primitives — they can be added without rearchitecting anything.

`UNDERPERFORMING` alone delivers the core value: automatically improving skills that users are visibly correcting the agent on. That is the highest-signal case.

### Self-Play Task Grounding

Self-play uses session excerpts as seeds for synthetic tasks rather than fully synthetic generation. This matters because a fully synthetic task for "git-workflow" might generate prompts that the skill was never designed for. Grounding in real sessions keeps the evaluation distribution closer to actual usage.

The fallback (LLM rephrase when fewer than 5 real excerpts) is still grounded: it rephrases a real task description, not invents a new one.

### pending_patches.json is Always Written

Even when no anomalies are detected (or all candidates are skipped), `pending_patches.json` is written as an empty array. This makes Stage 3 simpler: it can always read the file unconditionally and handle `[]` as "nothing to apply" without checking for file existence.

---

## 8. What Stage 3 Will Add

Stage 3 reads `pending_patches.json` and applies accepted patches:

- **Recency lock:** Skip skills that had an in-session patch within 24h (avoids racing with user corrections).
- **Auto-apply:** Call `_append_skill_history()` with `source="autoresearch"` — the source tag from Stage 1 makes this auditable.
- **Regression watch:** Next night, check whether metrics worsened. Auto-rollback if correction_rate rose > 15% and no in-session patches occurred since (unambiguous causation).
- **Nightly digest:** Format a human-readable summary and deliver via the configured gateway platform.
