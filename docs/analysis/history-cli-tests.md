# History CLI Tests — Evidence, Interpretation, and Limits

> This document is the complete reference for `tests/hermes_cli/test_history.py`.
> It answers four questions: *why these tests exist*, *what passed*, *what that proves*,
> and *what it does not prove*.

---

## 1. Why This Test File Exists

### The feature it covers

`hermes skills history <name>` is the read-only companion to `hermes skills rollback`.
`SKILL_HISTORY.md` is an append-only log that records every patch, edit, and rollback
applied to a skill. Before this command existed, the only way to inspect the log was to
open the raw Markdown file — there was no structured view and no way to see a single
record's diff without reading the entire file.

The command was step #5 in the roadmap in `docs/analysis/implementation-status.md` Part 5:

> *"`hermes skills history <name>` view command. Let users inspect the patch log without
> editing the raw Markdown. Output should be a Rich table: timestamp | action | reason |
> file. This is the natural companion to rollback."*

### What was built

Two new production functions in `hermes_cli/skills_hub.py`:

1. **`_parse_all_history_records(history_text)`** — parses ALL records from
   `SKILL_HISTORY.md` and returns them as a list of dicts (oldest first). Unlike the
   existing `_parse_last_history_record`, it includes rollback records and does not
   reverse-scan for the most recent restorable state.

2. **`do_history(name, detail=None, console=None)`** — the command implementation.
   Default output: a Rich Table with columns `#`, `Timestamp`, `Action`, `Reason`,
   `File`. With `--detail N`: full diff for record #N (1-indexed, oldest first), using
   the same coloured `difflib.unified_diff` rendering as `do_rollback`.

Plus argparse registration (`hermes skills history <name> [--detail N]`) in
`hermes_cli/main.py` and routing in `skills_command()`.

### Relationship to `test_rollback.py`

`test_history.py` uses the **identical infrastructure** as `test_rollback.py`:
- `_skill_dir(tmp_path)` from `tests/tools/test_skill_manager_tool.py:32`
- `_create_skill`, `_patch_skill`, `_append_skill_history` production functions
- `_capture_console()` helper (Rich Console writing to a `StringIO` sink)
- `_run_skills_cmd` pattern (real `main()` + patched `sys.argv` + `skills_command` spy)
- `Namespace` construction for dispatch tests

The test architecture is a parallel structure to `test_rollback.py`: 6 classes, same
layer-by-layer coverage, same separation of concerns.

---

## 2. Test Receipt — Verbatim Output

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
======================== 23 passed, 1 warning in 4.22s =========================
```

**Note on the warning.** The `DeprecationWarning` comes from `tests/conftest.py:91`, a
shared fixture that fires on the first test of any run. Pre-existing, unrelated to this
test module. No test in `test_history.py` uses asyncio.

**Note on `--override-ini="addopts="`.**  `pyproject.toml` sets
`addopts = "-m 'not integration' -n auto"`. Cleared here because `pytest-xdist` is not
installed in this Windows environment. On a full Linux dev environment: simply
`python -m pytest tests/hermes_cli/test_history.py -v`.

**Summary: 23 passed, 0 failed, 0 errors, 1 pre-existing warning. Run time: 4.22 s.**

---

## 3. Test Strategy and Architecture

### The two layers under test

`hermes skills history` spans the same four-layer stack as `hermes skills rollback`. The
test classes map directly onto layers, exactly as in `test_rollback.py`.

```
User runs: hermes skills history my-skill --detail 2
                        │
             Layer 1: argparse (main.py)
             Parses CLI input → Namespace(skills_action="history", name=..., detail=...)
                        │
             Layer 2: skills_command routing (skills_hub.py)
             elif action == "history": do_history(args.name, detail=getattr(...))
                        │
             Layer 3: do_history() (skills_hub.py)
             Find skill → read file → parse records → render table or diff
                        │
             Layer 4: _parse_all_history_records() (skills_hub.py)
             Regex parser: splits on headers, extracts all records oldest-first
