# Autoresearch Scheduling & Delivery — Implementation Document

> **Layer: Orchestration + Operations.**
> This layer wires the three autoresearch stages into a nightly schedule,
> exposes operator controls via `hermes autoresearch`, and delivers the
> nightly digest to Slack and Telegram.
> No skill files are ever modified here.

---

## 1. What This Layer Does

Stages 1–3 each have entry points (`run_stage1()`, `run_stage2()`, `run_stage3()`), but nothing called them on a schedule, nothing told operators what happened, and nothing sent the digest anywhere. This layer closes those gaps.

The four additions are:

1. **Runner** (`cron/autoresearch/runner.py`) — chains Stage 1 → 2 → 3 into a single `run_full_loop()` call, handles all inter-stage errors gracefully, sends the digest to configured messaging platforms, and writes `~/.hermes/autoresearch/state.json`.
2. **Scheduler tick** (`cron/scheduler.py:_tick_autoresearch()`) — called on every 60-second gateway beat. Reads the schedule from `config.yaml`, checks it against `state.json`, and fires `run_full_loop()` when the next scheduled occurrence has passed.
3. **CLI** (`hermes_cli/autoresearch.py`) — `hermes autoresearch` subcommands for operators to run, inspect, and configure the loop interactively.
4. **Config** (`hermes_cli/config.py`) — `autoresearch` section in `DEFAULT_CONFIG` for schedule, enabled flag, dry_run, and delivery platforms.

---

## 2. File Map

```
cron/autoresearch/
└── runner.py                    — NEW: run_full_loop(), deliver_digest(),
                                        save_run_state(), load_run_state()

cron/
└── scheduler.py                 — MODIFIED: _tick_autoresearch() added,
                                        called from tick() on every beat

hermes_cli/
├── autoresearch.py              — NEW: CLI handler for `hermes autoresearch`
└── config.py                    — MODIFIED: autoresearch section in DEFAULT_CONFIG

hermes_cli/
└── main.py                      — MODIFIED: autoresearch parser added,
                                        cmd_autoresearch() dispatch added,
                                        "autoresearch" added to _SUBCOMMANDS

~/.hermes/autoresearch/          (runtime, not in repo)
├── state.json                   — written by save_run_state() after each run
├── skill_metrics.db             — (Stage 1 output, read by Stage 2)
├── pending_patches.json         — (Stage 2 output, read by Stage 3)
└── nightly_digest.md            — (Stage 3 output, delivered by runner)
```

### New test files:

```
tests/cron/
├── test_runner.py               — runner unit tests (21 tests)
└── test_autoresearch_tick.py    — scheduler tick unit tests (10 tests)

tests/hermes_cli/
└── test_autoresearch_cli.py     — CLI handler unit tests (16 tests)
```

---

## 3. Components

### 3.1 Runner

**File:** `cron/autoresearch/runner.py`

The runner is the single callable that ties all three stages together. It is the only entry point that `_tick_autoresearch()` and `hermes autoresearch run` ever call.

#### `run_full_loop()`

```python
def run_full_loop(
    dry_run: bool = False,
    hermes_home: Optional[Path] = None,
    metrics_db_path: Optional[Path] = None,
    patches_path: Optional[Path] = None,
    digest_path: Optional[Path] = None,
    run_regression_watch: bool = True,
    skip_stage2: bool = False,
    state_path: Optional[Path] = None,
) -> str:
```

Execution order and error policy per stage:

| Stage | On success | On `ImportError` | On other exception |
|-------|-----------|------------------|--------------------|
| Stage 1 | continues | n/a | logs error, records first_error, continues |
| Stage 2 | continues | **graceful skip** (logged as warning) | logs error, records first_error, continues |
| Stage 3 | continues | n/a | logs error, returns error digest string |

**Stage 2 ImportError handling** is the most important rule. `run_stage2()` imports `agent.auxiliary_client` at call time. In deployments without an LLM configured, this raises `ImportError`. The runner catches it specifically, logs a warning, and skips Stage 2 — Stage 3 then applies any patches left from the previous run's `pending_patches.json`. This lets the apply-and-recover half of the loop work even without an LLM.

All other exceptions are caught, logged, and recorded in `error`. The loop continues to completion regardless. Stage 3 always runs (even if Stages 1 and 2 failed) because generating a digest with partial data is better than no digest at all.

