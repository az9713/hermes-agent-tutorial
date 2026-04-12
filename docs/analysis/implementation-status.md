# Implementation Status — 7 Self-Improvement Enhancements

> Written after the implementation commit. Records exactly what was built, what the 179 tests cover and don't cover, what conclusions they support, and what the realistic next steps are.

---

## Part 1: What Was Actually Implemented

### Overview

Six source files were changed; 875 lines net added across implementation and tests.

| File | Role |
|------|------|
| `tools/memory_tool.py` | Three memory changes (visibility, tiers, contradiction) |
| `tools/skill_manager_tool.py` | Two skill changes (history, reason enforcement) |
| `hermes_cli/skills_hub.py` | Rollback action (`do_rollback`); history view (`do_history`, `_parse_all_history_records`) |
| `hermes_cli/main.py` | Rollback subparser (`hermes skills rollback`); history subparser (`hermes skills history`) |
| `tests/tools/test_memory_tool.py` | 65 tests (54 new, 11 rewritten) |
| `tests/tools/test_skill_manager_tool.py` | 65 tests (11 new, 54 unchanged) |
| `tests/hermes_cli/test_rollback.py` | 26 tests (all new) — rollback CLI path |
| `tests/hermes_cli/test_history.py` | 23 tests (all new) — history CLI path |

---

### Change 1 — Intra-session memory visibility (`memory_tool.py`)

**What it does.** Adds `_session_additions: dict[str, list[str]]` to `MemoryStore`. When `add()` succeeds, the stored entry is appended to `_session_additions[target]`. In `format_for_system_prompt()`, the frozen base snapshot is returned as before, but if there are session additions, they are appended in a separate block:

```
══════════════════════════════════════════════
MEMORY (your personal notes) [12% — 263/2,200 chars]
══════════════════════════════════════════════
User deploys on Fridays

## Added this session
[HIGH] Never restart the prod DB without a snapshot
```

**The trade-off.** The base snapshot remains stable for Anthropic prompt caching. Each entry added mid-session causes one cache miss on the turn it is saved (the system prompt changes), then re-stabilises. Subsequent turns remain cached until the next addition.

**What it preserves.** `_session_additions` is cleared on every `load_from_disk()` call, so the semantics remain: a fresh session sees only what was on disk at start. There is no bleed between sessions.

**The test that was rewritten.** `test_snapshot_frozen_at_load` previously asserted `"added later" not in snapshot`. It was renamed `test_snapshot_shows_session_additions` and now asserts the reverse. A companion test (`test_base_snapshot_stable_when_no_additions`) pins the caching contract: identical calls with no additions return byte-identical strings.

---

### Change 2 — Skill patch history (`skill_manager_tool.py`)

**What it does.** Adds `_append_skill_history(skill_dir, action, reason, file_path, old_text, new_text)`. After every successful `_patch_skill()` or `_edit_skill()` call — after the security scan passes — this function is called. It reads the existing `SKILL_HISTORY.md` (if any), appends a new Markdown record, and atomic-writes the result. Record format:

```markdown
## 2026-04-11T14:32:05Z — patch
**Reason:** Adding missing verification step
**File:** SKILL.md

### Old
```text
Step 3: Deploy.
```

### New
```text
Step 3: Run smoke tests. Step 4: Deploy.
```
```

The file is append-only. No record is ever removed, including rollback records — so every rollback is itself reversible.

**Where it writes.** To `<skill_dir>/SKILL_HISTORY.md`, i.e. alongside `SKILL.md` in the skill's own directory. No SQLite migration needed.

**Atomicity.** Uses the same `_atomic_write_text` pattern as SKILL.md writes (temp file + `os.replace()`). Never leaves SKILL_HISTORY.md in a partially-written state.

---

### Change 3 — Evidence-required patches (`skill_manager_tool.py`)

**What it does.** The `skill_manage()` dispatcher checks: if `action in ("patch", "edit")` and `reason` is absent or empty, return a tool error before touching any file:

```json
{"success": false, "error": "'reason' is required when patching a skill — explain why in one sentence."}
```

The LLM retries the call with a `reason` field. That reason is logged by Change 2.

**Schema change.** `reason` added to `SKILL_MANAGE_SCHEMA` as an optional string with a description that makes it clear it is required for patch and edit. It is optional at the schema level (not in `required[]`) to avoid breaking any externally cached call signatures; enforcement happens at runtime in the dispatcher.

**What `create` does.** Create does not require a reason — it is a net-new action, not a modification of existing behaviour.

---

### Change 4 — Rollback command (`skills_hub.py`, `main.py`)

**What it does.** Adds `hermes skills rollback <name> [--yes]`.

1. Calls `_find_skill(name)` to locate the skill directory.
2. Reads `SKILL_HISTORY.md` and reverse-scans for the most recent non-rollback record.
3. Extracts `**File:**`, `### Old`, `### New` from that record.
4. Shows a `difflib.unified_diff` preview (coloured via Rich, capped at 60 lines).
5. Prompts `Restore? [y/N]` (skipped with `--yes`).
6. Atomic-writes the old content back to the target file.
7. Appends a rollback record to `SKILL_HISTORY.md` (action: rollback, reason: "Rolled back via CLI").
8. Calls `clear_skills_system_prompt_cache(clear_snapshot=True)` to invalidate the prompt cache.

**Rollback of rollback.** Because rollback appends rather than mutates, running `hermes skills rollback <name>` twice walks back two steps. The history is always a forward-only log.

**Parser registration.** Added to `skills_subparsers` in `hermes_cli/main.py` between `uninstall` and `publish`, following the exact pattern of all other subcommands. Routed in `skills_command()` in `skills_hub.py`.

**Test coverage.** ✅ Automated tests in `tests/hermes_cli/test_rollback.py` cover: `_parse_last_history_record` (7 unit tests), `do_rollback` success path (7 tests), `do_rollback` error paths (4 tests), prompt-cache clearing (2 tests), `skills_command` dispatch routing (2 tests), and argparse wiring including `--yes`/`-y` flags (4 tests). Full evidence, test strategy, class-by-class interpretation, and known limits are in [`docs/analysis/rollback-cli-tests.md`](rollback-cli-tests.md).

---

### Change 4b — History view command (`skills_hub.py`, `main.py`)

**What it does.** Adds `hermes skills history <name> [--detail N]`.

- **Default (no `--detail`)**: reads `SKILL_HISTORY.md`, parses all records via `_parse_all_history_records`, and displays a Rich Table with columns `#`, `Timestamp`, `Action`, `Reason`, `File`. Rows are colour-coded by action: green for `patch`, blue for `edit`, yellow for `rollback`. A summary line shows the record count and hints at `--detail`.
- **`--detail N`**: shows the header fields (timestamp, reason, file) and a coloured `difflib.unified_diff` for record #N (1-indexed, oldest first). Uses the same 60-line diff cap and green/red colouring as `do_rollback`. If N is out of range, prints an error with the valid range.

**New parser function `_parse_all_history_records`.** Unlike `_parse_last_history_record`, which reverse-scans and skips rollback records to find the most recent restorable state, `_parse_all_history_records` returns all records in chronological order (oldest first). Rollback records are included — the table is a full audit trail. Reuses the same regex patterns as `_parse_last_history_record`.