```

Testing each layer independently means a failure localises precisely: if
`test_history_subparser_registered` fails but `test_skills_command_routes_history` passes,
the problem is in argparse, not in routing.

### `_parse_all_history_records` vs `_parse_last_history_record`

These two functions serve different purposes and must not be confused:

| | `_parse_last_history_record` | `_parse_all_history_records` |
|--|--|--|
| **Used by** | `do_rollback` | `do_history` |
| **Returns** | `(file_path, old_text, new_text)` for the most recent restorable record | `list[dict]` of all records |
| **Order** | Reverse-scans (newest first) | Chronological (oldest first) |
| **Rollback records** | Skipped — rollback markers are not restorable targets | Included — the full audit trail |
| **On empty/malformed** | Returns `(None, None, None)` | Returns `[]` |

The critical difference is rollback inclusion. If `_parse_all_history_records` also
skipped rollback records, a user who ran rollback would see a hole in their history table.
`test_rollback_record_included` in Class 1 pins this contract.

### `_capture_console()` infrastructure

Both `test_rollback.py` and `test_history.py` define an identical helper:

```python
def _capture_console():
    sink = StringIO()
    return sink, Console(file=sink, force_terminal=False, color_system=None)
```

`force_terminal=False` prevents Rich from emitting ANSI escape sequences. `color_system=None`
ensures no colour markup leaks into the captured string. Together they make assertions like
`assert "patch" in output` work on plain text without stripping escape codes.

The function is duplicated (not imported) because it is 3 lines and importing it from
`test_rollback.py` would create a test-to-test dependency with no benefits.

### Real-file test infrastructure

All tests in Classes 2, 3, and 4 use `_skill_dir(tmp_path)` to redirect both
`SKILLS_DIR` and `get_all_skills_dirs` to a temporary directory, then call the production
functions `_create_skill` and `_patch_skill` to write real files. This is stronger than
mocking because:
- `_patch_skill` calls `_append_skill_history` — real history records are written in the
  exact format `_parse_all_history_records` parses.
- Any path-construction bug or encoding issue in the production code would fail a test.
- The history files are byte-for-byte identical to what a real user's `SKILL_HISTORY.md`
  would look like.

For `test_detail_identity_diff` (where `old_text == new_text`), `_append_skill_history`
is called directly to write a hand-crafted record — `_patch_skill` enforces that
`old_text != new_text`, so the only way to produce an identity record in a test is to
call the history writer directly.

---

## 4. Class-by-Class Interpretation

### Class 1 — `TestParseAllHistoryRecords` (5 tests, all PASSED)

**What this class tests.** `_parse_all_history_records` is a pure function — it takes a
string and returns a list of dicts with no side effects. It is the lowest layer and the
easiest to test thoroughly with inline string fixtures.

**What the passing tests prove.**

`test_empty_text_returns_empty_list` PASSED
→ The function never crashes on empty input and returns `[]`. `do_history` can safely
receive an empty list and print its "No parseable records" message.

`test_single_patch_record` PASSED
→ All five fields are correctly parsed: `timestamp`, `action`, `reason`, `file_path`,
`old_text`, `new_text`. The em-dash in `## 2026-04-11T14:32:05Z — patch` (U+2014) is
correctly matched by `r'## (\S+) — (\w+)'`. The `**Reason:**` and `**File:**` lines are
extracted. The fenced `### Old` and `### New` blocks with `\`\`\`text` markers are parsed
by the `re.DOTALL` regexes. This confirms the writer (`_append_skill_history`) and the
reader (`_parse_all_history_records`) use matching formats.

`test_multiple_records_preserves_order` PASSED
→ Two records in the order patch → rollback appear in the returned list as `list[0]=patch,
list[1]=rollback`. The function processes sections in forward order (not reversed), so the
oldest record is first. This is the correct order for the `#` column in the history table:
record #1 is the first change ever made, record #N is the most recent.

`test_rollback_record_included` PASSED
→ A rollback record is returned as a normal dict with `action="rollback"`. This is the
key design contrast with `_parse_last_history_record`, which explicitly skips rollback
records. The history view must show the complete audit trail — hiding rollbacks would
mislead users about the sequence of changes.

`test_malformed_record_skipped` PASSED
→ A text block that does not start with `## YYYY-MM-DDTHH:MM:SSZ — action` fails the
`re.match(r'## (\S+) — (\w+)', section)` check and is silently skipped. Adjacent valid
records are unaffected. This is defensive parsing: a manually-edited or partially-written
`SKILL_HISTORY.md` does not crash the command or hide valid records.

---

### Class 2 — `TestDoHistoryTableView` (5 tests, all PASSED)

**What this class tests.** The default output path of `do_history` — the Rich Table that
appears when `--detail` is not specified.

**What the passing tests prove.**

`test_single_patch_shows_table` PASSED
→ After `_create_skill` + `_patch_skill(reason="test reason")`, the captured console
output contains:
- The skill name in the table title (`"my-skill"`)
- The action string (`"patch"`)
- The reason text (`"test reason"`)
- The file name (`"SKILL.md"`)