After Stage 3 completes, `save_run_state()` is called unconditionally — even if all three stages failed, the run timestamp and error message are persisted.

**Return value:** The digest text from `run_stage3()`. If Stage 3 itself raised, a minimal error digest is returned instead.

---

#### `deliver_digest()`

```python
def deliver_digest(
    digest_text: str,
    platforms: List[str],
) -> Dict[str, Optional[str]]:
```

Sends `digest_text` to each named platform. For each entry:

1. Reads `{PLATFORM}_HOME_CHANNEL` env var (e.g. `SLACK_HOME_CHANNEL`, `TELEGRAM_HOME_CHANNEL`). If unset → returns error string, does not raise.
2. Imports `load_gateway_config, Platform` from `gateway.config`. If unavailable → error string.
3. Checks that the platform is configured and enabled in the gateway config.
4. Calls `_send_to_platform(platform, pconfig, chat_id, payload)` from `tools.send_message_tool`. If the event loop is already running (e.g. called from inside an async context), falls back to a `ThreadPoolExecutor` to run the coroutine.

**Return value:** `Dict[platform_name → error_string_or_None]`. Callers inspect this dict — `None` means success, a string means delivery failed with that reason. Errors are logged but never raised, so a Telegram failure does not prevent Slack delivery.

**All 15 gateway platforms** are in the platform map (slack, telegram, discord, whatsapp, signal, matrix, mattermost, homeassistant, dingtalk, feishu, wecom, weixin, email, sms, bluebubbles). Only the two env vars need to be set for Slack and Telegram delivery.

The header prepended to the digest on delivery:
```
Hermes Autoresearch — Nightly Digest
─────────────────────────────────────

<digest text>
```

---

#### `save_run_state()` / `load_run_state()`

```python
def save_run_state(status: str, error: Optional[str] = None,
                   state_path: Optional[Path] = None) -> None

def load_run_state(state_path: Optional[Path] = None) -> Dict[str, Any]
```

`state.json` schema:
```json
{
  "last_run_at": "2026-04-15T02:00:00+00:00",
  "last_status": "ok",
  "last_error":  null
}
```

`save_run_state()` writes atomically via `tempfile.mkstemp` + `os.replace` — no partial writes if the process is killed mid-run.

`load_run_state()` returns `{"last_run_at": None, "last_status": None, "last_error": None}` for any of: file missing, file unreadable, JSON malformed. Safe defaults prevent the scheduler from crashing when first deployed.

Default path: `HERMES_HOME/autoresearch/state.json`.

---

### 3.2 Scheduler Tick

**File:** `cron/scheduler.py` — `_tick_autoresearch()`

Called at the top of `tick()` on every 60-second gateway beat, before processing ordinary cron jobs. Uses its own schedule-check logic so it doesn't depend on the cron job registry.

**Decision logic:**

```
1. Load config.yaml → autoresearch.enabled?
   NO → return False (fast path, no I/O)

2. Load state.json → last_run_at

3. Compute base_dt:
   if last_run_at is set → use last_run_at
   if never ran         → use yesterday midnight (fires immediately on first deploy)

4. croniter(schedule, base_dt).get_next() → next_run
   if now < next_run → return False (not yet due)

5. Run:
   run_full_loop(dry_run=dry_run)
   if deliver_platforms:
       deliver_digest(digest, deliver_platforms)
   return True
```

**croniter dependency:** `croniter` is listed as an optional dependency. If not installed, `_tick_autoresearch()` catches the `ImportError` and returns `False` — the loop simply never fires, and everything else in the scheduler is unaffected.

**Error isolation:** All exceptions from `run_full_loop()` and `deliver_digest()` are caught and logged. The tick returns `True` (indicating the loop did attempt to run) even if it raised. Delivery errors per platform are logged individually. The tick never propagates an exception into `tick()`.

**Interaction with the cron lock:** `_tick_autoresearch()` runs inside the `tick()` file lock, so it cannot overlap with itself even if the gateway calls `tick()` twice in quick succession.

---

### 3.3 CLI

**File:** `hermes_cli/autoresearch.py`

**Main dispatcher:** `autoresearch_command(args) → int`

