# Stage 1 Autoresearch — Implementation Document

> **Stage: Observe Only.**  
> Stage 1 reads state.db, writes skill_metrics.db, and produces a nightly report.  
> Nothing in `HERMES_HOME/skills/` is ever modified.

---

## 1. What Stage 1 Does

Stage 1 is the observation half of the autoresearch loop. Each time it runs (nightly via cron), it:

1. Reads the last 24 hours of sessions from `state.db`.
2. Extracts structured signals from those sessions (token usage, correction count, completion flag, skills invoked).
3. Stores the signals in a separate `skill_metrics.db` so they survive across runs.
4. Aggregates per-skill health metrics for today.
5. Computes a 7-day rolling health summary per skill.
6. Writes `nightly_report.md` — a human-readable Markdown file listing flagged skills.

No skill files are read, edited, or patched. The only write targets are `skill_metrics.db` and `nightly_report.md`.

---

## 2. File Map

```
cron/autoresearch/
├── __init__.py          — run_stage1() entry point (wires all pieces)
├── signal_extractor.py  — reads state.db, produces SessionSignal dicts
├── skill_metrics.py     — SQLite DB layer (session_signals, skill_health tables)
└── reporter.py          — generates nightly_report.md from skill_health data

tools/skill_manager_tool.py  — MODIFIED: added source= param to _append_skill_history()
```

### New files in tests:

```
tests/cron/
├── test_skill_manager_source_tag.py   — source tagging (Slice 1)
├── test_skill_metrics.py              — DB layer unit tests
├── test_signal_extractor.py           — signal extraction unit tests
├── test_reporter.py                   — reporter unit tests
└── test_autoresearch_stage1.py        — end-to-end integration tests
```

---

## 3. Components

### 3.1 Source Tag in `_append_skill_history()` (Slice 1)

**File:** `tools/skill_manager_tool.py`

**Change:** Added a `source: str = "in-session"` parameter.

```python
def _append_skill_history(
    skill_dir: Path,
    action: str,
    reason: str,
    file_path: str,
    old_text: str,
    new_text: str,
    source: str = "in-session",   # ← new parameter, backward-compatible
) -> None:
    ...
    record = (
        f"\n## {now} — {action} [{source}]\n"  # ← source tag in header
        ...
    )
```

**Why:** When Stage 3 applies an autoresearch patch, it needs to tag the history entry as `[autoresearch]` so operators can audit which system wrote which entry. The tag also lets the regression watch distinguish its own rollbacks (`[autoresearch: regression-watch]`) from normal patches.

**Backward compatibility:** All existing callers that don't pass `source` automatically get `[in-session]` — no callers needed to change.

**Header format:**
```
## 2026-04-15T10:30:00Z — patch [autoresearch]
```

The regex `^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — (\w+) \[(.+)\]$` parses all three fields.

---

### 3.2 Signal Extractor

**File:** `cron/autoresearch/signal_extractor.py`

Reads the `sessions` and `messages` tables from `state.db`. For each session within the `since_hours` window, it produces a `SessionSignal` dict:

| Field | Source | Notes |
|-------|--------|-------|
| `session_id` | `sessions.id` | Primary key |
| `session_date` | `sessions.started_at` | UTC YYYY-MM-DD |
| `total_tokens` | `input_tokens + output_tokens` | From sessions table |
| `tool_call_count` | `sessions.tool_call_count` | Already aggregated in state.db |
| `correction_count` | Regex on user-role messages | `_CORRECTION_PATTERNS` |
| `completion_flag` | `end_reason` or last user msg | Boolean |
| `skills_invoked` | System prompt substring match | Heuristic — see note |

**Correction detection** — `_CORRECTION_PATTERNS` catches phrases like:
- "try again", "start over"
- "that's wrong / incorrect / not right"
- "not what I meant / asked / wanted"
- "you misunderstood"

JSON-formatted content (tool results starting with `[` or `{`) is skipped to avoid false positives.

**Completion detection** — two criteria, either is sufficient:
1. `end_reason` is `"cli_close"` or `"user_quit"` (explicit session close)
2. Last user message matches `_COMPLETION_PATTERNS` (thanks, perfect, done, looks good, etc.)

**Skill detection** — heuristic: scan `HERMES_HOME/skills/` for directories containing `SKILL.md`, then check if each skill name appears in `system_prompt`. This is imprecise — a skill name can appear in explanatory text without the skill being active. Reliable attribution is deferred to Stage 2 (LLM classification).

**Graceful degradation:** If `state.db` doesn't exist or is unreadable, returns `[]` with a log message — never raises.

---

### 3.3 Skill Metrics DB

**File:** `cron/autoresearch/skill_metrics.py`  
**Database:** `~/.hermes/autoresearch/skill_metrics.db` (WAL mode, separate from `state.db`)

Three tables:

**`session_signals`** — one row per analysed session (raw signals):
```sql
session_id        TEXT PRIMARY KEY,
session_date      TEXT NOT NULL,        -- YYYY-MM-DD (UTC)
total_tokens      INTEGER DEFAULT 0,
tool_call_count   INTEGER DEFAULT 0,
correction_count  INTEGER DEFAULT 0,
completion_flag   INTEGER DEFAULT 0,
skills_invoked    TEXT DEFAULT '[]',    -- JSON array
extracted_at      TEXT NOT NULL
```