This confirms that `_parse_all_history_records` is called, its results are fed into the
Rich Table, and all four visible data fields make it into the rendered output.

`test_multiple_patches_numbered` PASSED
→ After two patches with different reasons, the output contains both `"1"` and `"2"` (the
`#` column) and both reason strings. This confirms the `for i, rec in enumerate(records, 1)`
loop runs correctly and that multiple rows appear in the table.

`test_rollback_record_in_table` PASSED
→ After patch + rollback (triggered via `do_rollback(skip_confirm=True)`), calling
`do_history` shows `"rollback"` in the output. This proves `_parse_all_history_records`
does not skip rollback records at the integration level — not just at the unit test level
in Class 1. It also confirms the end-to-end pipeline: `_patch_skill` writes a history
record → `do_rollback` appends a rollback record → `do_history` shows both.

`test_shows_record_count` PASSED
→ After two patches, output contains `"2 record(s)"`. The summary line is appended after
the table. This is the primary affordance for users to know how many records exist before
deciding whether to use `--detail`.

`test_hint_about_detail_flag` PASSED
→ Output contains `"--detail"`. The hint text exists so users can discover the detail view
from the default output without reading the help text. If this hint were removed, this
test would fail — it pins the discovery UX.

---

### Class 3 — `TestDoHistoryDetailView` (4 tests, all PASSED)

**What this class tests.** The `--detail N` output path: the diff view for a specific
record.

**What the passing tests prove.**

`test_detail_shows_diff` PASSED
→ `do_history("my-skill", detail=1)` with a single patch in history produces output
containing `"Record #1"`, `"patch"`, and the new content of the patch. This confirms:
1. `records[detail - 1]` correctly selects record #1 (0-indexed access).
2. The record header (timestamp, reason, file) is printed.
3. `difflib.unified_diff` runs on `old_text` vs `new_text` and produces non-empty output.
4. The diff content (at minimum the `+` line with `"Do the new thing"`) is in the captured output.

`test_detail_out_of_range` PASSED
→ `detail=99` with a 1-record history produces output containing `"does not exist"` and
`"99"`. The `if detail < 1 or detail > len(records)` guard fires before any record access.
The error message also contains the valid range (`"1"` in this case), which is rendered
as `"1–1"` (using the en-dash U+2013). The function returns without raising.

`test_detail_zero_is_invalid` PASSED
→ `detail=0` triggers the same guard (records are 1-indexed; 0 is invalid). The output
contains `"does not exist"`. This is a boundary test: the guard uses `detail < 1`, so 0
is correctly rejected. If the guard used `detail <= 0` instead, 0 would also be rejected
— but the test would still pass. The difference matters only for negative values, which
this test does not cover (negative integers would need a separate test).

`test_detail_identity_diff` PASSED
→ When `old_text == new_text`, `difflib.unified_diff` returns an empty iterator. The
`if diff_lines:` branch is not taken. The output contains `"No visible diff"`. This
matches the identical test in `test_rollback.py` for the rollback preview. The scenario
arises when a skill is "patched" with identical content — not a common operation but a
valid edge case if someone is testing the history write path without changing anything.

---

### Class 4 — `TestDoHistoryErrorPaths` (3 tests, all PASSED)

**What this class tests.** Every early-return branch in `do_history` before the table or
diff is rendered.

**What the passing tests prove.**

`test_unknown_skill_prints_error` PASSED
→ `_find_skill("nonexistent-skill")` returns `None` inside the empty temp dir. Output
contains `"not found"` (case-insensitive). No exception is raised. This is the most
common user error: a typo in the skill name. `do_history` handles it identically to
`do_rollback` — print and return.

`test_no_history_file_prints_warning` PASSED
→ Skill exists but has never been patched or edited. `SKILL_HISTORY.md` does not exist.
Output contains `"No history found"`. The early return fires at
`if not history_path.exists()`. The skill directory and `SKILL.md` are untouched.

`test_empty_history_file_prints_warning` PASSED
→ `SKILL_HISTORY.md` exists but contains only `""`. `_parse_all_history_records("")`
returns `[]`. The early return fires at `if not records`. Output contains
`"No parseable records"`. This distinguishes between "history file absent" (user has
never patched the skill) and "history file present but unparseable" (file was truncated,
manually cleared, or written by a different tool). The two cases have different messages.

**Collectively, these 3 tests prove every early-exit path is covered.** No error
condition falls through to the table-rendering or diff-rendering code. `do_history` never
prints a partial table or raises an unhandled exception on error input.

---

### Class 5 — `TestHistoryCommandDispatch` (2 tests, all PASSED)

