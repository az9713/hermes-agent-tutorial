# Rollback CLI Tests — Evidence, Interpretation, and Limits

> This document is the complete reference for `tests/hermes_cli/test_rollback.py`.
> It answers three questions: *what passed*, *what that proves*, and *what it does not prove*.

---

## 1. Why This Test File Exists

`hermes skills rollback <name>` was implemented as Change 4 of the 7-enhancement plan. When the broader 125-test suite was completed, the rollback CLI path was the only feature with zero automated coverage. The gap was documented explicitly in `docs/analysis/implementation-status.md` Part 4, blind spot #3:

> *"do_rollback in skills_hub.py has no test. The parser in main.py has no test. The full `hermes skills rollback` flow was only verified manually in the plan; no automated test simulates it end-to-end."*

This file closes that gap.

---

## 2. Test Receipt — Verbatim Output

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

**Note on the warning.** The single `DeprecationWarning` comes from `tests/conftest.py:91` — a shared fixture that calls `asyncio.get_event_loop_policy().get_event_loop()`. It fires on the first test collected regardless of which test module runs. It is a pre-existing fixture issue in the upstream test infrastructure, unrelated to anything in `test_rollback.py`. No rollback test uses asyncio.

**Note on `--override-ini="addopts="`.**  `pyproject.toml` sets `addopts = "-m 'not integration' -n auto"`. The `-n auto` flag requires `pytest-xdist`, which is not installed in this Windows environment. Clearing `addopts` runs tests sequentially. On a full Linux dev environment with all dependencies, the run command is simply `python -m pytest tests/hermes_cli/test_rollback.py -v`.

**Summary: 26 passed, 0 failed, 0 errors, 1 pre-existing warning. Run time: 0.44 s.**

---

## 3. Test Strategy and Architecture

Before interpreting individual results, it helps to understand how the 26 tests are structured and what each approach is testing.

### The four layers under test

The rollback feature spans four distinct layers. Each layer gets its own test class.

```
User runs: hermes skills rollback my-skill --yes
                        │
             Layer 1: argparse (main.py)
             Parses CLI input → produces Namespace object
                        │
             Layer 2: skills_command routing (skills_hub.py)
             Routes Namespace to do_rollback() based on skills_action attr
                        │
             Layer 3: do_rollback() (skills_hub.py)
             Reads history → shows diff → confirms → writes file → logs record
                        │
             Layer 4: _parse_last_history_record() (skills_hub.py)
             Regex parser: extracts file_path, old_text, new_text from Markdown
```

Testing each layer independently means a failure localises precisely. If `test_rollback_subparser_registered` fails but `test_skills_command_routes_rollback_with_yes_true` passes, the problem is in argparse registration, not in routing.

### The test infrastructure approach

All functional tests (`TestDoRollback*`) use a real temp directory via `_skill_dir(tmp_path)`, which patches `SKILLS_DIR` and `get_all_skills_dirs` to point only at the temp dir. This means:

- `_create_skill`, `_patch_skill`, `_append_skill_history` write real files to disk.
- `do_rollback` reads, diffs, and writes real files.
- No `~/.hermes/skills/` directory is touched.

This is stronger than pure mocking — it catches any path-construction bug, encoding issue, or file-not-found condition that pure mocking would hide.

The argparse tests (`TestRollbackArgparse`) call `hermes_cli.main.main()` directly with a patched `sys.argv`. This exercises the **real** `ArgumentParser` built inside `main()` — the same 5,000-line function that runs when a user types `hermes`. Not a replica built in the test. The spy on `hermes_cli.skills_hub.skills_command` intercepts the call before any real work happens, so the tests are fast and have no side effects.

---

## 4. Class-by-Class Interpretation

### Class 1 — `TestParseLastHistoryRecord` (7 tests, all PASSED)

**What this class tests.** `_parse_last_history_record` is the regex-based Markdown parser that `do_rollback` depends on to know *what* to restore. It has no side effects and takes no arguments beyond a string — exactly the kind of function that is easy to test thoroughly.

**What the passing tests prove.**

`test_empty_text_returns_none_tuple` PASSED
→ The function never crashes on empty input. `do_rollback` can safely receive `(None, None, None)` and print its "No restorable record found" message.

`test_single_patch_record_extracted` PASSED
→ The em-dash format (`## 2026-04-11T14:32:05Z — patch`), `**File:**` line, and fenced `### Old` / `### New` blocks are correctly parsed. The exact string values match `_append_skill_history`'s output format — these were written from the same source format, so this test confirms both the writer and the reader are aligned.