| Subcommand | What it does |
|------------|-------------|
| `run [--dry-run]` | Calls `run_full_loop(dry_run=...)`, prints the full digest to stdout. Returns 1 on exception. |
| `status` | Prints enabled/schedule/deliver from config + last_run_at/last_status/last_error from state.json. |
| `schedule <expr>` | Validates via `croniter.is_valid()` (if installed), saves to `config.yaml`. Returns 1 if expr missing. |
| `patches` | Reads `HERMES_HOME/autoresearch/pending_patches.json`, prints each patch with skill name, status icon, deltas, and reason. |
| `enable` | Sets `autoresearch.enabled = True` in config.yaml. |
| `disable` | Sets `autoresearch.enabled = False` in config.yaml. |

**Sample `status` output:**

```
Autoresearch status
───────────────────
  enabled:     True
  schedule:    0 2 * * *
  dry_run:     False
  deliver:     ['slack', 'telegram']

  last run:    2026-04-15T02:00:00+00:00
  last status: ✓ ok
```

**Sample `patches` output:**

```
Pending patches (2):

  1. [✓] git-workflow — accepted
       reason: Clarify branch naming convention
       token Δ=-10%  quality Δ=+0.50

  2. [✗] code-review — rejected
       reason: Improve error handling
       token Δ=+5%  quality Δ=-0.80
       rejected: token_delta >= 0
```

All config reads/writes go through `hermes_cli.config.load_config()` / `save_config()`, which handle deep-merge with `DEFAULT_CONFIG` and atomic YAML writes. The CLI never touches `state.json` directly — it reads it via `load_run_state()`.

---

### 3.4 Config

**File:** `hermes_cli/config.py` — `DEFAULT_CONFIG["autoresearch"]`

```python
"autoresearch": {
    "enabled":  True,
    "schedule": "0 2 * * *",   # nightly at 02:00
    "dry_run":  False,
    "deliver":  [],             # e.g. ["slack", "telegram"]
},
```

| Key | Type | Default | What it controls |
|-----|------|---------|-----------------|
| `enabled` | bool | `True` | Turns the loop on/off. Toggle with `hermes autoresearch enable/disable`. |
| `schedule` | str | `"0 2 * * *"` | Standard cron expression. Evaluated by `croniter` in `_tick_autoresearch()`. |
| `dry_run` | bool | `False` | Passed to `run_stage3(dry_run=...)`. Skill files are never modified; digest still generated. |
| `deliver` | list | `[]` | Platform names to deliver the digest to. Requires `{NAME}_HOME_CHANNEL` env var for each. |

The config is deep-merged with any user overrides in `~/.hermes/config.yaml`. Adding `autoresearch:` to `config.yaml` overrides only the specified keys; unspecified keys keep their defaults.

**`main.py` changes:**

- `"autoresearch"` added to `_SUBCOMMANDS` set — prevents `hermes -c session_id autoresearch ...` from being misparsed as a session continuation flag.
- `cmd_autoresearch(args)` dispatch function added alongside `cmd_cron`, `cmd_skills`, etc.
- Full subparser added: `autoresearch {run,status,schedule,patches,enable,disable}` with argparse arguments.

---

## 4. Tests

### 4.1 Test Philosophy

All three test files use `unittest.mock.patch` rather than real I/O. The runner and tick tests mock the three stage functions so the tests run in milliseconds without hitting the DB or filesystem. Delivery tests mock `sys.modules` to inject fake gateway and send_message modules — the same approach used elsewhere in the codebase to test gateway-dependent code without a live gateway.

The CLI tests mock `_load_config` / `_save_config` / `_load_run_state` at the helper level rather than at the config module level. This keeps the tests isolated from `config.yaml` parse logic and makes test intent obvious.

### 4.2 `test_runner.py` (21 tests)

| Class | What it checks |
|-------|---------------|
| `TestStatePersistence` | save/load round-trip; error state round-trip; missing file → safe defaults; malformed file → safe defaults; parent dirs created; no .tmp files left after atomic write |
| `TestRunFullLoop` | All stages succeed → returns digest + saves "ok" state; Stage 2 ImportError → skipped, Stage 3 runs, state="ok"; Stage 2 other exception → error state, Stage 3 still runs; Stage 1 exception → error state, Stage 3 still runs; Stage 3 exception → error digest returned, error state; skip_stage2=True → Stage 2 never called; dry_run=True → passed to Stage 3; state written even when all three stages fail |
| `TestDeliverDigest` | No env var → error string; unknown platform → error string; gateway ImportError → error string; multiple platforms all checked; successful delivery returns None; platform disabled → error string |