**What this class tests.** `skills_command()` routing: that `skills_action="history"`
reaches `do_history` with the correct arguments.

**What the passing tests prove.**

`test_skills_command_routes_history` PASSED
→ `Namespace(skills_action="history", name="test-skill", detail=None)` causes
`do_history("test-skill", detail=None)` to be called. Two things confirmed: (1) the
`elif action == "history":` branch executes, and (2) `detail=None` is passed through
via `getattr(args, "detail", None)`. The spy uses `assert_called_once_with("test-skill", detail=None)`.

`test_skills_command_routes_history_with_detail` PASSED
→ `detail=3` in the Namespace → `do_history("test-skill", detail=3)` is called. Confirms
the `detail` attribute is not hardcoded or dropped; it is read from the Namespace and
forwarded as-is.

**Why these tests are separate from the argparse tests.** The dispatch tests construct
`Namespace` objects directly, bypassing argparse. If the `elif action == "history":` branch
were removed from `skills_command` but the argparse registration still existed, the
argparse tests would pass (they only check what the parser produces) but these tests would
fail (they check what `skills_command` does with the parsed result). The two layers are
independently vulnerable to different kinds of bugs.

---

### Class 6 — `TestHistoryArgparse` (4 tests, all PASSED)

**What this class tests.** The argparse subparser registration in `hermes_cli/main.py`.
These tests call the real `main()` with patched `sys.argv` — not a parser replica.

**The `_run_skills_cmd` helper.** Identical to `TestRollbackArgparse._run_skills_cmd` in
`test_rollback.py`. Patches `sys.argv` and `hermes_cli.skills_hub.skills_command`, calls
`main()`, returns the captured `vars(args)` dict. The `skills_command` spy is reachable
because `cmd_skills` (defined inside `main()`) does a fresh
`from hermes_cli.skills_hub import skills_command` at call time, resolving the name from
the module object where the patch lives.

**What the passing tests prove.**

`test_history_subparser_registered` PASSED
→ `["skills", "history", "my-skill"]` successfully parses to:
- `skills_action="history"`
- `name="my-skill"`
- `detail=None` (default)

All three confirm: the `"history"` subcommand is registered under the `skills` subparser,
the `name` positional is defined and populated, and `detail` defaults to `None` when
`--detail` is not passed.

`test_history_detail_flag` PASSED
→ Adding `"--detail", "3"` to the argv sets `detail=3` (an `int`, not a string). This
confirms `type=int` is correctly specified in `add_argument("--detail", type=int, ...)`.
If `type=int` were omitted, argparse would produce `detail="3"` (string), and this test
would fail the `result["detail"] == 3` assertion (strict equality with int 3).

`test_history_missing_name_errors` PASSED
→ `["skills", "history"]` — no `name` argument — causes `SystemExit(2)`. This confirms
`name` is a required positional argument (not `nargs="?"` or optional). Exit code 2 is
argparse's standard "argument error" code.

`test_history_detail_requires_int` PASSED
→ `["skills", "history", "my-skill", "--detail", "abc"]` causes `SystemExit(2)`.
Argparse `type=int` calls `int("abc")` which raises `ValueError`, which argparse converts
to a usage error and `SystemExit(2)`. This confirms that the `type=int` annotation does
input validation for free — no manual int-parsing is needed in `do_history`.

---

## 5. What All 23 Tests Together Prove

**The history view is end-to-end correct for its documented use cases.** A skill patched
once shows a single-row table with the correct timestamp, action, reason, and file. A
skill patched multiple times shows all rows numbered oldest-to-newest. A skill that was
rolled back shows the rollback action in the table. The full code path from `sys.argv`
through argparse, routing, file I/O, parsing, and Rich rendering is exercised.

**`_parse_all_history_records` is correctly distinct from `_parse_last_history_record`.**
The test `test_rollback_record_included` pins the most important design difference: the
history view returns all records including rollbacks. A future refactoring that extracted
a shared parser and accidentally added the rollback-skip logic would fail this test.

**The `--detail N` feature is wired end-to-end.** The `type=int` annotation on the
argparse argument, the 1-indexed record selection in `do_history`, and the boundary
checks (`detail < 1 or detail > len(records)`) are each independently verified.

**All three early-exit paths have distinct messages.** Unknown skill → "not found".
Missing history file → "No history found". Empty/unparseable history → "No parseable
records". Each message is pinned by its own test, preventing the three conditions from
being conflated into a single generic error.

**The parser correctly reports `type=int` validation.** `test_history_detail_requires_int`
confirms that non-integer `--detail` values produce `SystemExit(2)` from argparse — no
special handling in `do_history` needed. This is the correct place to validate the type.