**`skill_health`** — one row per (skill, date) aggregated from session_signals:
```sql
skill_name            TEXT    NOT NULL,
health_date           TEXT    NOT NULL,
invocation_count      INTEGER DEFAULT 0,
avg_tokens            REAL    DEFAULT 0,
correction_rate       REAL    DEFAULT 0,
completion_rate       REAL    DEFAULT 0,
in_session_patch_count INTEGER DEFAULT 0,
PRIMARY KEY (skill_name, health_date)
```

**`autoresearch_patches`** — patch log for Stage 3 (written by Stage 3, read by regression watch):
```sql
id, skill_name, patch_applied_at, patch_type,
baseline_tokens, baseline_correction_rate, baseline_completion_rate,
status TEXT DEFAULT 'applied'
```

**Key design decisions:**
- `INSERT OR REPLACE` on `session_signals` makes ingestion idempotent — running Stage 1 twice never double-counts.
- `skill_health` uses `INSERT OR REPLACE` keyed on `(skill_name, health_date)` — re-running the nightly job overwrites that day's aggregate rather than accumulating stale rows.
- All dates stored as UTC strings (`YYYY-MM-DD`) to avoid timezone mismatch between `session_date` (computed from UTC epoch in `extract_signals`) and `health_date` (computed with `datetime.now(timezone.utc)`).

---

### 3.4 Reporter

**File:** `cron/autoresearch/reporter.py`  
**Output:** `~/.hermes/autoresearch/nightly_report.md`

**Flagging thresholds:**
```python
CORRECTION_RATE_THRESHOLD = 0.30   # flag if > 30% of sessions have corrections
COMPLETION_RATE_THRESHOLD = 0.50   # flag if < 50% of sessions complete naturally
```

A skill is `FLAGGED ⚠` if either threshold is exceeded (both use strict inequality — at exactly 30% correction rate the skill is still OK).

**Report sections:**
1. Header with date
2. Sessions Analysed (count + skill count)
3. Skill Health table (7-day rolling: invocations, avg tokens, correction rate, completion rate, status)
4. Flagged Skills (detail with which threshold was exceeded)
5. Stage note ("Stage 1: Observe only. No patches applied.")

---

### 3.5 Entry Point

**File:** `cron/autoresearch/__init__.py`

`run_stage1()` orchestrates the pipeline in 5 steps:

```python
def run_stage1(state_db_path, metrics_db_path, report_path, hermes_home, since_hours=24):
    signals = extract_signals(state_db_path, since_hours, hermes_home)
    metrics_conn = open_db(metrics_db_path)
    for signal in signals:
        if not already_extracted(metrics_conn, signal["session_id"]):
            record_session_signal(metrics_conn, signal)
    health_rows = compute_and_store_skill_health(metrics_conn)
    summary = get_skill_health_summary(metrics_conn, days=7)
    metrics_conn.close()
    return generate_report(session_count=len(signals), skill_health=summary, report_path=report_path)
```

All path arguments have defaults that point to `HERMES_HOME`. Test code always passes explicit paths into `tmp_path` — no real filesystem is touched.

---

## 4. Tests

### 4.1 Test Philosophy

Every component has its own unit test file. The integration test (`test_autoresearch_stage1.py`) exercises the full pipeline with a real SQLite database in a temp directory. No mocks are used — all tests hit real SQLite files in `tmp_path`.

This approach was chosen deliberately: the prior implementation showed that mock-based tests can pass while real integration fails. Using real SQLite catches schema mismatches, wrong column names, and aggregation bugs that mocks would hide.

### 4.2 `test_skill_manager_source_tag.py` (11 tests)

**Tests the Slice 1 change: source tagging in `_append_skill_history()`.**

| Class | What it checks |
|-------|---------------|
| `TestDefaultSource` | `[in-session]` written when no source passed; appears in header line, not body; reason and file fields still present |
| `TestExplicitSource` | `[autoresearch]`, `[autoresearch: regression-watch]`, and arbitrary custom source written verbatim |
| `TestMultipleRecords` | Records accumulate (append-only); mixed sources coexist; each source tag appears on its own header |
| `TestHeaderFormat` | Header matches parseable regex `## TS — action [source]`; timestamp is valid UTC ISO-8601 |

**Key assertion** (`TestHeaderFormat::test_header_is_parseable`):
```python
HEADER_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — (\w+) \[(.+)\]$"
)
```
This regex is the contract between the writer (`_append_skill_history`) and any future parser (`hermes skills history`, Stage 3 regression watch).

### 4.3 `test_skill_metrics.py` (19 tests)

**Tests the SQLite DB layer.**