**Key invariant: Stage 2 ImportError ≠ error state.**
`test_stage2_import_error_skipped_gracefully` verifies that when Stage 2 skips because the LLM client is unavailable, `last_status` is `"ok"` — not `"error"`. This matters because a fresh Hermes install without an LLM configured will always trigger this path. Marking it as an error would make `hermes autoresearch status` permanently red for users who intentionally skip LLM configuration.

### 4.3 `test_autoresearch_tick.py` (10 tests)

| Test | What it checks |
|------|---------------|
| `test_disabled_returns_false` | `enabled=False` → immediate `False`, no I/O |
| `test_never_ran_calls_loop` | No `last_run_at` → base_dt=yesterday → loop called |
| `test_loop_not_yet_due_returns_false` | Next occurrence in future → `False`, loop not called |
| `test_loop_due_calls_run_full_loop` | Next occurrence in past → loop called, returns `True` |
| `test_deliver_called_with_platforms` | Configured platforms passed to `deliver_digest()` |
| `test_deliver_error_does_not_propagate` | Platform error string → logged, tick returns `True` |
| `test_run_full_loop_exception_returns_true` | Loop exception → caught, returns `True` |
| `test_croniter_not_installed_returns_false` | croniter absent → graceful `False` |
| `test_config_load_failure_returns_false` | Config exception → graceful `False` |
| `test_no_deliver_platforms_skips_deliver` | Empty deliver list → `deliver_digest` not called |

All tests inject a mock `croniter` module via `patch.dict(sys.modules, {"croniter": mock})` — this means tests run correctly on any machine regardless of whether `croniter` is installed.

### 4.4 `test_autoresearch_cli.py` (16 tests)

| Class | What it checks |
|-------|---------------|
| `TestRunCmd` | Calls `run_full_loop`, prints digest, returns 0; `--dry-run` passed through; exception → stderr + return 1 |
| `TestStatusCmd` | Schedule shown; last_run_at shown; last_error shown; never-run state shows "never" |
| `TestScheduleCmd` | Saves schedule to config; missing expr → return 1; prints confirmation |
| `TestPatchesCmd` | No file → "No pending_patches.json"; empty → "empty"; with patches → skill names shown |
| `TestEnableDisableCmd` | `enable` sets `enabled=True`; `disable` sets `enabled=False` |
| `TestUnknownSubcmd` | Unknown subcommand → return 1 |

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
                tests/cron/test_applier.py
                tests/cron/test_regression_watch.py
                tests/cron/test_digest.py
                tests/cron/test_autoresearch_stage3.py
                tests/cron/test_runner.py
                tests/cron/test_autoresearch_tick.py
                tests/hermes_cli/test_autoresearch_cli.py
                -q --override-ini="addopts="