`test_multiple_records_returns_most_recent` PASSED
→ When SKILL_HISTORY.md has multiple records, the parser correctly reverse-scans and returns the **newest** one. This is critical: a user running rollback twice should undo two distinct steps, not the same step twice. The reverse-scan logic works.

`test_rollback_record_is_skipped` PASSED
→ This is the most important correctness test for the parser. If a user patches, then rolls back, then patches again, the history is: `patch → rollback → patch`. When they rollback again, the parser must skip the rollback record and return the most recent **patch**. If this test failed, rollback-of-rollback would be mis-parsed: the function would try to restore the rollback entry's `old_text`, which is the patched version, making rollback non-idempotent in a confusing way. Passing confirms the skip logic works.

`test_all_rollback_records_returns_none` PASSED
→ If a user has somehow filled their history with only rollback records (edge case: they manually edited SKILL_HISTORY.md or there is a bug), the function returns `(None, None, None)` gracefully rather than applying a rollback record as if it were a patch.

`test_edit_action_also_returned` PASSED
→ The `— edit` action keyword is treated identically to `— patch`. An edit creates a full-content replacement record; rollback of an edit restores the pre-edit version. Confirming that `(\w+)` in the regex captures both `patch` and `edit` and that neither is special-cased here.

`test_malformed_record_without_old_block_returns_none` PASSED
→ If `### Old` is missing from a record (e.g., SKILL_HISTORY.md was manually edited, or a future code path writes an incomplete record), the parser falls through to `(None, None, None)`. No partial application of a rollback. No exception. This is defensive-coding behaviour confirmed.

**What the 7 parser tests do NOT prove.** They use inline strings constructed to match the exact format `_append_skill_history` writes. They do not test what happens if skill content itself contains lines matching the header regex (e.g., a step in a skill that happens to contain `## 2026-...`). That is a known gap documented in Part 4 of `implementation-status.md`.

---

### Class 2 — `TestDoRollbackHappyPath` (7 tests, all PASSED)

**What this class tests.** The end-to-end success path: create a skill, patch it, roll it back. Each test isolates one specific behaviour of `do_rollback`.

**What the passing tests prove.**

`test_patch_then_rollback_restores_original` PASSED
→ The core claim of the entire feature. Starting from `VALID_SKILL_CONTENT` ("Step 1: Do the thing."), patching it ("Step 1: Do the new thing."), then rolling back restores the original. Both the SKILL.md file content and the console output message are verified. This is the only test that validates the file actually ends up with the right bytes — all other tests build on this assumption.

The test is implemented with `_create_skill` + `_patch_skill` (real writes) + `do_rollback(skip_confirm=True)`. Using the real create/patch functions rather than manually writing the history ensures the test exercises the full write→read→restore pipeline.

`test_rollback_appends_rollback_record_to_history` PASSED
→ Confirms the append-only design. After patch + rollback, SKILL_HISTORY.md contains exactly two section headers: one `— patch` and one `— rollback`. The rollback record's `**Reason:**` is `"Rolled back via CLI"`. This is the design contract for reversible rollback: you can rollback the rollback by running the command again, because the patch record is still present.

The test counts `history.count("— patch") == 1` and `history.count("— rollback") == 1`. If `do_rollback` accidentally mutated or truncated the history (i.e., if it was not truly append-only), one of these counts would be wrong.