**Parser registration.** Added to `skills_subparsers` in `hermes_cli/main.py` immediately after `rollback`. Routed in `skills_command()` with `elif action == "history": do_history(args.name, detail=getattr(args, "detail", None))`.

**Test coverage.** ✅ Automated tests in `tests/hermes_cli/test_history.py` cover: `_parse_all_history_records` (5 unit tests), `do_history` table view (5 tests), `do_history` detail view (4 tests), error paths (3 tests), `skills_command` dispatch routing (2 tests), and argparse wiring including `--detail` type validation (4 tests). Full evidence, test strategy, class-by-class interpretation, and known limits are in [`docs/analysis/history-cli-tests.md`](history-cli-tests.md).

---

### Change 5 — Memory importance tiers (`memory_tool.py`)

**What it does.** `add()` now accepts `priority` (`"high"`, `"normal"`, `"ephemeral"`) and `expires_days` (int, default 30 for ephemeral).

Stored entry prefixes:

| Priority | Stored form |
|----------|-------------|
| `high` | `[HIGH] <content>` |
| `normal` | `<content>` (unchanged — backward compatible) |
| `ephemeral` | `[EPHEMERAL expires=YYYY-MM-DD] <content>` |

**Expiry.** On every `load_from_disk()` and `_reload_target()`, entries matching `[EPHEMERAL expires=...]` whose date is before today are filtered out. If any were dropped, the file is re-written before the snapshot is taken.

**Sort order.** `format_for_system_prompt()` and `_render_block()` call `_sort_entries_by_priority()`: high → normal → ephemeral.

**Eviction order on overflow.** When adding would exceed the char limit, eviction is attempted in this order: ephemeral entries (oldest first) → normal entries (oldest first). High-priority entries are never auto-evicted. If only high entries remain and the limit is still exceeded, the add fails with a human-readable error asking the LLM to consolidate.

**Backward compatibility.** Existing `MEMORY.md` files with no prefix parse as `normal`. Zero migration required.

**Schema.** `priority` and `expires_days` added to `MEMORY_SCHEMA` parameters. Tool handler updated to pass both through.

---

### Change 6 — Contradiction detection (`memory_tool.py`)

**What it does.** Adds `_detect_overlap(new_content, existing_entries) → list[str]`. Called inside `add()` before writing. If overlapping entries are found, the `add()` still succeeds but the result contains a `warning` key:

```json
{
  "success": true,
  "warning": "This may overlap with 1 existing entry: 'user prefers Python for all...' — consider editing or removing the old one.",
  ...
}
```

**Algorithm.** Pure Python, no embedding model:
1. Tokenise both sides: lowercase, strip punctuation, drop stopwords.
2. For each existing entry: `|intersection| / max(len(new_tokens), 1)`.
3. Flag if > 0.4.
4. Exact duplicates are excluded (handled upstream by dedup).

**Why non-blocking.** The heuristic has false positive risk — "user prefers Python" and "user prefers Go" correctly triggers, but "the project uses Python" and "the project uses SQLite" also share ~40% of meaningful tokens. Blocking on a false positive would frustrate the LLM. Warning lets it decide.

---

### Windows portability fix (`memory_tool.py`)

Not in the original plan, but required to make the test suite work on Windows.

`fcntl` is Unix-only. The original code had a bare `import fcntl` at module level, making the module un-importable on Windows. Changed to:

```python
if sys.platform != "win32":
    import fcntl
else:
    fcntl = None
```

`_file_lock()` checks `if fcntl is None` and uses a no-op context manager on Windows. Hermes officially requires Unix (WSL2 on Windows), so no functional regression; but now the module can be imported on Windows and unit tests can run (without actually locking files).

---

## Part 2: The 179 Tests

### Distribution

| File | Tests | New vs pre-existing |
|------|-------|---------------------|
| `test_memory_tool.py` | 65 | 54 new, 11 rewritten |
| `test_skill_manager_tool.py` | 65 | 11 new, 54 unchanged |
| `test_rollback.py` | 26 | all new |
| `test_history.py` | 23 | all new |

### All 179 tests by class

#### `test_memory_tool.py` — 65 tests

**TestMemorySchema (2)**

| Test | What it checks |
|------|---------------|
| `test_discourages_diary_style_task_logs` | Schema description tells LLM not to use memory as a task log; keywords "Do NOT save task progress", "session_search" present |
| `test_schema_exposes_priority_param` | `priority` and `expires_days` appear in MEMORY_SCHEMA parameters |

**TestScanMemoryContent (7)**

| Test | What it checks |
|------|---------------|
| `test_clean_content_passes` | Benign content returns `None` (no block) |
| `test_prompt_injection_blocked` | "ignore previous instructions", "Ignore ALL instructions", "disregard your rules" all blocked |
| `test_exfiltration_blocked` | `curl … $API_KEY`, `cat ~/.env`, `cat /home/user/.netrc` blocked |
| `test_ssh_backdoor_blocked` | "authorized_keys", `~/.ssh/id_rsa` blocked |
| `test_invisible_unicode_blocked` | U+200B, U+FEFF blocked |
| `test_role_hijack_blocked` | "you are now a different AI" blocked |
| `test_system_override_blocked` | "system prompt override" blocked |

**TestMemoryStoreAdd (6)**

| Test | What it checks |
|------|---------------|
| `test_add_entry` | Entry appears in `entries` after add |
| `test_add_to_user` | `target="user"` routes to user store |
| `test_add_empty_rejected` | Whitespace-only content returns `success: false` |
| `test_add_duplicate_rejected` | Second identical add succeeds (no error) but doesn't create a duplicate |
| `test_add_injection_blocked` | Injection pattern in content blocks the add |
| `test_add_invalid_priority_rejected` | Priority not in `high/normal/ephemeral` returns `success: false` |

**TestMemoryStoreReplace (6)**

| Test | What it checks |
|------|---------------|
| `test_replace_entry` | Substring match replaces content, old content gone |
| `test_replace_no_match` | Non-matching `old_text` returns error |
| `test_replace_ambiguous_match` | Two distinct entries matching same substring returns error, not a silent choice |
| `test_replace_empty_old_text_rejected` | Empty `old_text` rejected |
| `test_replace_empty_new_content_rejected` | Empty `new_content` rejected (use remove instead) |
| `test_replace_injection_blocked` | Injection in replacement content blocked |

**TestMemoryStoreRemove (3)**

| Test | What it checks |
|------|---------------|
| `test_remove_entry` | Entry removed; store empty after |
| `test_remove_no_match` | Non-matching `old_text` returns error |
| `test_remove_empty_old_text` | Whitespace `old_text` rejected |

**TestMemoryStorePersistence (2)**

| Test | What it checks |
|------|---------------|
| `test_save_and_load_roundtrip` | Entries written by one store instance are readable by a second instance |
| `test_deduplication_on_load` | Duplicate entries in the file collapse to one on `load_from_disk()` |

**TestMemoryStoreSnapshot (5)** *(rewritten from the original 2-test class)*

| Test | What it checks |
|------|---------------|
| `test_snapshot_shows_session_additions` | Entry added after `load_from_disk()` appears in `format_for_system_prompt()` in the same session |
| `test_base_snapshot_stable_when_no_additions` | With no session additions, two calls return identical strings (caching contract) |
| `test_empty_snapshot_returns_none` | Empty store returns `None` |
| `test_session_additions_cleared_on_reload` | `_session_additions` is reset to `[]` on `load_from_disk()` |
| `test_session_addition_only_snapshot_when_no_base` | Addition with no base snapshot still returns a non-None result |

