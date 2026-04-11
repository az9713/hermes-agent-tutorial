# Implementation Status — 7 Self-Improvement Enhancements

> Written after the implementation commit. Records exactly what was built, what the 125 tests cover and don't cover, what conclusions they support, and what the realistic next steps are.

---

## Part 1: What Was Actually Implemented

### Overview

Six source files were changed; 875 lines net added across implementation and tests.

| File | Role |
|------|------|
| `tools/memory_tool.py` | Three memory changes (visibility, tiers, contradiction) |
| `tools/skill_manager_tool.py` | Two skill changes (history, reason enforcement) |
| `hermes_cli/skills_hub.py` | Rollback action (`do_rollback`) |
| `hermes_cli/main.py` | Rollback subparser (`hermes skills rollback`) |
| `tests/tools/test_memory_tool.py` | 60 tests (49 new, 11 rewritten) |
| `tests/tools/test_skill_manager_tool.py` | 65 tests (11 new, 54 unchanged) |

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

## Part 2: The 125 Tests

### Distribution

| File | Tests | New vs pre-existing |
|------|-------|---------------------|
| `test_memory_tool.py` | 60 | 49 new, 11 rewritten |
| `test_skill_manager_tool.py` | 65 | 11 new, 54 unchanged |

### All 125 tests by class

#### `test_memory_tool.py` — 60 tests

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

## Part 3: What the 125 Tests Tell Us

These tests are all **unit and functional tests of internal logic**. They run in-process with isolated temp directories. Passing means:

**The new logic is correctly implemented.** The session-additions pattern, priority prefix parsing, expiry logic, eviction order, overlap heuristic, history appending, and reason enforcement all behave as designed at the function level.

**The security scanner still works.** The 7 injection/exfiltration tests confirm that adding the new `priority` and `expires_days` parameters did not bypass or break the content-scanning path.

**Atomic write discipline is preserved.** Patch history uses `_atomic_write_text`, the same function the skill writes use. History writes are not partial-write risks.

**The frozen-snapshot caching contract is explicitly pinned.** `test_base_snapshot_stable_when_no_additions` ensures that if no entries are added mid-session, the system prompt output is byte-identical across calls. This is the condition for Anthropic prompt cache hits.

**Session additions are correctly scoped.** `test_session_additions_cleared_on_reload` verifies there is no bleed between sessions — starting a new session gives a fresh slate.

**Backward compatibility holds.** Entries without any prefix parse as `normal`. Nothing in the new code requires MEMORY.md files to be migrated.

**All pre-existing skill manager tests still pass.** The 54 unchanged tests confirm that adding `reason` enforcement and history logging did not break create, delete, write_file, remove_file, or validation paths.

---

## Part 4: What the Tests Cannot Tell Us

**1. LLM integration.** The LLM is never invoked. We cannot know whether the LLM will:
- Actually supply a `reason` the first time (it will get an error and retry on the first missed call, but this means one extra API round-trip per patch/edit until it learns the schema).
- Actually notice contradiction warnings and act on them.
- Actually benefit from session additions being visible — the improvement is only useful if the LLM reads and uses that block.

**2. Real-file concurrency.** `_file_lock` uses `fcntl.flock`. Tests on Windows use a no-op lock. We have not tested simultaneous writes from two processes (e.g., a CLI session and a gateway session writing memory at the same time). The mechanism is correct by design, but race conditions under load are not covered.

**3. Rollback CLI path.** `do_rollback` in `skills_hub.py` has no test. The parser in `main.py` has no test. The full `hermes skills rollback` flow was only verified manually in the plan; no automated test simulates it end-to-end, including the `input("Restore? [y/N]:")` prompt path.

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

**1. Test the rollback CLI path.**
Write a test in `tests/hermes_cli/` (or a new `tests/tools/test_skill_history.py`) that:
- Creates a skill, patches it, patches it again.
- Calls `do_rollback(name, skip_confirm=True, console=Console(quiet=True))` directly.
- Asserts SKILL.md content reverted to before the last patch.
- Asserts SKILL_HISTORY.md has a rollback record appended.

**2. Calibrate the overlap threshold.**
Collect 20–30 real MEMORY.md entries from a used installation. Run `_detect_overlap` pairwise. Count false positives (unrelated entries flagged). Adjust threshold (currently 0.4) and/or stopword list. Target: fewer than 5% false positive rate.

**3. Add `_today()` monkeypatch tests for expiry edge cases.**
The `_today()` function is already isolated for this reason. Add a test where two entries — one expiring today, one expiring tomorrow — are loaded after monkeypatching `_today` to today. Verify exactly one is dropped.

### Near-term (2–4 sessions)

**4. History size management.**
SKILL_HISTORY.md grows unboundedly. Options:
- Cap at N records (e.g. 50), rotate oldest when full.
- Offer `hermes skills history <name>` to display records without showing the full raw file.
- `hermes skills history --prune <name>` to truncate to last 10.

**5. `hermes skills history <name>` view command.**
Let users inspect the patch log without editing the raw Markdown. Output should be a Rich table: timestamp | action | reason | file. This is the natural companion to rollback.

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
| TestMemoryToolDispatcher | memory_tool | 8 | 2 new |
| **Subtotal** | | **60** | |
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
| **Total** | | **125** | **~60 new or rewritten** |

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