Result: 303 passed, 0 failed, 1 warning (3.08s)
```

Breakdown:
- Stage 1 (93 tests): all pass, unchanged
- Stage 2 (81 tests): all pass, unchanged
- Stage 3 (82 tests): all pass, unchanged
- Scheduling & delivery new tests (47 tests): 21 + 10 + 16 = 47

---

## 6. Verification Criteria

The scheduling and delivery layer is complete when:

- [x] `run_full_loop()` chains all three stages, handles per-stage failures without aborting
- [x] Stage 2 `ImportError` (no LLM) is treated as a graceful skip, not an error
- [x] `state.json` written atomically after every run
- [x] `_tick_autoresearch()` fires `run_full_loop()` when the cron schedule is due
- [x] `_tick_autoresearch()` returns `False` gracefully when `croniter` is not installed
- [x] Delivery to Slack and Telegram via `{PLATFORM}_HOME_CHANNEL` env var
- [x] Delivery errors per platform are logged, not raised
- [x] `hermes autoresearch run` triggers the loop and prints the digest
- [x] `hermes autoresearch status` shows config + last-run state
- [x] `hermes autoresearch disable` / `enable` / `schedule` write to `config.yaml`
- [x] `autoresearch` in `_SUBCOMMANDS` — `hermes -c id autoresearch` parses correctly
- [x] 303/303 tests pass (all four stages combined)

---

## 7. Design Decisions

### Separate Runner from Scheduler

`_tick_autoresearch()` in the scheduler does only two things: decide whether to run, and call `run_full_loop()`. All orchestration logic lives in `runner.py`. This separation matters because `hermes autoresearch run` (CLI) and the nightly cron tick both need to call the same loop — if the orchestration lived inside `tick()`, the CLI would have to replicate it.

The scheduler also doesn't call `deliver_digest()` directly — it reads the `deliver` config and passes it to the runner's result. Delivery is part of the loop's contract, not part of scheduling.

### Stage 2 ImportError is Not an Error

The design choice to mark `last_status="ok"` when Stage 2 skips due to a missing LLM is deliberate. The distinction is:

- **ImportError from Stage 2** → LLM not configured → `pending_patches.json` from the previous run is used → Stage 3 applies what it can → system is operating correctly for its configuration.
- **RuntimeError from Stage 2** → LLM is configured but crashed → something is broken → `last_status="error"`.

Treating all Stage 2 failures as errors would produce a permanently red `status` output on any deployment that doesn't have `agent.auxiliary_client` installed — which includes most self-hosted Hermes instances without an external LLM.

### `_tick_autoresearch()` Inside the File Lock

`_tick_autoresearch()` runs inside the existing `fcntl`/`msvcrt` file lock in `tick()`. This means it can't run concurrently with itself even if the gateway's 60-second timer fires slightly early, or if a manual `hermes cron tick` command is run while the gateway is also ticking. The tradeoff is that the autoresearch loop adds latency to ordinary cron job processing on the beat it runs — but the loop is fast at schedule-check time (just reads a config and a JSON file), and only does real work once a night.

### Never-Ran Base Date

When `state.json` has no `last_run_at` (first deploy, state file deleted, etc.), `_tick_autoresearch()` sets `base_dt` to yesterday midnight. This means `croniter(schedule, yesterday_midnight).get_next()` is always in the past, so the loop fires on the very next tick after first deploy — without requiring any manual trigger. This "deploy and forget" behaviour is intentional: there is no `hermes autoresearch enable` step required after installation.

### `_SUBCOMMANDS` Registration

Hermes parses `hermes -c session_id autoresearch run` by splitting on the session continuation flag `-c`. If `"autoresearch"` is not in `_SUBCOMMANDS`, the parser sees `autoresearch` as a chat message to resume into the session named by the previous token, rather than as a subcommand. The `_SUBCOMMANDS` set is a fast lookup that runs before argparse — adding `"autoresearch"` to it costs nothing and prevents a class of silent misparse bugs.

### Delivery Uses `{PLATFORM}_HOME_CHANNEL`, Not a Per-Run Target

The delivery target is read from an env var at delivery time, not stored in `state.json` or `pending_patches.json`. This matches the convention used throughout the rest of the codebase (`_resolve_delivery_target()` in `scheduler.py` uses the same pattern for cron job delivery). It also means the delivery target can be changed without touching any stored state — update the env var, next run delivers to the new channel.

---

## 8. Complete Autoresearch Loop: End-to-End Flow

All four layers are now implemented. The complete nightly cycle from signal extraction to operator notification:

```
_tick_autoresearch() fires (02:00, via cron/scheduler.py tick())
│
└─→ run_full_loop()
    │
    ├─→ Stage 1 (Observe):
    │     extract_signals(state.db)
    │     → record_session_signal(skill_metrics.db)
    │     → compute_skill_health(skill_metrics.db)
    │     → nightly_report.md
    │
    ├─→ Stage 2 (Hypothesize + Evaluate):
    │     detect_anomalies(skill_metrics.db)
    │     → generate_hypothesis(LLM)         ← skipped if LLM unavailable
    │     → evaluate_candidate(LLM self-play)
    │     → pending_patches.json
    │
    ├─→ Stage 3 (Apply + Recover):
    │     apply_patches(recency lock + stale guard + dry_run)
    │       → SKILL.md updated atomically
    │       → SKILL_HISTORY.md tagged [autoresearch]
    │       → autoresearch_patches DB row
    │     check_regressions(causation check + rollback threshold)
    │       → SKILL.md restored if regression
    │       → SKILL_HISTORY.md tagged [autoresearch: regression-watch]
    │     → nightly_digest.md
    │
    ├─→ save_run_state(state.json)
    │
    └─→ deliver_digest(["slack", "telegram"])
          → Slack: #hermes-digest
          → Telegram: @hermes_bot
```

Total: 303 tests across 17 test files, all passing.