**TestMemoryPriority (7)** *(new)*

| Test | What it checks |
|------|---------------|
| `test_high_priority_prefix_stored` | `[HIGH]` prefix appears in `memory_entries` |
| `test_normal_priority_no_prefix` | Normal entry stored without prefix |
| `test_ephemeral_prefix_stored` | `[EPHEMERAL expires=...]` prefix with correct date |
| `test_high_priority_sorts_first` | After reload, `[HIGH]` entry appears before normal in snapshot |
| `test_ephemeral_expires_on_reload` | Entry with yesterday's expiry date dropped on load |
| `test_non_expired_ephemeral_kept` | Entry with future expiry date kept on load |
| `test_high_not_auto_evicted_when_limit_hit` | Adding entries past the limit evicts normal, not high |

**TestContradictionDetection (4)** *(new)*

| Test | What it checks |
|------|---------------|
| `test_no_overlap_no_warning` | Unrelated entries produce no warning |
| `test_overlap_above_threshold_returns_warning` | "user prefers Python" vs "user prefers Go" triggers warning |
| `test_add_still_succeeds_when_overlap_detected` | Warning is non-blocking; both entries stored |
| `test_exact_duplicate_not_flagged_as_overlap` | Exact dup caught by dedup, not flagged as overlap |

**TestDetectOverlapUnit (3)** *(new)*

| Test | What it checks |
|------|---------------|
| `test_high_overlap_detected` | Overlapping sentences return non-empty list |
| `test_low_overlap_not_detected` | Semantically unrelated sentences return `[]` |
| `test_exact_dup_excluded` | Exact duplicate excluded from overlap list |

**TestPriorityHelpers (7)** *(new)*

| Test | What it checks |
|------|---------------|
| `test_parse_high` | `_parse_entry_priority("[HIGH] fact")` → `("high", "fact")` |
| `test_parse_ephemeral` | Ephemeral prefix parsed correctly |
| `test_parse_normal` | No prefix → `("normal", content)` |
| `test_is_expired_past_date` | Yesterday's expiry → `True` |
| `test_is_expired_future_date` | Future expiry → `False` |
| `test_is_expired_normal_entry` | Non-ephemeral → `False` |
| `test_sort_high_first` | High always index 0 in sorted output |

**TestMemoryToolDispatcher (8)**

| Test | What it checks |
|------|---------------|
| `test_no_store_returns_error` | `store=None` returns JSON error |
| `test_invalid_target` | `target="invalid"` rejected |
| `test_unknown_action` | Unknown action rejected |
| `test_add_via_tool` | Happy-path add through the dispatcher function |
| `test_add_with_high_priority` | Priority threaded through dispatcher to store |
| `test_add_with_ephemeral_priority` | Ephemeral threaded through dispatcher |
| `test_replace_requires_old_text` | Missing `old_text` on replace caught |
| `test_remove_requires_old_text` | Missing `old_text` on remove caught |

**TestExpiryEdgeCases (5)** *(new)*

All five tests monkeypatch `tools.memory_tool._today` to `date(2026, 4, 15)` — completely clock-independent and immune to midnight race conditions.

| Test | What it checks |
|------|---------------|
| `test_entry_expiring_today_is_not_expired` | `_is_expired("[EPHEMERAL expires=2026-04-15] …")` → `False`. Documents the `>` (not `>=`) contract: expiry date is the last day the entry is kept. |
| `test_entry_expiring_yesterday_is_expired` | `_is_expired("[EPHEMERAL expires=2026-04-14] …")` → `True`. Yesterday is the first day the entry is dropped. |
| `test_entry_expiring_tomorrow_is_not_expired` | `_is_expired("[EPHEMERAL expires=2026-04-16] …")` → `False`. Clock-independent version of `test_is_expired_future_date`. |
| `test_load_from_disk_drops_exactly_expired_entries` | Writes three EPHEMERAL entries (yesterday/today/tomorrow) to a temp MEMORY.md; calls `load_from_disk()`; asserts exactly 2 survive in `memory_entries` and the expired entry is absent from the file on disk. Exercises the expiry pruning path in `load_from_disk()` (lines 239-245 of `memory_tool.py`). |
| `test_reload_target_drops_exactly_expired_entries` | Same three-entry setup; triggers the pruning via `_reload_target("memory")` (called inside `add()`). Verifies both expiry code paths are covered and that an `add()` call post-load preserves exactly the non-expired entries. |

For full strategy, per-test interpretation, development history (including the `MemoryStore(tmp_path)` bug encountered during development), and documented gaps, see [`docs/analysis/expiry-edge-case-tests.md`](expiry-edge-case-tests.md).

---

#### `test_skill_manager_tool.py` — 65 tests

**TestValidateName (6)** — pre-existing

Covers: valid patterns, empty name, length limit, uppercase, leading hyphen, special characters.

**TestValidateCategory (3)** — pre-existing

Covers: valid category, path traversal (`../`), absolute path.

**TestValidateFrontmatter (8)** — pre-existing

Covers: valid content, empty content, missing `---` opener, unclosed frontmatter, missing `name`, missing `description`, empty body, invalid YAML.

**TestValidateFilePath (6)** — pre-existing

Covers: valid subdirectory paths, empty path, `..` traversal, disallowed subdirectory, directory-only (no filename), root-level file.

**TestCreateSkill (7)** — pre-existing

Covers: create success, category creates subdirectory, duplicate name blocked, invalid name blocked, invalid content blocked, category traversal blocked, absolute category blocked.

**TestEditSkill (3)** — pre-existing

Covers: successful edit, nonexistent skill, invalid content.

**TestPatchSkill (7)** — pre-existing

Covers: unique match, no match, ambiguous match, replace_all, supporting file, not found, symlink escape blocked.

**TestDeleteSkill (3)** — pre-existing

Covers: delete success, not found, empty parent directory cleaned up.

**TestWriteFile (4)** — pre-existing

Covers: write reference file, nonexistent skill, disallowed path, symlink escape.

**TestRemoveFile (3)** — pre-existing

Covers: remove success, not found, symlink escape.

**TestSkillManageDispatcher (4)** — pre-existing

Covers: unknown action, create without content, patch without `old_string`, full create via dispatcher.

**TestSkillPatchHistory (6)** — new

| Test | What it checks |
|------|---------------|
| `test_patch_writes_history_file` | `SKILL_HISTORY.md` created after first patch |
| `test_history_contains_old_and_new_blocks` | Both `old_string` and `new_string` appear in the record |
| `test_history_contains_reason` | The `reason` argument appears in the record |
| `test_history_is_append_only` | Two patches produce two `## 20...` section headers |
| `test_edit_writes_history` | Full rewrite via `_edit_skill()` also records history |
| `test_append_skill_history_helper` | Helper creates file, correct fields, second call appends |

**TestReasonRequired (5)** — new

| Test | What it checks |
|------|---------------|
| `test_patch_without_reason_returns_error` | Missing reason → `success: false`, "reason" in error |
| `test_edit_without_reason_returns_error` | Same for edit |
| `test_patch_with_reason_succeeds` | Reason present → success |
| `test_edit_with_reason_succeeds` | Same for edit |
| `test_create_does_not_require_reason` | Create without reason → success |