---

## 6. What the 23 Tests Do Not Prove

**1. Rich Table column widths and wrapping.**
The Rich Table renders into a `StringIO` sink with `force_terminal=False`. Column width
calculations depend on terminal width — in a real terminal, long reasons or timestamps
may be truncated or wrapped differently. The tests assert on substring presence
(`"patch" in output`), not on exact column alignment. A Rich layout bug would not be
caught.

**2. Action colour coding.**
`do_history` colour-codes actions: green for `patch`, blue for `edit`, yellow for
`rollback`. The `_capture_console` helper uses `color_system=None`, which strips all ANSI
codes. The tests cannot verify that `patch` is green in a real terminal. Only that the
word "patch" appears in the output.

**3. The `edit` action in the table.**
`test_rollback_record_in_table` verifies "rollback" appears. No test uses `_edit_skill`
to generate an `edit` record and verifies it appears with `action="edit"` and the correct
blue colour. The logic is identical to `patch` (the action string is inserted verbatim),
but it is not directly tested in the table view.

**4. Reason truncation at 60 characters.**
`do_history` truncates reasons longer than 60 characters with an ellipsis (`…`). No test
provides a reason longer than 60 characters to verify this truncation. A bug in the slice
(`rec["reason"][:60]`) or the ellipsis condition (`if len(...) > 60`) would not be caught.

**5. Diff line cap at 60 lines.**
The `--detail` diff view caps at 60 lines with a "... and N more lines" message. No test
patches a skill with enough content to produce a 61+ line diff. This is the same gap as
in `test_rollback.py`.

**6. `hermes skills history` invoked from within an active agent session.**
If the history file is being written by `_patch_skill` while `do_history` is reading it
(two concurrent processes), the read may see a partial file. `do_history` is read-only and
does not acquire the file lock. In practice this is a very narrow race, but it is not
tested.

**7. History file with hundreds of records.**
`_parse_all_history_records` reads the entire file and does a full regex split. On a
skill with 500 patches, `SKILL_HISTORY.md` may be several megabytes. No performance test
exists.

**8. `--detail N` with `N` as a large negative integer.**
The guard `detail < 1` correctly catches `detail=0` and `detail=-100`. But no test
explicitly uses a large negative value — `test_detail_zero_is_invalid` only tests 0.
The logic handles it correctly, but it is not verified.

---

## 7. How to Reproduce the Run

```bash
cd /path/to/hermes-agent

# Just the history tests
python -m pytest tests/hermes_cli/test_history.py -v --override-ini="addopts="

# Include rollback tests (shared infrastructure validation)
python -m pytest tests/hermes_cli/test_rollback.py tests/hermes_cli/test_history.py -v --override-ini="addopts="

# Full regression — all four test files
python -m pytest \
  tests/tools/test_memory_tool.py \
  tests/tools/test_skill_manager_tool.py \
  tests/hermes_cli/test_rollback.py \
  tests/hermes_cli/test_history.py \
  -q --override-ini="addopts="
# Expected: 179 passed, 1 warning

# Full suite
python -m pytest tests/ -q --override-ini="addopts="
```

---

## 8. Source Files Referenced

| File | Role |
|------|------|
| `tests/hermes_cli/test_history.py` | This test file |
| `hermes_cli/skills_hub.py` (after line 697) | `_parse_all_history_records` — all-records parser under test in Class 1 |
| `hermes_cli/skills_hub.py` (after line 795) | `do_history` — function under test in Classes 2, 3, 4 |
| `hermes_cli/skills_hub.py:1121` | `skills_command` history dispatch — tested in Class 5 |
| `hermes_cli/main.py` (after line 4878) | Argparse subparser registration — tested in Class 6 |
| `hermes_cli/skills_hub.py:673` | `_parse_last_history_record` — the analogous function `_parse_all_history_records` is contrasted against |
| `tools/skill_manager_tool.py:297` | `_append_skill_history` — writes the records that `_parse_all_history_records` reads |
| `tools/skill_manager_tool.py:294` | `SKILL_HISTORY_FILE` — `"SKILL_HISTORY.md"` constant |
| `tools/skill_manager_tool.py:200` | `_find_skill` — skill lookup used in `do_history` |
| `tests/tools/test_skill_manager_tool.py:32` | `_skill_dir` context manager — reused by Classes 2, 3, 4 |
| `tests/hermes_cli/test_rollback.py:43` | `_capture_console` pattern — duplicated locally in `test_history.py` |