| Class | What it checks |
|-------|---------------|
| `TestOpenDb` | All three tables created; idempotent on second open |
| `TestRecordSessionSignal` | Row inserted; skills_invoked JSON-serialised; completion_flag stored as int; duplicate session_id replaces (not appends) |
| `TestAlreadyExtracted` | False before insert; True after; only exact ID matches |
| `TestComputeAndStoreSkillHealth` | Empty on no data; correct invocation_count, correction_rate, completion_rate; multiple skills bucketed separately; rows written to DB; idempotent rerun |
| `TestGetSkillHealthSummary` | Empty on no data; within-window rows included; old rows excluded; sorted by correction_rate DESC; multi-day aggregation with weighted averages |

**Key numeric test** (`test_aggregates_multiple_days`):
```python
# skill had 4 sessions at 0.25 correction and 6 sessions at 0.50 correction
# weighted average = (0.25*4 + 0.50*6) / 10 = 0.40
assert r["correction_rate"] == pytest.approx(0.4)
```

### 4.4 `test_signal_extractor.py` (26 tests)

**Tests signal extraction from state.db.**

| Class | What it checks |
|-------|---------------|
| `TestGracefulDegradation` | Missing DB → empty list; sessions outside window → empty list |
| `TestCountCorrections` | Empty messages → 0; known phrases detected (try again, that's wrong, incorrect, start over); assistant messages ignored; JSON content skipped; multiple corrections counted |
| `TestCheckCompletion` | `cli_close` / `user_quit` end_reason → True; unknown end_reason → False; thanks/perfect/looks good in last user msg → True; non-completion msg → False; only last user message matters |
| `TestDetectSkillsInPrompt` | Empty prompt/list → []; present skill detected; absent skill not detected; multiple skills; case-insensitive |
| `TestGetKnownSkillNames` | Missing skills dir → []; skill dirs with SKILL.md returned; dirs without SKILL.md excluded |
| `TestExtractSignals` | One signal per session; required keys present; total_tokens = input+output; correction_count from messages; skills_invoked from system prompt; session_date is UTC YYYY-MM-DD |

**Key design note:** The `make_row()` helper creates real `sqlite3.Row` objects using an in-memory DB. This is needed because `sqlite3.Row` can't be constructed directly — it must come from a query result.

### 4.5 `test_reporter.py` (18 tests)

**Tests the Markdown report generator.**

| Class | What it checks |
|-------|---------------|
| `TestSkillStatus` | OK within thresholds; FLAGGED when correction_rate > 30%; FLAGGED when completion_rate < 50%; exact threshold values are OK (strict >/<); None values don't crash |
| `TestReportContent` | Date in header; session count rendered; skill name in table; OK skill not in flagged section; flagged skill appears in flagged section; no-skills fallback message; "Stage 1 / No patches applied" footer; 3-skill count; correction rate as %; tokens with comma formatting |
| `TestFileOutput` | Report written to disk; returned text matches file content; parent directories created automatically |

### 4.6 `test_autoresearch_stage1.py` (6 tests)

**End-to-end integration tests for `run_stage1()`.**

| Test | What it checks |
|------|---------------|
| `test_no_state_db_returns_valid_report` | No crash when state.db missing; returns valid report |
| `test_report_written_to_disk` | Report file exists after run |
| `test_sessions_stored_in_metrics_db` | 2 sessions → 2 rows in session_signals |
| `test_idempotent_double_run` | Running twice → still only 1 row (not double-counted) |
| `test_flagged_skill_in_report` | Skill with 100% correction rate appears as FLAGGED in report |
| `test_session_count_in_report` | 4 sessions → "4" in report text |

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
                -v --override-ini="addopts="

Result: 93 passed, 0 failed, 1 warning (0.86s)
```

The 1 warning is a pre-existing asyncio deprecation in `conftest.py` — unrelated to autoresearch code.

**One bug caught during test run:** `compute_and_store_skill_health` originally used `date.today().isoformat()` (local date) as the target date, while `extract_signals` stores `session_date` using `datetime.fromtimestamp(..., tz=timezone.utc).strftime("%Y-%m-%d")` (UTC date). On machines where local date differs from UTC date (e.g. UTC+something after midnight local), signals written today in UTC wouldn't be found by the aggregation query. Fixed by changing the target date to `datetime.now(timezone.utc).strftime("%Y-%m-%d")` — consistently UTC throughout.

---

## 6. Verification Criteria

Stage 1 is complete when:

- [x] `run_stage1()` can be called with a real or missing `state.db` without crashing
- [x] New sessions are extracted and stored in `skill_metrics.db`
- [x] Re-running Stage 1 never double-counts sessions
- [x] `nightly_report.md` is written with correct date, counts, and flagged skills
- [x] All dates use UTC consistently
- [x] 93/93 tests pass
- [x] No skill files in `HERMES_HOME/skills/` are read or modified

---

## 7. What Stage 2 Will Add

Stage 2 will extend the pipeline after `get_skill_health_summary()`:

- For each FLAGGED skill, call an LLM to generate a candidate patch.
- Run the old and patched skill side-by-side on synthetic tasks (self-play evaluation).
- Use an LLM judge to score which version performs better.
- Write accepted candidates to `autoresearch_patches` with status `"candidate"`.

Stage 3 will apply accepted candidates (using the now-tagged `[autoresearch]` source), then run a regression watch for 24h before promoting the patch to `"confirmed"` or rolling it back.