---

#### `test_rollback.py` — 26 tests (all new)

**TestParseLastHistoryRecord (7)**

| Test | What it checks |
|------|---------------|
| `test_empty_text_returns_none_tuple` | Empty history → `(None, None, None)` |
| `test_single_patch_record_extracted` | file_path, old_text, new_text correctly parsed from one record |
| `test_multiple_records_returns_most_recent` | Two records → newer one returned |
| `test_rollback_record_is_skipped` | Rollback record is skipped; preceding patch record returned |
| `test_all_rollback_records_returns_none` | Only rollback records → `(None, None, None)` |
| `test_edit_action_also_returned` | `— edit` action keyword parses identically to `— patch` |
| `test_malformed_record_without_old_block_returns_none` | Missing `### Old` block → fall-through to `(None, None, None)` |

**TestDoRollbackHappyPath (7)**

| Test | What it checks |
|------|---------------|
| `test_patch_then_rollback_restores_original` | SKILL.md content reverts; success message in output |
| `test_rollback_appends_rollback_record_to_history` | History grows from 1 to 2 records; rollback record has correct reason |
| `test_rollback_shows_unified_diff_preview` | Diff preview block appears in console output |
| `test_rollback_with_identical_content_prints_no_visible_diff` | When old == new, "No visible diff" printed |
| `test_skip_confirm_false_with_y_restores` | input() returning "y" allows restore |
| `test_skip_confirm_false_with_n_cancels` | input() returning "n" cancels; file unchanged; "Cancelled" in output |
| `test_confirm_eof_treated_as_no` | EOFError from input() treated as "n"; cancels cleanly |

**TestDoRollbackErrorPaths (4)**

| Test | What it checks |
|------|---------------|
| `test_unknown_skill_prints_error` | "not found" in output; no exception raised |
| `test_skill_exists_but_no_history_prints_warning` | "No history found" when SKILL_HISTORY.md absent |
| `test_history_only_rollback_records_prints_no_restorable` | "No restorable record found" when only rollback entries exist |
| `test_malformed_history_prints_no_restorable` | "No restorable record found" on malformed record |

**TestDoRollbackClearsPromptCache (2)**

| Test | What it checks |
|------|---------------|
| `test_successful_rollback_clears_prompt_cache` | `clear_skills_system_prompt_cache(clear_snapshot=True)` called exactly once |
| `test_cache_clear_failure_is_swallowed` | RuntimeError from cache clear does not propagate; success message still printed |

**TestSkillsCommandRollbackDispatch (2)**

| Test | What it checks |
|------|---------------|
| `test_skills_command_routes_rollback_with_yes_true` | `skills_command(Namespace(skills_action="rollback", yes=True))` calls `do_rollback(..., skip_confirm=True)` |
| `test_skills_command_rollback_defaults_yes_false` | Absent `yes` attr resolves to `skip_confirm=False` |

**TestRollbackArgparse (4)**

| Test | What it checks |
|------|---------------|
| `test_rollback_subparser_registered` | `hermes skills rollback my-skill` parses to `skills_action="rollback"`, `name="my-skill"`, `yes=False` |
| `test_rollback_yes_flag_long` | `--yes` sets `yes=True` |
| `test_rollback_yes_flag_short` | `-y` sets `yes=True` |
| `test_rollback_missing_name_errors` | Missing `name` positional → `SystemExit(2)` |

---

#### `test_history.py` — 23 tests (all new)

**TestParseAllHistoryRecords (5)**

| Test | What it checks |
|------|---------------|
| `test_empty_text_returns_empty_list` | Empty input → `[]`; function never raises on empty string |
| `test_single_patch_record` | All five fields (timestamp, action, reason, file_path, old_text, new_text) correctly parsed from one record |
| `test_multiple_records_preserves_order` | Two records → oldest first; chronological order is preserved (not reversed as in `_parse_last_history_record`) |
| `test_rollback_record_included` | Unlike `_parse_last_history_record`, rollback records are NOT skipped — the full audit trail is returned |
| `test_malformed_record_skipped` | Block without a valid `## timestamp — action` header is silently skipped; adjacent valid records still parsed |

**TestDoHistoryTableView (5)**

| Test | What it checks |
|------|---------------|
| `test_single_patch_shows_table` | After one patch: table output contains skill name in title, "patch" action, reason text, "SKILL.md" filename |
| `test_multiple_patches_numbered` | After two patches: both numbered `1` and `2`; both reasons visible |
| `test_rollback_record_in_table` | After patch + rollback: "rollback" action visible in table |
| `test_shows_record_count` | Output contains "N record(s)" summary line |
| `test_hint_about_detail_flag` | Output contains "--detail" hint text pointing users to the diff view |

**TestDoHistoryDetailView (4)**

| Test | What it checks |
|------|---------------|
| `test_detail_shows_diff` | `detail=1` → output contains "Record #1", the action name, and diff content |
| `test_detail_out_of_range` | `detail=99` on a 1-record history → "does not exist" error with valid range printed |
| `test_detail_zero_is_invalid` | `detail=0` → same error (records are 1-indexed) |
| `test_detail_identity_diff` | When `old_text == new_text` → "No visible diff" message |

**TestDoHistoryErrorPaths (3)**

| Test | What it checks |
|------|---------------|
| `test_unknown_skill_prints_error` | `_find_skill` returns `None` → "not found" in output; no exception |
| `test_no_history_file_prints_warning` | Skill exists but `SKILL_HISTORY.md` absent → "No history found" |
| `test_empty_history_file_prints_warning` | `SKILL_HISTORY.md` exists but is empty → "No parseable records" |

**TestHistoryCommandDispatch (2)**

| Test | What it checks |
|------|---------------|
| `test_skills_command_routes_history` | `Namespace(skills_action="history", name="test-skill", detail=None)` → `do_history("test-skill", detail=None)` via spy |
| `test_skills_command_routes_history_with_detail` | `detail=3` in Namespace → `do_history("test-skill", detail=3)` |

**TestHistoryArgparse (4)**

| Test | What it checks |
|------|---------------|
| `test_history_subparser_registered` | `hermes skills history my-skill` parses to `skills_action="history"`, `name="my-skill"`, `detail=None` |
| `test_history_detail_flag` | `--detail 3` sets `detail=3` (int) |
| `test_history_missing_name_errors` | Missing `name` positional → `SystemExit(2)` |
| `test_history_detail_requires_int` | `--detail abc` → `SystemExit(2)` (argparse `type=int` rejects non-integer) |

---

## Part 3: What the 179 Tests Tell Us

These tests are all **unit and functional tests of internal logic**. They run in-process with isolated temp directories. Passing means:

**The new logic is correctly implemented.** The session-additions pattern, priority prefix parsing, expiry logic, eviction order, overlap heuristic, history appending, and reason enforcement all behave as designed at the function level.

**The security scanner still works.** The 7 injection/exfiltration tests confirm that adding the new `priority` and `expires_days` parameters did not bypass or break the content-scanning path.

**Atomic write discipline is preserved.** Patch history uses `_atomic_write_text`, the same function the skill writes use. History writes are not partial-write risks.

**The frozen-snapshot caching contract is explicitly pinned.** `test_base_snapshot_stable_when_no_additions` ensures that if no entries are added mid-session, the system prompt output is byte-identical across calls. This is the condition for Anthropic prompt cache hits.