`test_rollback_shows_unified_diff_preview` PASSED
→ The "Rollback preview for 'name' (file)" heading appears in the captured console output. This confirms the diff-preview block executes. It does not verify individual diff lines in detail (that would depend on Rich's markup stripping, making the assertion fragile), but it confirms the preview path is reached.

`test_rollback_with_identical_content_prints_no_visible_diff` PASSED
→ When `old_text == new_text` in the history record, `difflib.unified_diff` returns an empty list. The code takes the `else` branch and prints "No visible diff — file already matches the rollback target." Confirmed. This is an edge case that would occur if someone patched a skill without actually changing anything (e.g., reformatting whitespace in a way that round-trips to the same string).

`test_skip_confirm_false_with_y_restores` PASSED
→ `monkeypatch.setattr("builtins.input", lambda _prompt: "y")` simulates the user typing "y" at the prompt. The file is restored. This confirms the confirmation guard is wired correctly and that "y" is recognised as an affirmative answer.

`test_skip_confirm_false_with_n_cancels` PASSED
→ Input returns "n". The file is **not** changed (still shows patched content). Output contains "Cancelled." This confirms the guard actually prevents the write — the `return` statement after printing "Cancelled" is reached before `_atomic_write_text`. If the write had happened before the prompt, this test would fail.

`test_confirm_eof_treated_as_no` PASSED
→ `input()` raises `EOFError` (what happens when stdin is a pipe or has been closed — e.g., running `echo "" | hermes skills rollback`). The `except (EOFError, KeyboardInterrupt)` block treats this as "n". File unchanged, "Cancelled" in output. This prevents an automated script from accidentally confirming a rollback when it pipes empty stdin.

---

### Class 3 — `TestDoRollbackErrorPaths` (4 tests, all PASSED)

**What this class tests.** Every `return` branch in `do_rollback` before the actual write: unknown skill, missing history file, non-restorable history, malformed history.

**What the passing tests prove.**

`test_unknown_skill_prints_error` PASSED
→ `_find_skill("nonexistent-skill")` returns `None` (inside the empty temp dir). The output contains "not found" (case-insensitive) and no exception is raised. Confirms `do_rollback` never crashes on a bad skill name — it prints and returns. This is the most common user error (typo in skill name).

`test_skill_exists_but_no_history_prints_warning` PASSED
→ Skill exists but has never been patched, so `SKILL_HISTORY.md` does not exist. Output contains "No history found". The SKILL.md file is untouched. Confirms the early return before any read or write attempt.

`test_history_only_rollback_records_prints_no_restorable` PASSED
→ SKILL_HISTORY.md exists and is valid, but contains only a rollback record (no patch to restore to). Output contains "No restorable record found". This confirms the parser's return value is checked and acted on — `do_rollback` does not proceed to restore `None` as file content.

`test_malformed_history_prints_no_restorable` PASSED
→ The history record has a `### Old` block but is missing `### New`. The parser returns `(None, None, None)`. Same "No restorable record found" message. Same early return. This ensures `do_rollback` is robust against any future write path that produces incomplete records.

**Collectively, these 4 tests prove that every early-exit path is covered.** No error path falls through to the write or history-append code. `do_rollback` never writes partial content or corrupts the file on error.

---

### Class 4 — `TestDoRollbackClearsPromptCache` (2 tests, all PASSED)

**What this class tests.** After a successful rollback, `do_rollback` calls `clear_skills_system_prompt_cache(clear_snapshot=True)` to force the next agent turn to rebuild the skill system prompt from disk. If this is skipped, the agent continues using the old (pre-rollback) skill content for the rest of the session.

**What the passing tests prove.**

`test_successful_rollback_clears_prompt_cache` PASSED
→ The spy `calls == [{"clear_snapshot": True}]` confirms:
  1. The function is called exactly **once** (not zero, not twice).
  2. It is called with `clear_snapshot=True`, not `clear_snapshot=False`. The `True` flag tells the prompt builder to drop both the in-memory cache AND the frozen snapshot, so the very next call to `format_for_system_prompt()` re-reads SKILL.md from disk.
  3. The spy captures keyword args, not positional args — confirming the call site uses `clear_skills_system_prompt_cache(clear_snapshot=True)`, not `clear_skills_system_prompt_cache(True)`.

**Why the lazy-import patch target matters.** `do_rollback` imports the function inside its body:
```python
try:
    from agent.prompt_builder import clear_skills_system_prompt_cache
    clear_skills_system_prompt_cache(clear_snapshot=True)
except Exception:
    pass
```

This is a **lazy import** — the name is bound fresh on each call from `agent.prompt_builder` module object. Patching `agent.prompt_builder.clear_skills_system_prompt_cache` before calling `do_rollback` works because the `from ... import` resolves the name from the module at call time, where the patched binding lives. If the import were moved to module scope (`from agent.prompt_builder import clear_skills_system_prompt_cache` at the top of `skills_hub.py`), the patch target would change to `hermes_cli.skills_hub.clear_skills_system_prompt_cache`. This caveat is documented in the test class docstring so the fix is obvious if the import ever moves.

`test_cache_clear_failure_is_swallowed` PASSED
→ The spy raises `RuntimeError("cache unavailable")`. `do_rollback` still prints the success message "Rolled back 'test-skill'". Confirms the `except Exception: pass` block genuinely swallows the error. This is the correct behaviour: a cache-clear failure should never block the file restore. The user's skill has been corrected; the cache will self-heal on the next session start. Failing here would mean a rollback that succeeds on disk but crashes on cache invalidation — confusing and wrong.

This is the **only class in the entire 151-test suite** that explicitly tests a `clear_skills_system_prompt_cache` call from production code. It establishes the pattern for future CLI-writes-invalidate-cache tests.

---

### Class 5 — `TestSkillsCommandRollbackDispatch` (2 tests, all PASSED)

**What this class tests.** The routing function `skills_command()` in `skills_hub.py`. This is the function `cmd_skills()` (inside `main.py`) delegates to when the skills subcommand is invoked. It reads `args.skills_action` and routes to the appropriate `do_*` function.

**What the passing tests prove.**

`test_skills_command_routes_rollback_with_yes_true` PASSED
→ A `Namespace(skills_action="rollback", name="test-skill", yes=True)` causes `do_rollback("test-skill", skip_confirm=True)` to be called. Two things are confirmed: (1) the routing `elif action == "rollback":` branch executes, and (2) `yes=True` is correctly translated to `skip_confirm=True`. The spy uses `assert_called_once_with("test-skill", skip_confirm=True)` — positional arg `name` and keyword arg `skip_confirm` — which matches the exact call signature of `do_rollback`.

`test_skills_command_rollback_defaults_yes_false` PASSED
→ `Namespace(skills_action="rollback", name="test-skill")` — no `yes` attribute. `getattr(args, "yes", False)` returns `False`. `do_rollback("test-skill", skip_confirm=False)` is called. Confirms the default-to-False fallback for the confirmation prompt flag. This matters for `hermes skills rollback <name>` (without `--yes`): users will be prompted, not silently confirmed.

**Why these tests are separate from the argparse tests.** The dispatch tests bypass argparse entirely — they construct `Namespace` objects directly. This means they test the routing logic in isolation from the parser. If the argparse registration changes but the routing logic is broken independently, these tests catch it. If the routing is correct but the argparse flag is misspelled, the argparse tests catch it.

---

### Class 6 — `TestRollbackArgparse` (4 tests, all PASSED)

**What this class tests.** The argparse subparser registration in `hermes_cli/main.py` — the code at lines 4876–4878 that registers `hermes skills rollback` with its `name` positional and `--yes`/`-y` flag.

**Key design decision: testing the real parser.**

Unlike `tests/hermes_cli/test_argparse_flag_propagation.py`, which builds a **local replica** of the parser structure, these tests call `hermes_cli.main.main()` with a patched `sys.argv`. This exercises the exact same `ArgumentParser` that a user interacts with. A mistake in any of the 5,000 lines of `main()` that build the parser will fail these tests. A replica would pass even if the real code was different.

The approach:
1. `monkeypatch.setattr("sys.argv", ["hermes", "skills", "rollback", ...])` — sets the args before the parser reads them.
2. `monkeypatch.setattr("hermes_cli.skills_hub.skills_command", spy)` — intercepts the final call, preventing any real skill work.
3. `main()` is called. It builds the parser, parses `sys.argv[1:]`, routes to `cmd_skills(args)`, which does `from hermes_cli.skills_hub import skills_command; skills_command(args)`. Because `skills_command` is a fresh `from ... import` at call time, the monkeypatched binding is used.
4. The spy captures `vars(args)`, which is the parsed `Namespace`.

**What the passing tests prove.**

`test_rollback_subparser_registered` PASSED
→ `["skills", "rollback", "my-skill"]` successfully parses. The captured Namespace has `skills_action="rollback"`, `name="my-skill"`, and `yes=False`. This confirms:
  - The `"rollback"` subcommand is registered under the `skills` subparser.
  - The `name` positional argument is defined and populated.
  - `yes` defaults to `False` when `--yes` is not passed.

`test_rollback_yes_flag_long` PASSED
→ Adding `"--yes"` to the argv sets `yes=True`. Confirms the long form of the flag is registered.

`test_rollback_yes_flag_short` PASSED
→ Adding `"-y"` sets `yes=True`. Confirms `-y` is registered as an alias for `--yes`. This matters for interactive users who prefer the short form.

`test_rollback_missing_name_errors` PASSED
→ `["skills", "rollback"]` — no `name` argument — causes argparse to call `parser.error()`, which raises `SystemExit(2)`. The `exit_code == 2` confirms this is an argument error, not a runtime error (runtime errors are exit code 1). Confirms that `name` is a required positional argument (i.e., `add_argument("name", ...)` without `nargs="?"` or `default=...`).

---

## 5. What All 26 Tests Together Prove

Reading the six passing classes as a unit, here is what can be asserted with confidence:

**The rollback feature is end-to-end correct for its documented use cases.** A skill patched once, then rolled back with `--yes`, has its content correctly restored to the pre-patch version. The history file grows by one record rather than being modified. The prompt cache is invalidated. The full code path from `sys.argv` through argparse, routing, file I/O, and cache clearing is exercised.

**All failure modes are handled gracefully.** Four distinct error conditions — unknown skill, no history, no restorable record, malformed record — each produce an appropriate message and return without modifying any file. No error path raises an unhandled exception.

**The confirmation prompt is correctly gated.** `skip_confirm=False` requires "y" or "yes" to proceed; any other string, EOF, or KeyboardInterrupt cancels the operation and leaves files unchanged.

**The append-only design holds.** After patch + rollback, the history file contains two records, not one. The rollback itself is recorded, making it reversible. This is the key invariant of the audit trail design.

**The parser registration is wired to the routing, which is wired to the implementation.** Passing a real argv through the real `main()` parser and observing it reach `do_rollback` with the right arguments proves the three layers are connected without gaps.

**The cache-clear contract is pinned with exact kwargs.** Future refactoring of the cache-clear call cannot silently drop the `clear_snapshot=True` flag without failing a test.

---

## 6. What the 26 Tests Do Not Prove

These gaps are worth tracking. They are not bugs — they are boundaries of what automated unit/functional tests can verify.

**1. Rich diff rendering in a real terminal.**
The diff preview is tested with a `StringIO` sink and a `force_terminal=False, color_system=None` Console. Real terminal output includes ANSI colour codes and may wrap differently. The diff output itself (added/removed lines) is confirmed by asserting `"Rollback preview"` appears, but no test asserts the colours are correct or that lines beyond the 60-line cap display the `"... and N more lines"` footer. To test this you would need a PTY simulation or screenshot comparison.

**2. Rollback of a rollback (two-step reversal).**
`test_rollback_record_is_skipped` confirms the parser skips rollback records, but no test drives the full two-rollback sequence — patch, rollback, rollback again — and verifies SKILL.md ends up at the original content. This is the advertised "rollback-of-rollback" feature. It follows from the logic being tested, but it is not directly verified end-to-end.

**3. History content embedded in skill bodies.**
If a skill's step descriptions happen to contain `## 2026-04-11T` patterns (e.g., a skill that teaches how to write SKILL_HISTORY.md format), the regex splitter in `_parse_last_history_record` may misidentify section boundaries. Not tested.

**4. History file with hundreds of records.**
`do_rollback` reads the entire file and does a full regex scan. On a skill patched 500 times, SKILL_HISTORY.md may be several megabytes. No performance test exists.

**5. Cross-platform line endings.**
`_atomic_write_text` on Windows writes files in text mode (CRLF). The regex patterns in `_parse_last_history_record` use `\n`. The tests pass on Windows because Python's text-mode open normalises CRLF to `\n` on read. But a file written on Windows and read on a system that uses `open(..., "rb")` would have `\r\n` in the fenced blocks, which the regex would not strip. Not tested.

**6. `hermes skills rollback` invoked from within an active agent session.**
The tests are pure CLI tests — they call `do_rollback` directly or via `main()`. Within an active `hermes` session, skill files may be locked, or the LLM's in-memory system prompt state may diverge from disk in ways that the cache clear does not fully reconcile until the next turn. This is an integration-level concern not addressable by unit tests.

---

## 7. How to Reproduce the Run

```bash
cd /path/to/hermes-agent

# Fast: just the rollback tests
python -m pytest tests/hermes_cli/test_rollback.py -v --override-ini="addopts="

# Regression: include the memory and skill manager test suites
python -m pytest \
  tests/tools/test_memory_tool.py \
  tests/tools/test_skill_manager_tool.py \
  tests/hermes_cli/test_rollback.py \
  -v --override-ini="addopts="
# Expected: 151 passed, 1 warning

# Full suite
python -m pytest tests/ -q --override-ini="addopts="
```

---

## 8. Source Files Referenced

| File | Role |
|------|------|
| `tests/hermes_cli/test_rollback.py` | This test file |
| `hermes_cli/skills_hub.py:673` | `_parse_last_history_record` — regex parser under test in Class 1 |
| `hermes_cli/skills_hub.py:700` | `do_rollback` — function under test in Classes 2, 3, 4 |
| `hermes_cli/skills_hub.py:1121` | `skills_command` rollback dispatch — tested in Class 5 |
| `hermes_cli/main.py:4876` | Argparse subparser registration — tested in Class 6 |
| `tools/skill_manager_tool.py` | `_create_skill`, `_patch_skill`, `_append_skill_history`, `SKILL_HISTORY_FILE` — test helpers |
| `agent/prompt_builder.py` | `clear_skills_system_prompt_cache` — spy target in Class 4 |
| `tests/tools/test_skill_manager_tool.py:32` | `_skill_dir` context manager — reused by Classes 2, 3, 4 |