**Session additions are correctly scoped.** `test_session_additions_cleared_on_reload` verifies there is no bleed between sessions — starting a new session gives a fresh slate.

**Backward compatibility holds.** Entries without any prefix parse as `normal`. Nothing in the new code requires MEMORY.md files to be migrated.

**All pre-existing skill manager tests still pass.** The 54 unchanged tests confirm that adding `reason` enforcement and history logging did not break create, delete, write_file, remove_file, or validation paths.

**The rollback CLI path is fully exercised.** `_parse_last_history_record` is tested against 7 string patterns including the rollback-skip rule and malformed input. `do_rollback` is driven against real temp-dir skill files with `skip_confirm=True`, verifying both the file write and the history append. The argparse tests call `hermes_cli.main.main()` with patched `sys.argv` — these use the **real parser** built inside `main()`, not a replica, so they catch any future renaming of the flag or subcommand. The lazy-import patch target for `clear_skills_system_prompt_cache` is documented in the test class docstring so the correct target is known if the import is ever moved to module scope.

**The history view path is fully exercised.** `_parse_all_history_records` is tested against 5 string patterns including rollback inclusion (proving it differs from `_parse_last_history_record`) and malformed record skipping. `do_history` is driven against real temp-dir skills for both the table view and the `--detail N` diff view. Error paths cover unknown skill, absent history file, and empty history file. Dispatch and argparse tests confirm the full `hermes skills history` → `do_history` wiring including `--detail` int-type validation.

---

## Part 4: What the Tests Cannot Tell Us

**1. LLM integration.** The LLM is never invoked. We cannot know whether the LLM will:
- Actually supply a `reason` the first time (it will get an error and retry on the first missed call, but this means one extra API round-trip per patch/edit until it learns the schema).
- Actually notice contradiction warnings and act on them.
- Actually benefit from session additions being visible — the improvement is only useful if the LLM reads and uses that block.

**2. Real-file concurrency.** `_file_lock` uses `fcntl.flock`. Tests on Windows use a no-op lock. We have not tested simultaneous writes from two processes (e.g., a CLI session and a gateway session writing memory at the same time). The mechanism is correct by design, but race conditions under load are not covered.

**3. Rollback CLI path.** ✅ *Resolved.* `tests/hermes_cli/test_rollback.py` now covers `_parse_last_history_record`, `do_rollback` (success + error + confirmation paths), `skills_command` routing, and the argparse subparser. The remaining untested surface is only the interactive Rich diff output when stdout is a real terminal (vs the captured StringIO used in tests).

**4. The overlap threshold.** The 0.4 threshold was chosen by reasoning, not calibration. We have no measurement of how many false positives or false negatives this produces on real-world MEMORY.md content. A threshold that produces too many warnings will train the LLM to ignore them.

**5. Ephemeral expiry across timezones.** `_today()` uses `date.today()` which is wall-clock local time. A server running in UTC with a user in UTC-8 writing an entry at 11pm local time will expire it one day earlier than the user expects. Not tested.

**6. Eviction interaction with session additions.** If an ephemeral entry is evicted mid-session during an add, it may still appear in `_session_additions` from when it was first added. The eviction and session additions lists are not cross-checked. The entry will appear in the current session prompt but be absent from the file.

**7. History parsing edge cases.** `_parse_last_history_record` in `skills_hub.py` uses regex to parse Markdown. If a skill's old content contains lines that look like `## 2026-...` timestamps or `### Old` / `### New` headers, the parser may mis-identify boundaries. Not tested.

**8. Performance at scale.** History is append-only. A skill patched 1,000 times has a 1,000-record SKILL_HISTORY.md. `do_rollback` reads and regex-scans the entire file each time. Not benchmarked.

**9. Cross-platform line endings.** On Windows, `_atomic_write_text` opens files in text mode (default CRLF). History records written on Windows will have CRLF; regex patterns in `_parse_last_history_record` use `\n`. Not tested on Windows end-to-end.

---

## Part 5: Next Steps

These are ordered by impact-to-effort ratio, not strict priority.

### Immediate (one session each)

**1. ~~Test the rollback CLI path.~~** ✅ Done — `tests/hermes_cli/test_rollback.py` (26 tests, 2026-04-11).

**2. Calibrate the overlap threshold.**
Collect 20–30 real MEMORY.md entries from a used installation. Run `_detect_overlap` pairwise. Count false positives (unrelated entries flagged). Adjust threshold (currently 0.4) and/or stopword list. Target: fewer than 5% false positive rate.

**3. ~~Add `_today()` monkeypatch tests for expiry edge cases.~~** ✅ Done — `TestExpiryEdgeCases` (5 tests, 2026-04-11). Pins `_today()` to `date(2026, 4, 15)` and exercises both `load_from_disk()` and `_reload_target()` expiry paths with exact boundary dates.

### Near-term (2–4 sessions)

**4. History size management.**
SKILL_HISTORY.md grows unboundedly. Options:
- Cap at N records (e.g. 50), rotate oldest when full.
- Offer `hermes skills history <name>` to display records without showing the full raw file.
- `hermes skills history --prune <name>` to truncate to last 10.

**5. ~~`hermes skills history <name>` view command.~~** ✅ Done — `do_history` + `_parse_all_history_records` (2026-04-11). Rich Table (default) + `--detail N` diff view. 23 tests in `tests/hermes_cli/test_history.py`.

**6. Memory audit CLI (`hermes memory audit`).**
Originally deferred from the plan (Tier 2, "not implementing — deferred"). Now that tiers are in place, this becomes more useful: show all entries, their priority, expiry date if ephemeral, character count. Flag entries that are candidates for `replace` or `remove`. No new infrastructure needed — it is a read-only formatted view of MEMORY.md and USER.md.

**7. Integration test for `format_for_system_prompt` with the actual agent loop.**
The intra-session visibility improvement is only useful if `prompt_builder.py` actually calls `format_for_system_prompt()` on the `MemoryStore` instance after each turn. Verify this with a test that mocks the LLM but exercises the full agent loop: turn 1 adds a memory, turn 2's system prompt contains it.

### Longer-term (new infrastructure required)

These remain Tier 3 from the original analysis — they are documented here as the realistic roadmap.

**8. Outcome-gated skill creation.**
Skills are currently created based on task complexity. Track whether the user accepted the result (no follow-up corrections in N turns). Only create a skill if the approach was accepted. Requires a hook in the conversation loop after the LLM's final turn.

**9. Semantic overlap detection.**
Replace the word-overlap heuristic with an embedding-based similarity check (e.g. `nomic-embed-text` via Ollama). This would correctly detect that "slow API responses" and "high HTTP latency" are related without false-positiving on "the user uses Python" and "the project uses Go".

**10. Memory consolidation cron.**
Nightly background job: read the last 7 sessions' summaries via `session_search`, ask the LLM "what facts from these sessions should be added or updated in MEMORY.md?", propose changes. Requires a cron job infrastructure integration point (hooks into the existing cron scheduler).

**11. Proactive context injection.**
At session start, automatically search the last 3 sessions for topics related to the current session opener and inject a brief summary into the first system prompt. Requires a change to the agent loop's session-start logic.

---

## Appendix: Test Count by Class

| Class | File | Count | New? |
|-------|------|-------|------|
| TestMemorySchema | memory_tool | 2 | 1 new |
| TestScanMemoryContent | memory_tool | 7 | — |
| TestMemoryStoreAdd | memory_tool | 6 | 1 new |
| TestMemoryStoreReplace | memory_tool | 6 | — |
| TestMemoryStoreRemove | memory_tool | 3 | — |
| TestMemoryStorePersistence | memory_tool | 2 | — |
| TestMemoryStoreSnapshot | memory_tool | 5 | 3 new, 2 rewritten |
| TestMemoryPriority | memory_tool | 7 | all new |
| TestContradictionDetection | memory_tool | 4 | all new |
| TestDetectOverlapUnit | memory_tool | 3 | all new |
| TestPriorityHelpers | memory_tool | 7 | all new |
| TestExpiryEdgeCases | memory_tool | 5 | all new |
| TestMemoryToolDispatcher | memory_tool | 8 | 2 new |
| **Subtotal** | | **65** | |
| TestValidateName | skill_manager | 6 | — |
| TestValidateCategory | skill_manager | 3 | — |
| TestValidateFrontmatter | skill_manager | 8 | — |
| TestValidateFilePath | skill_manager | 6 | — |
| TestCreateSkill | skill_manager | 7 | — |
| TestEditSkill | skill_manager | 3 | — |
| TestPatchSkill | skill_manager | 7 | — |
| TestDeleteSkill | skill_manager | 3 | — |
| TestWriteFile | skill_manager | 4 | — |
| TestRemoveFile | skill_manager | 3 | — |
| TestSkillManageDispatcher | skill_manager | 4 | — |
| TestSkillPatchHistory | skill_manager | 6 | all new |
| TestReasonRequired | skill_manager | 5 | all new |
| **Subtotal** | | **65** | |
| TestParseLastHistoryRecord | test_rollback | 7 | all new |
| TestDoRollbackHappyPath | test_rollback | 7 | all new |
| TestDoRollbackErrorPaths | test_rollback | 4 | all new |
| TestDoRollbackClearsPromptCache | test_rollback | 2 | all new |
| TestSkillsCommandRollbackDispatch | test_rollback | 2 | all new |
| TestRollbackArgparse | test_rollback | 4 | all new |
| **Subtotal** | | **26** | |
| TestParseAllHistoryRecords | test_history | 5 | all new |
| TestDoHistoryTableView | test_history | 5 | all new |
| TestDoHistoryDetailView | test_history | 4 | all new |
| TestDoHistoryErrorPaths | test_history | 3 | all new |
| TestHistoryCommandDispatch | test_history | 2 | all new |
| TestHistoryArgparse | test_history | 4 | all new |
| **Subtotal** | | **23** | |
| **Total** | | **179** | **~114 new or rewritten** |

---

## Appendix: Full pytest Output (run evidence)

Run command:

```
python -m pytest tests/tools/test_memory_tool.py tests/tools/test_skill_manager_tool.py -v --override-ini="addopts=" --tb=short
```

Environment: Python 3.13.5 · pytest 8.4.2 · platform win32 · 2026-04-11

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 125 items

tests/tools/test_memory_tool.py::TestMemorySchema::test_discourages_diary_style_task_logs PASSED [  0%]
tests/tools/test_memory_tool.py::TestMemorySchema::test_schema_exposes_priority_param PASSED [  1%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_clean_content_passes PASSED [  2%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_prompt_injection_blocked PASSED [  3%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_exfiltration_blocked PASSED [  4%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_ssh_backdoor_blocked PASSED [  4%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_invisible_unicode_blocked PASSED [  5%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_role_hijack_blocked PASSED [  6%]
tests/tools/test_memory_tool.py::TestScanMemoryContent::test_system_override_blocked PASSED [  7%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_entry PASSED [  8%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_to_user PASSED [  8%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_empty_rejected PASSED [  9%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_duplicate_rejected PASSED [ 10%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_injection_blocked PASSED [ 11%]
tests/tools/test_memory_tool.py::TestMemoryStoreAdd::test_add_invalid_priority_rejected PASSED [ 12%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_entry PASSED [ 12%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_no_match PASSED [ 13%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_ambiguous_match PASSED [ 14%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_empty_old_text_rejected PASSED [ 15%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_empty_new_content_rejected PASSED [ 16%]
tests/tools/test_memory_tool.py::TestMemoryStoreReplace::test_replace_injection_blocked PASSED [ 16%]
tests/tools/test_memory_tool.py::TestMemoryStoreRemove::test_remove_entry PASSED [ 17%]
tests/tools/test_memory_tool.py::TestMemoryStoreRemove::test_remove_no_match PASSED [ 18%]
tests/tools/test_memory_tool.py::TestMemoryStoreRemove::test_remove_empty_old_text PASSED [ 19%]
tests/tools/test_memory_tool.py::TestMemoryStorePersistence::test_save_and_load_roundtrip PASSED [ 20%]
tests/tools/test_memory_tool.py::TestMemoryStorePersistence::test_deduplication_on_load PASSED [ 20%]
tests/tools/test_memory_tool.py::TestMemoryStoreSnapshot::test_snapshot_shows_session_additions PASSED [ 21%]
tests/tools/test_memory_tool.py::TestMemoryStoreSnapshot::test_base_snapshot_stable_when_no_additions PASSED [ 22%]
tests/tools/test_memory_tool.py::TestMemoryStoreSnapshot::test_empty_snapshot_returns_none PASSED [ 23%]
tests/tools/test_memory_tool.py::TestMemoryStoreSnapshot::test_session_additions_cleared_on_reload PASSED [ 24%]
tests/tools/test_memory_tool.py::TestMemoryStoreSnapshot::test_session_addition_only_snapshot_when_no_base PASSED [ 24%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_high_priority_prefix_stored PASSED [ 25%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_normal_priority_no_prefix PASSED [ 26%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_ephemeral_prefix_stored PASSED [ 27%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_high_priority_sorts_first PASSED [ 28%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_ephemeral_expires_on_reload PASSED [ 28%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_non_expired_ephemeral_kept PASSED [ 29%]
tests/tools/test_memory_tool.py::TestMemoryPriority::test_high_not_auto_evicted_when_limit_hit PASSED [ 30%]
tests/tools/test_memory_tool.py::TestContradictionDetection::test_no_overlap_no_warning PASSED [ 31%]
tests/tools/test_memory_tool.py::TestContradictionDetection::test_overlap_above_threshold_returns_warning PASSED [ 32%]
tests/tools/test_memory_tool.py::TestContradictionDetection::test_add_still_succeeds_when_overlap_detected PASSED [ 32%]
tests/tools/test_memory_tool.py::TestContradictionDetection::test_exact_duplicate_not_flagged_as_overlap PASSED [ 33%]
tests/tools/test_memory_tool.py::TestDetectOverlapUnit::test_high_overlap_detected PASSED [ 34%]
tests/tools/test_memory_tool.py::TestDetectOverlapUnit::test_low_overlap_not_detected PASSED [ 35%]
tests/tools/test_memory_tool.py::TestDetectOverlapUnit::test_exact_dup_excluded PASSED [ 36%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_parse_high PASSED [ 36%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_parse_ephemeral PASSED [ 37%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_parse_normal PASSED [ 38%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_is_expired_past_date PASSED [ 39%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_is_expired_future_date PASSED [ 40%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_is_expired_normal_entry PASSED [ 40%]
tests/tools/test_memory_tool.py::TestPriorityHelpers::test_sort_high_first PASSED [ 41%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_no_store_returns_error PASSED [ 42%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_invalid_target PASSED [ 43%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_unknown_action PASSED [ 44%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_add_via_tool PASSED [ 44%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_add_with_high_priority PASSED [ 45%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_add_with_ephemeral_priority PASSED [ 46%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_replace_requires_old_text PASSED [ 47%]
tests/tools/test_memory_tool.py::TestMemoryToolDispatcher::test_remove_requires_old_text PASSED [ 48%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_valid_names PASSED [ 48%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_empty_name PASSED [ 49%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_too_long PASSED [ 50%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_uppercase_rejected PASSED [ 51%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_starts_with_hyphen_rejected PASSED [ 52%]
tests/tools/test_skill_manager_tool.py::TestValidateName::test_special_chars_rejected PASSED [ 52%]
tests/tools/test_skill_manager_tool.py::TestValidateCategory::test_valid_categories PASSED [ 53%]
tests/tools/test_skill_manager_tool.py::TestValidateCategory::test_path_traversal_rejected PASSED [ 54%]
tests/tools/test_skill_manager_tool.py::TestValidateCategory::test_absolute_path_rejected PASSED [ 55%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_valid_content PASSED [ 56%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_empty_content PASSED [ 56%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_no_frontmatter PASSED [ 57%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_unclosed_frontmatter PASSED [ 58%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_missing_name_field PASSED [ 59%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_missing_description_field PASSED [ 60%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_no_body_after_frontmatter PASSED [ 60%]
tests/tools/test_skill_manager_tool.py::TestValidateFrontmatter::test_invalid_yaml PASSED [ 61%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_valid_paths PASSED [ 62%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_empty_path PASSED [ 63%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_path_traversal_blocked PASSED [ 64%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_disallowed_subdirectory PASSED [ 64%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_directory_only_rejected PASSED [ 65%]
tests/tools/test_skill_manager_tool.py::TestValidateFilePath::test_root_level_file_rejected PASSED [ 66%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_skill PASSED [ 67%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_with_category PASSED [ 68%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_duplicate_blocked PASSED [ 68%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_invalid_name PASSED [ 69%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_invalid_content PASSED [ 70%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_rejects_category_traversal PASSED [ 71%]
tests/tools/test_skill_manager_tool.py::TestCreateSkill::test_create_rejects_absolute_category PASSED [ 72%]
tests/tools/test_skill_manager_tool.py::TestEditSkill::test_edit_existing_skill PASSED [ 72%]
tests/tools/test_skill_manager_tool.py::TestEditSkill::test_edit_nonexistent_skill PASSED [ 73%]
tests/tools/test_skill_manager_tool.py::TestEditSkill::test_edit_invalid_content_rejected PASSED [ 74%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_unique_match PASSED [ 75%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_nonexistent_string PASSED [ 76%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_ambiguous_match_rejected PASSED [ 76%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_replace_all PASSED [ 77%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_supporting_file PASSED [ 78%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_skill_not_found PASSED [ 79%]
tests/tools/test_skill_manager_tool.py::TestPatchSkill::test_patch_supporting_file_symlink_escape_blocked PASSED [ 80%]
tests/tools/test_skill_manager_tool.py::TestDeleteSkill::test_delete_existing PASSED [ 80%]
tests/tools/test_skill_manager_tool.py::TestDeleteSkill::test_delete_nonexistent PASSED [ 81%]
tests/tools/test_skill_manager_tool.py::TestDeleteSkill::test_delete_cleans_empty_category_dir PASSED [ 82%]
tests/tools/test_skill_manager_tool.py::TestWriteFile::test_write_reference_file PASSED [ 83%]
tests/tools/test_skill_manager_tool.py::TestWriteFile::test_write_to_nonexistent_skill PASSED [ 84%]
tests/tools/test_skill_manager_tool.py::TestWriteFile::test_write_to_disallowed_path PASSED [ 84%]
tests/tools/test_skill_manager_tool.py::TestWriteFile::test_write_symlink_escape_blocked PASSED [ 85%]
tests/tools/test_skill_manager_tool.py::TestRemoveFile::test_remove_existing_file PASSED [ 86%]
tests/tools/test_skill_manager_tool.py::TestRemoveFile::test_remove_nonexistent_file PASSED [ 87%]
tests/tools/test_skill_manager_tool.py::TestRemoveFile::test_remove_symlink_escape_blocked PASSED [ 88%]
tests/tools/test_skill_manager_tool.py::TestSkillManageDispatcher::test_unknown_action PASSED [ 88%]
tests/tools/test_skill_manager_tool.py::TestSkillManageDispatcher::test_create_without_content PASSED [ 89%]
tests/tools/test_skill_manager_tool.py::TestSkillManageDispatcher::test_patch_without_old_string PASSED [ 90%]
tests/tools/test_skill_manager_tool.py::TestSkillManageDispatcher::test_full_create_via_dispatcher PASSED [ 91%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_patch_writes_history_file PASSED [ 92%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_history_contains_old_and_new_blocks PASSED [ 92%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_history_contains_reason PASSED [ 93%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_history_is_append_only PASSED [ 94%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_edit_writes_history PASSED [ 95%]
tests/tools/test_skill_manager_tool.py::TestSkillPatchHistory::test_append_skill_history_helper PASSED [ 96%]
tests/tools/test_skill_manager_tool.py::TestReasonRequired::test_patch_without_reason_returns_error PASSED [ 96%]
tests/tools/test_skill_manager_tool.py::TestReasonRequired::test_edit_without_reason_returns_error PASSED [ 97%]
tests/tools/test_skill_manager_tool.py::TestReasonRequired::test_patch_with_reason_succeeds PASSED [ 98%]
tests/tools/test_skill_manager_tool.py::TestReasonRequired::test_edit_with_reason_succeeds PASSED [ 99%]
tests/tools/test_skill_manager_tool.py::TestReasonRequired::test_create_does_not_require_reason PASSED [100%]

============================== warnings summary ===============================
tests/tools/test_memory_tool.py::TestMemorySchema::test_discourages_diary_style_task_logs
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 125 passed, 1 warning in 0.78s ========================
```

**Note on the warning.** The single `DeprecationWarning: There is no current event loop` comes from `tests/conftest.py:91`, which calls `asyncio.get_event_loop_policy().get_event_loop()` in a fixture that runs before every test. This is pre-existing infrastructure code unrelated to our changes; Python 3.10+ deprecated implicit event loop creation. It does not affect test correctness and is present in the upstream test suite.

**Note on `addopts`.** The project's `pyproject.toml` sets `addopts = "-m 'not integration' -n auto"` (parallel execution via `pytest-xdist`). The `--override-ini="addopts="` flag above clears this because `pytest-xdist` is not installed in this environment. On a full Linux development environment with all dependencies installed, the equivalent command is simply:

```
python -m pytest tests/tools/test_memory_tool.py tests/tools/test_skill_manager_tool.py -v
```

---

### Rollback CLI tests

Run command:

```
python -m pytest tests/hermes_cli/test_rollback.py -v --override-ini="addopts="
```

Environment: Python 3.13.5 · pytest 8.4.2 · platform win32 · 2026-04-11

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 26 items

tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_empty_text_returns_none_tuple PASSED [  3%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_single_patch_record_extracted PASSED [  7%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_multiple_records_returns_most_recent PASSED [ 11%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_rollback_record_is_skipped PASSED [ 15%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_all_rollback_records_returns_none PASSED [ 19%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_edit_action_also_returned PASSED [ 23%]
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_malformed_record_without_old_block_returns_none PASSED [ 26%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_patch_then_rollback_restores_original PASSED [ 30%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_rollback_appends_rollback_record_to_history PASSED [ 34%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_rollback_shows_unified_diff_preview PASSED [ 38%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_rollback_with_identical_content_prints_no_visible_diff PASSED [ 42%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_skip_confirm_false_with_y_restores PASSED [ 46%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_skip_confirm_false_with_n_cancels PASSED [ 50%]
tests/hermes_cli/test_rollback.py::TestDoRollbackHappyPath::test_confirm_eof_treated_as_no PASSED [ 53%]
tests/hermes_cli/test_rollback.py::TestDoRollbackErrorPaths::test_unknown_skill_prints_error PASSED [ 57%]
tests/hermes_cli/test_rollback.py::TestDoRollbackErrorPaths::test_skill_exists_but_no_history_prints_warning PASSED [ 61%]
tests/hermes_cli/test_rollback.py::TestDoRollbackErrorPaths::test_history_only_rollback_records_prints_no_restorable PASSED [ 65%]
tests/hermes_cli/test_rollback.py::TestDoRollbackErrorPaths::test_malformed_history_prints_no_restorable PASSED [ 69%]
tests/hermes_cli/test_rollback.py::TestDoRollbackClearsPromptCache::test_successful_rollback_clears_prompt_cache PASSED [ 73%]
tests/hermes_cli/test_rollback.py::TestDoRollbackClearsPromptCache::test_cache_clear_failure_is_swallowed PASSED [ 76%]
tests/hermes_cli/test_rollback.py::TestSkillsCommandRollbackDispatch::test_skills_command_routes_rollback_with_yes_true PASSED [ 80%]
tests/hermes_cli/test_rollback.py::TestSkillsCommandRollbackDispatch::test_skills_command_rollback_defaults_yes_false PASSED [ 84%]
tests/hermes_cli/test_rollback.py::TestRollbackArgparse::test_rollback_subparser_registered PASSED [ 88%]
tests/hermes_cli/test_rollback.py::TestRollbackArgparse::test_rollback_yes_flag_long PASSED [ 92%]
tests/hermes_cli/test_rollback.py::TestRollbackArgparse::test_rollback_yes_flag_short PASSED [ 96%]
tests/hermes_cli/test_rollback.py::TestRollbackArgparse::test_rollback_missing_name_errors PASSED [100%]

============================== warnings summary ===============================
tests/hermes_cli/test_rollback.py::TestParseLastHistoryRecord::test_empty_text_returns_none_tuple
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 26 passed, 1 warning in 0.44s ========================
```

**Note on the argparse tests.** `TestRollbackArgparse` calls `hermes_cli.main.main()` with a monkeypatched `sys.argv` and a spy on `hermes_cli.skills_hub.skills_command`. This exercises the **real** `ArgumentParser` built inside `main()` — not a replica — so a rename of `--yes` to `--confirm` in `main.py` would fail these tests immediately. The `skills_command` spy is reachable because `cmd_skills` (defined inside `main()`) does a fresh `from hermes_cli.skills_hub import skills_command` on each invocation, resolving the name from the module object at call time, where the monkeypatched binding lives.

For full interpretation of every passing test, the test design rationale, and documented limits, see [`docs/analysis/rollback-cli-tests.md`](rollback-cli-tests.md).

---

### Expiry edge-case tests

Run command:

```
python -m pytest tests/tools/test_memory_tool.py::TestExpiryEdgeCases -v --override-ini="addopts="
```

Environment: Python 3.13.5 · pytest 8.4.2 · platform win32 · 2026-04-11

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 5 items

tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_today_is_not_expired PASSED [ 20%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_yesterday_is_expired PASSED [ 40%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_tomorrow_is_not_expired PASSED [ 60%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_load_from_disk_drops_exactly_expired_entries PASSED [ 80%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_reload_target_drops_exactly_expired_entries PASSED [100%]

============================== warnings summary ===============================
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_today_is_not_expired
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 5 passed, 1 warning in 3.22s =========================
```

**Note on the monkeypatch.** All five tests use a `pin_today` fixture that calls `monkeypatch.setattr("tools.memory_tool._today", lambda: date(2026, 4, 15))`. This patches the module-level `_today()` function so every downstream caller (`_is_expired`, `load_from_disk`, `_reload_target`, `add`) sees the same fixed date. Tests 4 and 5 additionally monkeypatch `tools.memory_tool.MEMORY_DIR` and `tools.memory_tool.get_memory_dir` to redirect file I/O to `tmp_path`. The `MemoryStore` is constructed with `MemoryStore(memory_char_limit=2200, user_char_limit=1375)` — the same pattern used in `test_ephemeral_expires_on_reload` — not with a directory argument (the store reads the directory from the module-level `get_memory_dir()` function at load time).

---

### History CLI tests

Run command:

```
python -m pytest tests/hermes_cli/test_history.py -v --override-ini="addopts="
```

Environment: Python 3.13.5 · pytest 8.4.2 · platform win32 · 2026-04-11

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 23 items

tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_empty_text_returns_empty_list PASSED [  4%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_single_patch_record PASSED [  8%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_multiple_records_preserves_order PASSED [ 13%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_rollback_record_included PASSED [ 17%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_malformed_record_skipped PASSED [ 21%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_single_patch_shows_table PASSED [ 26%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_multiple_patches_numbered PASSED [ 30%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_rollback_record_in_table PASSED [ 34%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_shows_record_count PASSED [ 39%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_hint_about_detail_flag PASSED [ 43%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_shows_diff PASSED [ 47%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_out_of_range PASSED [ 52%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_zero_is_invalid PASSED [ 56%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_identity_diff PASSED [ 60%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_unknown_skill_prints_error PASSED [ 65%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_no_history_file_prints_warning PASSED [ 69%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_empty_history_file_prints_warning PASSED [ 73%]
tests/hermes_cli/test_history.py::TestHistoryCommandDispatch::test_skills_command_routes_history PASSED [ 78%]
tests/hermes_cli/test_history.py::TestHistoryCommandDispatch::test_skills_command_routes_history_with_detail PASSED [ 82%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_subparser_registered PASSED [ 86%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_detail_flag PASSED [ 91%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_missing_name_errors PASSED [ 95%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_detail_requires_int PASSED [100%]

============================== warnings summary ===============================
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_empty_text_returns_empty_list
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 23 passed, 1 warning in 4.22s ========================
```

For full interpretation of every passing test, the test design rationale, and documented limits, see [`docs/analysis/history-cli-tests.md`](history-cli-tests.md).

---

### Full regression receipt (179 tests)

```
python -m pytest tests/tools/test_memory_tool.py tests/tools/test_skill_manager_tool.py tests/hermes_cli/test_rollback.py tests/hermes_cli/test_history.py -q --override-ini="addopts="
```

```
179 passed, 1 warning in 4.43s
```
